"""Turn the unified index into selected cases and native per-group subsets."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .io import canonical_json, read_jsonl, sha256_text, write_jsonl
from .model import CaseRef, Selection
from .paths import RunPaths, resolve_path
from .registry import Manifest


@dataclass
class SelectionFilters:
    ids: list[str] = field(default_factory=list)
    source: str | None = None
    representation: str | None = None
    task_type: str | None = None
    usage_license: str | None = None
    runnable: bool | None = None
    allow_local_build: bool = False
    where: list[tuple[str, str]] = field(default_factory=list)
    limit: int | None = None
    shard: tuple[int, int] | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "ids": list(self.ids),
            "source": self.source,
            "representation": self.representation,
            "task_type": self.task_type,
            "usage_license": self.usage_license,
            "runnable": self.runnable,
            "allow_local_build": self.allow_local_build,
            "where": [list(item) for item in self.where],
            "limit": self.limit,
            "shard": list(self.shard) if self.shard else None,
        }


def catalog_path(root: Path, manifest: Manifest) -> Path:
    value = manifest.section("catalog").get("path")
    if not value:
        raise ValueError(f"{manifest.path}: catalog.path is required")
    return resolve_path(root, value)


def load_catalog(root: Path, manifest: Manifest) -> list[dict[str, Any]]:
    """Load the manifest's slice of the shared catalog.

    A manifest may declare ``select`` (field -> value) to pin itself to a
    source in a fused index.  Without it, every catalog row belongs to the
    dataset.
    """

    path = catalog_path(root, manifest)
    if not path.is_file():
        raise ValueError(f"catalog not found: {path}")
    rows = read_jsonl(path)
    constraints = manifest.section("select")
    if constraints:
        rows = [
            row
            for row in rows
            if all(row.get(key) == value for key, value in constraints.items())
        ]
    return rows


def select_cases(
    root: Path,
    manifest: Manifest,
    filters: SelectionFilters,
) -> Selection:
    rows = load_catalog(root, manifest)
    catalog = manifest.section("catalog")
    id_field = str(catalog.get("id_field") or "hqbd_id")

    runnable_filter = filters.runnable
    if runnable_filter is None:
        runnable_filter = bool(manifest.section("defaults").get("runnable", True))

    wanted_ids = set(filters.ids)
    selected: list[dict[str, Any]] = []
    for row in rows:
        case_id = str(row.get(id_field) or "")
        if not case_id:
            raise ValueError(f"{manifest.path}: catalog row without {id_field}")
        if wanted_ids and case_id not in wanted_ids:
            continue
        if filters.source and row.get("source") != filters.source:
            continue
        if filters.representation and row.get("representation") != filters.representation:
            continue
        if filters.task_type and row.get("task_type") != filters.task_type:
            continue
        if filters.usage_license and row.get("usage_license") != filters.usage_license:
            continue
        if runnable_filter:
            runnable = bool(row.get("runnable"))
            local = row.get("runnability_status") == "local_build_required"
            if not runnable and not (filters.allow_local_build and local):
                continue
        for key, value in filters.where:
            if str(row.get(key)) != value:
                break
        else:
            selected.append(row)

    if wanted_ids:
        found = {str(row.get(id_field)) for row in selected}
        missing = sorted(wanted_ids - found)
        if missing:
            raise ValueError("unknown case IDs: " + ", ".join(missing))

    if filters.shard:
        index, count = filters.shard
        if count < 1 or not (1 <= index <= count):
            raise ValueError("--shard must be I/N with 1 <= I <= N")
        selected = [row for offset, row in enumerate(selected) if offset % count == index - 1]

    if filters.limit is not None:
        if filters.limit < 0:
            raise ValueError("--limit must be >= 0")
        selected = selected[: filters.limit]

    cases = _resolve_native_records(root, manifest, selected)
    snapshot = filters.snapshot()
    digest = sha256_text(
        canonical_json({"ids": [case.hqbd_id for case in cases], "filters": snapshot})
    )
    return Selection(cases=cases, rows=selected, sha256=digest, filters=snapshot)


def _resolve_native_records(
    root: Path,
    manifest: Manifest,
    rows: list[dict[str, Any]],
) -> list[CaseRef]:
    catalog = manifest.section("catalog")
    id_field = str(catalog.get("id_field") or "hqbd_id")
    file_field = str(catalog.get("native_file_field") or "source_file")
    line_field = str(catalog.get("native_line_field") or "source_line")
    base_value = catalog.get("native_base") or "."
    base = resolve_path(root, str(base_value))

    cache: dict[Path, list[dict[str, Any]]] = {}
    cases: list[CaseRef] = []
    for row in rows:
        relative = row.get(file_field)
        if not relative:
            raise ValueError(f"catalog row {row.get(id_field)!r} has no {file_field}")
        native_path = (base / str(relative)).resolve()
        if native_path not in cache:
            if not native_path.is_file():
                raise ValueError(f"native file not found: {native_path}")
            cache[native_path] = read_jsonl(native_path)
        records = cache[native_path]
        line = int(row.get(line_field) or 0)
        if not (1 <= line <= len(records)):
            raise ValueError(
                f"{row.get(id_field)!r}: {line_field}={line} outside "
                f"{native_path} (1..{len(records)})"
            )
        native = records[line - 1]
        if native.get("id") != row.get(id_field):
            raise ValueError(
                f"{row.get(id_field)!r}: native record at {native_path}:{line} "
                f"has id {native.get('id')!r}"
            )
        cases.append(
            CaseRef(
                hqbd_id=str(row[id_field]),
                row=row,
                native=native,
                native_path=native_path,
            )
        )
    return cases


def materialize_inputs(
    run_paths: RunPaths,
    group_id: str,
    cases: list[CaseRef],
) -> Path:
    """Write the exact native records for one group, preserving order."""

    path = run_paths.group_input(group_id)
    write_jsonl(path, [case.native for case in cases])
    ids_path = run_paths.group_ids(group_id)
    ids_path.parent.mkdir(parents=True, exist_ok=True)
    ids_path.write_text(
        "".join(case.hqbd_id + "\n" for case in cases), encoding="utf-8", newline="\n"
    )
    return path
