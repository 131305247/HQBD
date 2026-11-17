"""Dataset validation, environment doctor, and scaffolding."""

from __future__ import annotations

import csv
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .io import read_json, read_jsonl, sha256_file, write_json
from .model import Check, PlanContext
from .paths import ensure_within, resolve_path
from .registry import Manifest

REQUIRED_FIELDS = ("source", "source_file", "source_line")


def validate_dataset(
    ctx: PlanContext,
    *,
    strict: bool = False,
    check_hashes: bool = False,
) -> list[Check]:
    manifest = ctx.manifest
    checks: list[Check] = []
    checks.append(
        Check(
            "manifest.name",
            "ok" if manifest.path.stem == manifest.name else "warn",
            f"name={manifest.name!r}, file={manifest.path.name!r}",
        )
    )

    catalog = manifest.section("catalog")
    id_field = str(catalog.get("id_field") or "hqbd_id")
    file_field = str(catalog.get("native_file_field") or "source_file")
    line_field = str(catalog.get("native_line_field") or "source_line")
    native_base = resolve_path(ctx.root, str(catalog.get("native_base") or "."))

    try:
        from .selection import load_catalog

        rows = load_catalog(ctx.root, manifest)
    except (OSError, ValueError) as exc:
        checks.append(Check("catalog.load", "error", str(exc)))
        return _finish(checks, strict)

    checks.append(Check("catalog.count", "ok", f"{len(rows)} rows"))
    ids = [str(row.get(id_field) or "") for row in rows]
    duplicates = sorted({value for value in ids if value and ids.count(value) > 1})
    checks.append(
        Check(
            "catalog.ids",
            "error" if not all(ids) or duplicates else "ok",
            "IDs present and unique"
            if all(ids) and not duplicates
            else f"empty IDs or duplicates: {duplicates[:5]}",
        )
    )

    missing_fields = [
        str(row.get(id_field))
        for row in rows
        if any(row.get(field) in (None, "") for field in REQUIRED_FIELDS)
    ]
    checks.append(
        Check(
            "catalog.fields",
            "error" if missing_fields else "ok",
            "source, source_file, source_line present"
            if not missing_fields
            else f"{len(missing_fields)} rows missing fields, e.g. {missing_fields[:3]}",
        )
    )

    pointer_errors: list[str] = []
    pointers: dict[tuple[str, int], str] = {}
    cache: dict[Path, list[dict[str, Any]]] = {}
    for row in rows:
        case_id = str(row.get(id_field) or "")
        relative = row.get(file_field)
        if not relative:
            continue
        path = (native_base / str(relative)).resolve()
        if path not in cache:
            if not path.is_file():
                pointer_errors.append(f"{case_id}: missing {path}")
                continue
            cache[path] = read_jsonl(path)
        line = int(row.get(line_field) or 0)
        if not (1 <= line <= len(cache[path])):
            pointer_errors.append(f"{case_id}: line {line} out of range in {path.name}")
            continue
        native = cache[path][line - 1]
        if native.get("id") != case_id:
            pointer_errors.append(
                f"{case_id}: native id {native.get('id')!r} at {path.name}:{line}"
            )
        pointer = (str(path), line)
        if pointer in pointers:
            pointer_errors.append(
                f"{case_id}: duplicate pointer with {pointers[pointer]}"
            )
        pointers[pointer] = case_id
    checks.append(
        Check(
            "catalog.pointers",
            "error" if pointer_errors else "ok",
            "every row resolves to its native record"
            if not pointer_errors
            else f"{len(pointer_errors)} errors, e.g. {pointer_errors[:3]}",
        )
    )

    expected = manifest.expect("records")
    checks.append(
        Check(
            "catalog.expect",
            "ok" if expected in (None, len(rows)) else "error",
            f"{len(rows)} rows" + ("" if expected is None else f", expected {expected}"),
        )
    )

    checks.extend(_mapping_check(ctx.root, manifest, ids))
    if check_hashes:
        checks.extend(_hash_check(ctx.root, manifest))

    checks.extend(ctx_manifest_path_checks(ctx))
    checks.extend(_adapter_validate(ctx))
    return _finish(checks, strict)


def ctx_manifest_path_checks(ctx: PlanContext) -> list[Check]:
    checks: list[Check] = []
    try:
        ensure_within(ctx.root, resolve_path(ctx.root, str(ctx.manifest.section("catalog").get("path"))))
        checks.append(Check("manifest.path", "ok", "catalog is inside the HQBD root"))
    except ValueError:
        checks.append(
            Check(
                "manifest.path",
                "warn",
                "catalog points outside the HQBD root; keep paths portable",
            )
        )
    root = ctx.backend_root
    checks.append(
        Check(
            "manifest.backend",
            "ok" if root.is_dir() else "warn",
            f"backend root: {root}",
        )
    )
    return checks


def _adapter_validate(ctx: PlanContext) -> list[Check]:
    from .registry import load_adapter

    adapter = load_adapter(ctx.manifest)
    try:
        return adapter.validate(ctx.manifest, ctx)
    except Exception as exc:  # defensive: adapters must not crash validation
        return [Check("adapter.validate", "error", f"{type(exc).__name__}: {exc}")]


def _native_base(root: Path, manifest: Manifest) -> Path:
    value = manifest.section("catalog").get("native_base")
    return resolve_path(root, str(value or "."))


def _mapping_check(root: Path, manifest: Manifest, ids: list[str]) -> list[Check]:
    mapping = _native_base(root, manifest) / "ID_MAPPING.csv"
    if manifest.name not in ("cvdp", "hwe") or not mapping.is_file():
        return []
    with mapping.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        mapped = {row.get("hqbd_id") for row in reader}
    missing = sorted(set(ids) - mapped)
    return [
        Check(
            "mapping.ids",
            "ok" if not missing else "error",
            f"{len(ids)} IDs covered by {mapping.name}"
            if not missing
            else f"{len(missing)} IDs absent from {mapping.name}, e.g. {missing[:3]}",
        )
    ]


def _hash_check(root: Path, manifest: Manifest) -> list[Check]:
    build_manifest = _native_base(root, manifest) / "BUILD_MANIFEST.json"
    if not build_manifest.is_file():
        return [Check("hashes.build_manifest", "warn", f"missing {build_manifest}")]
    try:
        data = read_json(build_manifest)
    except (OSError, ValueError) as exc:
        return [Check("hashes.build_manifest", "error", str(exc))]
    base = build_manifest.parent
    mismatches: list[str] = []
    for entry in data.get("outputs") or []:
        path = base / str(entry.get("path"))
        expected = entry.get("sha256")
        if not path.is_file():
            mismatches.append(f"{entry.get('path')}: missing")
        elif sha256_file(path) != expected:
            mismatches.append(f"{entry.get('path')}: hash mismatch")
    return [
        Check(
            "hashes.outputs",
            "ok" if not mismatches else "error",
            f"{len(data.get('outputs') or [])} release files match"
            if not mismatches
            else f"{len(mismatches)} mismatches, e.g. {mismatches[:3]}",
        )
    ]


def _finish(checks: list[Check], strict: bool) -> list[Check]:
    if strict:
        for item in checks:
            if item.status == "warn":
                item.status = "error"
                item.message = "[strict] " + item.message
    return checks


def doctor_dataset(
    ctx: PlanContext,
    *,
    check_images: bool = False,
) -> list[Check]:
    checks: list[Check] = []
    checks.append(
        Check(
            "backend.root",
            "ok" if ctx.backend_root.is_dir() else "error",
            str(ctx.backend_root),
        )
    )
    checks.append(_python_check(ctx))
    checks.append(_runs_check(ctx.root))

    adapter = _load_adapter(ctx.manifest)
    try:
        checks.extend(adapter.doctor(ctx.manifest, ctx, check_images=check_images))
    except Exception as exc:
        checks.append(Check("adapter.doctor", "error", f"{type(exc).__name__}: {exc}"))
    return checks


def _python_check(ctx: PlanContext) -> Check:
    argv = list(ctx.python_argv) + ["-c", "import sys; print(sys.version.split()[0])"]
    try:
        completed = subprocess.run(
            argv,
            cwd=str(ctx.backend_root) if ctx.backend_root.is_dir() else None,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return Check("backend.python", "error", f"{argv[:-2]}: {exc}")
    if completed.returncode != 0:
        message = _first_line(completed.stderr) or _first_line(completed.stdout)
        return Check(
            "backend.python",
            "error",
            f"{' '.join(argv[:-2])} failed: {message}",
        )
    return Check("backend.python", "ok", f"{' '.join(argv[:-2])} -> {completed.stdout.strip()}")


def _runs_check(root: Path) -> Check:
    runs = root / "runs"
    try:
        runs.mkdir(parents=True, exist_ok=True)
        probe = runs / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return Check("runs.writable", "error", str(exc))
    usage = shutil.disk_usage(root)
    free_gib = usage.free / (1024**3)
    return Check(
        "runs.writable",
        "ok" if free_gib >= 1 else "warn",
        f"{runs} writable, {free_gib:.1f} GiB free",
    )


def _load_adapter(manifest: Manifest) -> Any:
    from .registry import load_adapter

    return load_adapter(manifest)


def create_dataset(
    root: Path,
    name: str,
    adapter_kind: str = "generic",
    *,
    force: bool = False,
) -> Path:
    from .registry import BUILTIN_ADAPTERS, benchmark_dir

    if name in BUILTIN_ADAPTERS:
        raise ValueError(f"{name!r} collides with a built-in dataset name")
    if not name.replace("-", "_").isidentifier():
        raise ValueError("dataset name must be a Python-friendly identifier")

    directory = benchmark_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.json"
    if path.exists() and not force:
        raise ValueError(f"manifest already exists: {path}")

    if adapter_kind == "python":
        package_init = directory / "__init__.py"
        if not package_init.exists():
            package_init.write_text("", encoding="utf-8")
        plugin_dir = directory / "plugins"
        plugin_dir.mkdir(exist_ok=True)
        init = plugin_dir / "__init__.py"
        if not init.exists():
            init.write_text("", encoding="utf-8")
        plugin = plugin_dir / f"{name}.py"
        if plugin.exists() and not force:
            raise ValueError(f"plugin already exists: {plugin}")
        plugin.write_text(_plugin_skeleton(name), encoding="utf-8", newline="\n")
        adapter = f"benchmarks.plugins.{name}:{_class_name(name)}Adapter"
    elif adapter_kind in ("cvdp", "hwe", "generic"):
        adapter = adapter_kind
    else:
        raise ValueError("--adapter must be generic, cvdp, hwe, or python")

    manifest = {
        "schema_version": 1,
        "name": name,
        "description": f"TODO: describe {name}",
        "adapter": adapter,
        "catalog": {
            "path": f"datasets/{name}/{name}_index.jsonl",
            "id_field": "hqbd_id",
            "native_base": f"datasets/{name}",
            "native_file_field": "source_file",
            "native_line_field": "source_line",
        },
        "defaults": {"runnable": True},
        "backend": {"root": f"evaluators/{name}", "python": ["python"]},
        "expect": {"records": 0},
    }
    if adapter_kind == "generic":
        manifest["generic"] = {
            "group_by": ["source"],
            "stages": [
                {
                    "id": "run",
                    "commands": [
                        {
                            "argv": [
                                "{backend_root}/run_dataset.py",
                                "--dataset",
                                "{dataset}",
                                "--output",
                                "{group}/results.json",
                            ],
                            "cwd": "{backend_root}",
                        }
                    ],
                    "expected_outputs": ["{group}/results.json"],
                }
            ],
            "collect": {
                "results_path": "{group}/results.json",
                "id_field": "id",
                "status_field": "status",
                "status_map": {
                    "pass": "resolved",
                    "fail": "unresolved",
                },
            },
        }
    write_json(path, manifest)
    return path


def _class_name(name: str) -> str:
    return "".join(part.capitalize() for part in name.replace("-", "_").split("_"))


def _plugin_skeleton(name: str) -> str:
    class_name = _class_name(name) + "Adapter"
    return f'''"""Adapter skeleton for dataset {name!r}.

Implement plan() and collect(); validate_options() may stay empty.  See
hqbd_bench/adapters/cvdp.py for a complete example.
"""

from __future__ import annotations

from hqbd_bench.adapters.base import BaseAdapter


class {class_name}(BaseAdapter):
    name = "{name}"
    version = "1"
    capabilities = {{"groups": ["source"], "modes": ["agent", "llm"]}}

    def plan(self, manifest, selection, ctx):
        raise NotImplementedError("build GroupPlan objects here")

    def collect(self, manifest, outcomes, ctx):
        raise NotImplementedError("normalize backend artifacts here")
'''


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""
