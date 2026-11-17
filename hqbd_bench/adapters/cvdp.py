"""CVDP adapter: one batch invocation per native source file."""

from __future__ import annotations

import shutil
import subprocess
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..io import read_json
from ..model import (
    CaseRef,
    Check,
    CollectResult,
    Command,
    Completion,
    ExecutionOptions,
    GroupOutcome,
    GroupPlan,
    PlanContext,
    Selection,
    Stage,
)
from ..paths import slugify
from ..registry import Manifest
from .base import BaseAdapter, check

HEAVY_STATUS = "cvdp_heavy_bundle"


class CvdpAdapter(BaseAdapter):
    name = "cvdp"
    version = "1"
    capabilities = {
        "groups": ["source_file"],
        "modes": ["llm", "agent", "golden"],
        "requires_docker": True,
        "native_format": "cvdp_jsonl",
    }

    # -- validation -----------------------------------------------------
    def validate(self, manifest: Manifest, ctx: PlanContext) -> list[Check]:
        checks: list[Check] = []
        expected = manifest.expect("records")
        rows = _catalog_rows(ctx.root, manifest)
        checks.append(
            check(
                "catalog.count",
                "ok" if expected in (None, len(rows)) else "error",
                f"{len(rows)} records" + ("" if expected is None else f", expected {expected}"),
            )
        )

        expected_runnable = manifest.expect("runnable")
        runnable = sum(1 for row in rows if row.get("runnable"))
        checks.append(
            check(
                "cvdp.runnable",
                "ok" if expected_runnable in (None, runnable) else "error",
                f"{runnable} runnable"
                + ("" if expected_runnable is None else f", expected {expected_runnable}"),
            )
        )

        bad_format = [
            row.get("hqbd_id")
            for row in rows
            if len(str(row.get("hqbd_id", "")).split("_")) < 2
            or str(row.get("hqbd_id", "")).split("_")[1] not in ("copilot", "agentic")
        ]
        checks.append(
            check(
                "cvdp.id_format",
                "ok" if not bad_format else "error",
                "all IDs preserve the CVDP mode token"
                if not bad_format
                else f"{len(bad_format)} bad IDs, e.g. {bad_format[:3]}",
            )
        )

        heavy_mismatch = [
            row.get("hqbd_id")
            for row in rows
            if row.get("representation") == "cvdp_agentic_heavy"
            and row.get("runnability_status") != HEAVY_STATUS
        ]
        checks.append(
            check(
                "cvdp.heavy_status",
                "ok" if not heavy_mismatch else "error",
                "heavy cases are marked cvdp_heavy_bundle"
                if not heavy_mismatch
                else f"{len(heavy_mismatch)} mismatches",
            )
        )

        comprehension = [row for row in rows if row.get("task_type") == "code_comprehension"]
        bad_comprehension = [
            row.get("hqbd_id")
            for row in comprehension
            if row.get("runnable") or row.get("harness_available")
        ]
        checks.append(
            check(
                "cvdp.comprehension",
                "ok" if not bad_comprehension else "error",
                f"{len(comprehension)} comprehension cases without a runnable harness"
                if not bad_comprehension
                else f"{len(bad_comprehension)} flagged runnable/harness",
            )
        )

        heavy_dir = _heavy_dir(ctx)
        bundle_names = sorted(
            {
                str(case.native.get("context", {}).get("repo"))
                for case in _all_native_cases(ctx, manifest)
                if case.row.get("runnability_status") == HEAVY_STATUS
            }
        )
        missing_bundles = [
            name
            for name in bundle_names
            if not (heavy_dir / f"{name}.bundle").exists() and not (heavy_dir / name).is_dir()
        ]
        checks.append(
            check(
                "cvdp.heavy_bundles",
                "ok" if not missing_bundles else "warn",
                f"{len(bundle_names)} bundle references resolve"
                if not missing_bundles
                else f"{len(missing_bundles)} unresolved under {heavy_dir}",
            )
        )
        return checks

    def doctor(
        self, manifest: Manifest, ctx: PlanContext, check_images: bool = False
    ) -> list[Check]:
        checks: list[Check] = []
        runner = ctx.backend_root / "run_benchmark.py"
        checks.append(
            check(
                "cvdp.runner",
                "ok" if runner.is_file() else "error",
                str(runner),
            )
        )
        env_file = ctx.backend_root / ".env"
        if env_file.is_file():
            keys = _env_keys(env_file)
            checks.append(check("cvdp.env", "ok", f"{env_file} ({len(keys)} keys)"))
            checks.append(
                check(
                    "cvdp.env.sim_image",
                    "ok" if "OSS_SIM_IMAGE" in keys else "warn",
                    "OSS_SIM_IMAGE present" if "OSS_SIM_IMAGE" in keys else "OSS_SIM_IMAGE missing",
                )
            )
        else:
            checks.append(
                check("cvdp.env", "warn", f"{env_file} missing; copy .env.example")
            )

        heavy_dir = _heavy_dir(ctx)
        if heavy_dir.is_dir():
            bundles = list(heavy_dir.glob("*.bundle"))
            lfs = [path.name for path in bundles if _is_lfs_pointer(path)][:5]
            checks.append(
                check(
                    "cvdp.heavy",
                    "ok" if not lfs else "warn",
                    f"{heavy_dir} ({len(bundles)} bundles)"
                    if not lfs
                    else f"{len(lfs)}+ bundles are Git LFS pointers, e.g. {lfs}",
                )
            )
        else:
            checks.append(check("cvdp.heavy", "warn", f"{heavy_dir} missing"))

        if check_images:
            checks.append(_docker_check())
        return checks

    def validate_options(
        self, manifest: Manifest, options: ExecutionOptions
    ) -> list[str]:
        errors: list[str] = []
        mode = options.mode or "llm"
        if mode == "agent" and not options.agent:
            errors.append("--mode agent requires --agent <docker-image>")
        if mode == "llm" and not options.model:
            errors.append("--mode llm requires --model <model>")
        if mode == "golden" and not options.allow_golden:
            errors.append("--mode golden is only allowed with --allow-golden")
        if options.force_agentic and options.force_copilot:
            errors.append("--force-agentic and --force-copilot are mutually exclusive")
        if options.threads < 1:
            errors.append("--threads must be >= 1")
        return errors

    # -- planning -------------------------------------------------------
    def plan(
        self, manifest: Manifest, selection: Selection, ctx: PlanContext
    ) -> list[GroupPlan]:
        groups: "OrderedDict[str, list[CaseRef]]" = OrderedDict()
        for case in selection.cases:
            source_file = str(case.row.get("source_file") or "cvdp")
            group_id = slugify(Path(source_file).stem)
            groups.setdefault(group_id, []).append(case)

        plans: list[GroupPlan] = []
        for group_id, cases in groups.items():
            group_dir = ctx.run_paths.group_dir(group_id)
            work_dir = group_dir / "cvdp_work"
            raw_result = work_dir / "raw_result.json"
            input_path = ctx.run_paths.group_input(group_id)
            ids = [case.hqbd_id for case in cases]

            argv = list(ctx.python_argv) + [
                str(ctx.backend_root / "run_benchmark.py"),
                "-f",
                str(input_path),
                "-p",
                str(work_dir),
                "-t",
                str(ctx.options.threads),
            ]
            mode = ctx.options.mode or "llm"
            if mode == "agent":
                argv.extend(["-l", "-g", str(ctx.options.agent)])
            elif mode == "llm":
                argv.extend(["-l", "-m", str(ctx.options.model)])
            if ctx.options.force_agentic:
                argv.append("--force-agentic")
            if ctx.options.force_copilot:
                argv.append("--force-copilot")

            env: dict[str, str] = {}
            if any(case.runnability_status == HEAVY_STATUS for case in cases):
                env["CVDP_HEAVY_REPOS_PATH"] = str(_heavy_dir(ctx))

            run_stage = Stage(
                id="run",
                commands=[
                    Command(
                        argv=argv,
                        cwd=ctx.backend_root,
                        env=env,
                        timeout_s=ctx.options.timeout_s,
                    )
                ],
                expected_outputs=[raw_result],
                completion=Completion(kind="json_keys", path=raw_result, keys=ids),
                resume="skip_if_complete",
                before_run=_archive_partial(raw_result, ids),
            )
            plans.append(
                GroupPlan(
                    id=group_id,
                    cases=cases,
                    stages=[run_stage],
                    framework="cvdp",
                    input_path=input_path,
                )
            )
        return plans

    # -- collection -----------------------------------------------------
    def collect(
        self, manifest: Manifest, outcomes: list[GroupOutcome], ctx: PlanContext
    ) -> CollectResult:
        results: list[dict[str, Any]] = []
        unmatched: list[dict[str, Any]] = []
        for outcome in outcomes:
            raw_path = outcome.group_dir / "cvdp_work" / "raw_result.json"
            raw: dict[str, Any] = {}
            if raw_path.is_file():
                try:
                    loaded = read_json(raw_path)
                    if isinstance(loaded, dict):
                        raw = loaded
                except (OSError, ValueError):
                    raw = {}
            selected_ids = {case.hqbd_id for case in outcome.cases}
            for extra in sorted(set(raw) - selected_ids):
                unmatched.append({"group_id": outcome.group_id, "id": extra})
            for case in outcome.cases:
                results.append(
                    _normalize_case(case, raw.get(case.hqbd_id), outcome, ctx)
                )
        return CollectResult(results=results, unmatched=unmatched)


def _catalog_rows(root: Path, manifest: Manifest) -> list[dict[str, Any]]:
    from ..selection import load_catalog

    return load_catalog(root, manifest)


def _all_native_cases(ctx: PlanContext, manifest: Manifest) -> list[CaseRef]:
    from ..selection import SelectionFilters, select_cases

    return select_cases(ctx.root, manifest, SelectionFilters(runnable=False)).cases


def _heavy_dir(ctx: PlanContext) -> Path:
    value = ctx.manifest.section("backend").get("heavy_repos_path")
    if not value:
        return (
            ctx.root
            / "datasets"
            / "hqbd_v0.0.1"
            / "hqbd_v0.0.1_agentic_heavy_code_generation_public"
        )
    from ..paths import resolve_path

    return resolve_path(ctx.root, str(value))


def _archive_partial(raw_path: Path, ids: list[str]):
    def before_run() -> None:
        if not raw_path.is_file():
            return
        try:
            data = read_json(raw_path)
        except (OSError, ValueError):
            data = None
        if isinstance(data, dict) and set(data) == set(ids):
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = raw_path.with_name(
            f"{raw_path.stem}.partial.{stamp}{raw_path.suffix}"
        )
        raw_path.replace(destination)

    return before_run


def _normalize_case(
    case: CaseRef,
    raw: Any,
    outcome: GroupOutcome,
    ctx: PlanContext,
) -> dict[str, Any]:
    artifacts = {
        "workdir": ctx.run_paths.relative(outcome.group_dir / "cvdp_work"),
        "log": ctx.run_paths.relative(ctx.run_paths.group_log(outcome.group_id, "run")),
    }
    report_path = outcome.group_dir / "cvdp_work" / "report.json"
    if report_path.is_file():
        artifacts["report"] = ctx.run_paths.relative(report_path)

    base = {
        "schema_version": "1.0",
        "run_id": ctx.run_id,
        "hqbd_id": case.hqbd_id,
        "source": case.source,
        "native_id": case.native_id,
        "representation": case.representation,
        "task_type": case.task_type,
        "group_id": outcome.group_id,
        "status": "missing",
        "resolved": False,
        "score": None,
        "tests_passed": 0,
        "tests_failed": 0,
        "tests_total": 0,
        "error_category": "no_result",
        "error_message": "case absent from raw_result.json",
        "backend": {"framework": "cvdp", "image": None},
        "artifacts": artifacts,
    }
    if not isinstance(raw, dict):
        return base

    tests = raw.get("tests")
    tests = tests if isinstance(tests, list) else []
    passed = sum(1 for item in tests if isinstance(item, dict) and item.get("result") == 0)
    failed = sum(
        1 for item in tests if not isinstance(item, dict) or item.get("result") != 0
    )
    base["tests_passed"] = passed
    base["tests_failed"] = failed
    base["tests_total"] = len(tests)
    base["error_message"] = None
    base["error_category"] = None

    agent_status = raw.get("agent_status")
    errors = raw.get("errors")
    error_count = errors if isinstance(errors, int) else (1 if errors else 0)

    if agent_status == "completed_no_patch":
        base.update(status="no_patch", resolved=False, score=0.0, error_category="agent_no_patch")
    elif agent_status in ("timeout", "budget_exceeded"):
        base.update(
            status="timeout" if agent_status == "timeout" else "error",
            resolved=False,
            score=None,
            error_category=agent_status,
            error_message=raw.get("agent_error") or agent_status,
        )
    elif agent_status == "failed":
        base.update(
            status="error",
            resolved=False,
            score=None,
            error_category="agent_failed",
            error_message=_trim(raw.get("agent_error")),
        )
    elif not tests:
        base.update(
            status="error",
            resolved=False,
            score=None,
            error_category="harness_error" if error_count else "no_result",
            error_message=_first_test_error(tests) or "no test results",
        )
    elif error_count:
        base.update(
            status="error",
            resolved=False,
            score=None,
            error_category="harness_error",
            error_message=_first_test_error(tests) or f"{error_count} harness error(s)",
        )
    elif failed == 0:
        base.update(status="resolved", resolved=True, score=1.0)
    else:
        base.update(
            status="unresolved",
            resolved=False,
            score=0.0,
            error_message=_first_test_error(tests),
        )

    duration = raw.get("agent_execution") or raw.get("execution")
    if isinstance(duration, (int, float)):
        base["duration_s"] = float(duration)
    if raw.get("agent_patch_file"):
        base["artifacts"]["agent_patch"] = str(raw["agent_patch_file"])
    return base


def _first_test_error(tests: list[Any]) -> str | None:
    for item in tests:
        if isinstance(item, dict):
            message = item.get("error_msg")
            if message:
                return _trim(str(message))
    return None


def _trim(value: Any, limit: int = 2000) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "\n... [truncated]"


def _env_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.add(line.split("=", 1)[0].strip())
    return keys


def _is_lfs_pointer(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(64).startswith(b"version https://git-lfs")
    except OSError:
        return False


def _docker_check() -> Check:
    docker = shutil.which("docker")
    if not docker:
        return check("docker", "error", "docker executable not found")
    try:
        completed = subprocess.run(
            [docker, "info"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return check("docker", "error", f"docker info failed: {exc}")
    return check(
        "docker",
        "ok" if completed.returncode == 0 else "error",
        "daemon reachable" if completed.returncode == 0 else _first_line(completed.stderr),
    )


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""
