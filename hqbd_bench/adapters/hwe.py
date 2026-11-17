"""HWE-bench adapter: images -> adapter -> Harbor -> verify -> evaluator."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections import OrderedDict
from pathlib import Path
from typing import Any

from ..io import read_json
from ..model import (
    CaseRef,
    Check,
    CollectResult,
    Command,
    Completion,
    Condition,
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

LOCAL_BUILD = "local_build_required"
PUBLISHED = "docker_image_published"


class HweAdapter(BaseAdapter):
    name = "hwe"
    version = "1"
    capabilities = {
        "groups": ["org", "repo"],
        "modes": ["agent"],
        "requires_docker": True,
        "native_format": "hwe_jsonl",
    }

    # -- validation -----------------------------------------------------
    def validate(self, manifest: Manifest, ctx: PlanContext) -> list[Check]:
        checks: list[Check] = []
        rows = _catalog_rows(ctx.root, manifest)
        expected = manifest.expect("records")
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
                "hwe.runnable",
                "ok" if expected_runnable in (None, runnable) else "error",
                f"{runnable} runnable" + ("" if expected_runnable is None else f", expected {expected_runnable}"),
            )
        )

        registry = _images(manifest).get("registry", "ghcr.io/pku-liang")
        bad_images: list[str] = []
        local_build = 0
        for row in rows:
            native_id = str(row.get("native_id") or "")
            if "#" not in native_id or "/" not in native_id:
                bad_images.append(str(row.get("hqbd_id")))
                continue
            org_repo, number = native_id.split("#", 1)
            org, repo = org_repo.split("/", 1)
            expected_image = f"{registry}/{org.lower()}_m_{repo.lower()}:pr-{number}"
            if row.get("runnability_status") == LOCAL_BUILD:
                local_build += 1
                if row.get("docker_image") is not None or row.get("runnable"):
                    bad_images.append(str(row.get("hqbd_id")))
            elif row.get("docker_image") != expected_image:
                bad_images.append(str(row.get("hqbd_id")))
        checks.append(
            check(
                "hwe.images",
                "ok" if not bad_images else "error",
                "image names and runnability agree"
                if not bad_images
                else f"{len(bad_images)} mismatches, e.g. {bad_images[:3]}",
            )
        )
        checks.append(
            check(
                "hwe.local_build",
                "ok",
                f"{local_build} cases require a locally built image",
            )
        )

        cases = _all_native_cases(ctx, manifest)
        missing_fields: list[str] = []
        for case in cases:
            native = case.native
            base = native.get("base") if isinstance(native.get("base"), dict) else {}
            for field, value in (
                ("org", native.get("org")),
                ("repo", native.get("repo")),
                ("number", native.get("number")),
                ("base.sha", base.get("sha")),
                ("fix_patch", native.get("fix_patch")),
                ("tb_script", native.get("tb_script")),
            ):
                if value in (None, ""):
                    missing_fields.append(f"{case.hqbd_id}:{field}")
        checks.append(
            check(
                "hwe.native_fields",
                "ok" if not missing_fields else "error",
                "all native evaluation fields present"
                if not missing_fields
                else f"{len(missing_fields)} missing, e.g. {missing_fields[:3]}",
            )
        )
        return checks

    def doctor(
        self, manifest: Manifest, ctx: PlanContext, check_images: bool = False
    ) -> list[Check]:
        checks: list[Check] = []
        for module in (
            "hwe_bench/harness/harbor/adapter.py",
            "hwe_bench/harness/harbor/verify_bridge.py",
            "hwe_bench/harness/evaluator.py",
        ):
            path = ctx.backend_root / module
            checks.append(check(f"hwe.{Path(module).stem}", "ok" if path.is_file() else "error", str(path)))

        harbor = ctx.harbor_argv[0] if ctx.harbor_argv else "harbor"
        harbor_path = shutil.which(harbor) if os.sep not in harbor else harbor
        checks.append(
            check(
                "hwe.harbor",
                "ok" if harbor_path and Path(harbor_path).exists() else "error",
                str(harbor_path or f"{harbor!r} not found on PATH"),
            )
        )
        checks.append(_docker_check())

        rows = _catalog_rows(ctx.root, manifest)
        open_titan = sum(1 for row in rows if row.get("runnability_status") == LOCAL_BUILD)
        if open_titan:
            checks.append(
                check(
                    "hwe.local_build",
                    "warn",
                    f"{open_titan} cases need a local image (Synopsys VCS); "
                    "pass --allow-local-build to include them",
                )
            )

        if check_images:
            checks.extend(_image_checks(rows, _images(manifest)))
        return checks

    def validate_options(
        self, manifest: Manifest, options: ExecutionOptions
    ) -> list[str]:
        errors: list[str] = []
        mode = options.mode or "agent"
        if mode != "agent":
            errors.append("HWE only supports --mode agent")
        if not options.agent:
            errors.append("HWE requires --agent (for example claude-code)")
        if not options.model:
            errors.append("HWE requires --model (for example anthropic/claude-sonnet-4-6)")
        if options.concurrency < 1:
            errors.append("--concurrency must be >= 1")
        if options.allow_golden:
            errors.append("--allow-golden is not supported for HWE")
        missing_env = [name for name in options.agent_env if name not in os.environ]
        if missing_env:
            errors.append(
                "agent environment variables not set: " + ", ".join(missing_env)
            )
        return errors

    # -- planning -------------------------------------------------------
    def plan(
        self, manifest: Manifest, selection: Selection, ctx: PlanContext
    ) -> list[GroupPlan]:
        groups: "OrderedDict[tuple[str, str], list[CaseRef]]" = OrderedDict()
        for case in selection.cases:
            native = case.native
            key = (str(native.get("org", "")), str(native.get("repo", "")))
            groups.setdefault(key, []).append(case)

        images = _images(manifest)
        plans: list[GroupPlan] = []
        for (org, repo), cases in groups.items():
            group_id = slugify(f"{org}__{repo}")
            group_dir = ctx.run_paths.group_dir(group_id)
            input_path = ctx.run_paths.group_input(group_id)
            tasks_dir = group_dir / "tasks"
            jobs_dir = group_dir / "jobs"
            job_name = _job_name(ctx.run_id, group_id)
            job_dir = jobs_dir / job_name
            patches_dir = group_dir / "patches"
            eval_dir = group_dir / "eval"
            workdir = (group_dir / "eval_workdir").resolve()
            log_dir = (group_dir / "eval_logs").resolve()
            patches_file = patches_dir / "patches.jsonl"
            final_report = eval_dir / "final_report.json"

            stages: list[Stage] = []
            distributed = [
                case for case in cases if case.runnability_status != LOCAL_BUILD
            ]
            if distributed:
                stages.append(
                    Stage(
                        id="pull-images",
                        commands=_pull_commands(
                            org, repo, distributed, images, ctx, input_path
                        ),
                        optional=True,
                        resume="rerun",
                    )
                )

            stages.append(
                Stage(
                    id="adapter",
                    commands=[
                        Command(
                            argv=list(ctx.python_argv)
                            + [
                                "-m",
                                "hwe_bench.harness.harbor.adapter",
                                "--input",
                                str(input_path),
                                "--output",
                                str(tasks_dir),
                            ],
                            cwd=ctx.backend_root,
                            timeout_s=ctx.options.timeout_s,
                        )
                    ],
                    expected_outputs=[tasks_dir],
                    completion=Completion(kind="all_exist"),
                    resume="rerun",
                    before_run=_reset_dir(tasks_dir),
                )
            )

            harbor_argv = list(ctx.harbor_argv) + [
                "run",
                "--path",
                str(tasks_dir),
                "-a",
                str(ctx.options.agent),
                "-m",
                str(ctx.options.model),
                "--job-name",
                job_name,
                "--jobs-dir",
                str(jobs_dir),
                "-k",
                str(ctx.options.attempts),
                "-r",
                str(ctx.options.retries),
                "--n-concurrent",
                str(ctx.options.concurrency),
            ]
            display_argv = list(harbor_argv)
            for key, value in ctx.options.agent_kwargs:
                harbor_argv.extend(["--ak", f"{key}={value}"])
                display_argv.extend(["--ak", f"{key}={value}"])
            for name in ctx.options.agent_env:
                value = os.environ.get(name, "")
                harbor_argv.extend(["--ae", f"{name}={value}"])
                display_argv.extend(["--ae", f"{name}=***"])
            stages.append(
                Stage(
                    id="harbor",
                    commands=[
                        Command(
                            argv=harbor_argv,
                            display_argv=display_argv,
                            cwd=ctx.backend_root,
                            timeout_s=ctx.options.timeout_s,
                        )
                    ],
                    depends_on=[stage.id for stage in stages],
                    expected_outputs=[job_dir / "result.json"],
                    completion=Completion(kind="all_exist"),
                    resume="skip_if_complete",
                    collect_on_failure=True,
                )
            )

            stages.append(
                Stage(
                    id="verify",
                    commands=[
                        Command(
                            argv=list(ctx.python_argv)
                            + [
                                "-m",
                                "hwe_bench.harness.harbor.verify_bridge",
                                "--harbor-job-dir",
                                str(job_dir),
                                "--output",
                                str(patches_dir),
                            ],
                            cwd=ctx.backend_root,
                            timeout_s=ctx.options.timeout_s,
                        )
                    ],
                    depends_on=["harbor"],
                    expected_outputs=[
                        patches_dir / "collection_summary.json",
                        patches_file,
                    ],
                    completion=Completion(kind="all_exist"),
                    resume="rerun",
                    collect_on_failure=True,
                )
            )

            stages.append(
                Stage(
                    id="evaluate",
                    commands=[
                        Command(
                            argv=list(ctx.python_argv)
                            + [
                                "-m",
                                "hwe_bench.harness.evaluator",
                                "--workdir",
                                str(workdir),
                                "--patch_files",
                                str(patches_file.resolve()),
                                "--dataset_files",
                                str(input_path.resolve()),
                                "--output_dir",
                                str(eval_dir.resolve()),
                                "--log_dir",
                                str(log_dir),
                                "--stop_on_error",
                                "false",
                                "--max_workers",
                                str(ctx.options.concurrency),
                                "--max_workers_build_image",
                                str(ctx.options.concurrency),
                                "--max_workers_run_instance",
                                str(ctx.options.concurrency),
                            ],
                            cwd=ctx.backend_root,
                            timeout_s=ctx.options.timeout_s,
                        )
                    ],
                    depends_on=["verify"],
                    condition=Condition(kind="nonempty", path=patches_file),
                    expected_outputs=[final_report],
                    completion=Completion(kind="all_exist"),
                    resume="rerun",
                    reset_on_change=[patches_file, input_path],
                    reset_paths=[workdir, eval_dir, log_dir],
                    collect_on_failure=True,
                )
            )

            plans.append(
                GroupPlan(
                    id=group_id,
                    cases=cases,
                    stages=stages,
                    framework="harbor",
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
            results.extend(_collect_group(outcome, ctx, unmatched))
        return CollectResult(results=results, unmatched=unmatched)


# ---------------------------------------------------------------------------
# planning helpers


def _images(manifest: Manifest) -> dict[str, Any]:
    values = manifest.section("backend").get("images")
    return values if isinstance(values, dict) else {}


def _job_name(run_id: str, group_id: str) -> str:
    return slugify(f"hqbd-{run_id}-{group_id}")


def _pull_commands(
    org: str,
    repo: str,
    cases: list[CaseRef],
    images: dict[str, Any],
    ctx: PlanContext,
    input_path: Path,
) -> list[Command]:
    mode = str(images.get("mode") or "direct")
    if mode == "script":
        script = ctx.backend_root / "scripts" / "pull_images.sh"
        return [
            Command(
                argv=[
                    "bash",
                    str(script),
                    _repo_key(repo),
                    "--dataset",
                    str(input_path),
                ],
                cwd=ctx.backend_root,
                timeout_s=ctx.options.timeout_s,
            )
        ]

    registry = str(images.get("registry") or "ghcr.io/pku-liang")
    local_prefix = str(images.get("local_prefix") or "hwebench")
    image = f"{org.lower()}_m_{repo.lower()}"
    tags = ["base"] + [
        f"pr-{int(case.native['number'])}" for case in cases if "number" in case.native
    ]
    commands: list[Command] = []
    for tag in tags:
        remote = f"{registry}/{image}:{tag}"
        local = f"{local_prefix}/{image}:{tag}"
        commands.append(
            Command(
                argv=["docker", "pull", remote],
                timeout_s=ctx.options.timeout_s,
            )
        )
        commands.append(
            Command(
                argv=["docker", "tag", remote, local],
                timeout_s=ctx.options.timeout_s,
            )
        )
    return commands


def _repo_key(repo: str) -> str:
    return repo.lower().replace("-", "")


def _reset_dir(path: Path):
    def before_run() -> None:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)

    return before_run


# ---------------------------------------------------------------------------
# collection helpers


def _collect_group(
    outcome: GroupOutcome,
    ctx: PlanContext,
    unmatched: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    patches_dir = outcome.group_dir / "patches"
    eval_dir = outcome.group_dir / "eval"
    workdir = (outcome.group_dir / "eval_workdir").resolve()

    final = _read_object(eval_dir / "final_report.json")
    summary = _read_list(patches_dir / "collection_summary.json")

    resolved = _keys(final.get("resolved_ids"))
    unresolved = _keys(final.get("unresolved_ids"))
    errored = _keys(final.get("error_ids")) | _keys(final.get("incomplete_ids"))
    empty_patch = _keys(final.get("empty_patch_ids"))

    summary_by_key: dict[tuple[str, str, int], dict[str, Any]] = {}
    for entry in summary:
        instance_id = str(entry.get("instance_id") or "")
        key = _parse_instance(instance_id)
        if key is None:
            unmatched.append(
                {
                    "group_id": outcome.group_id,
                    "trial_name": entry.get("trial_name"),
                    "reason": entry.get("error") or "unmapped trial",
                }
            )
            continue
        summary_by_key[key] = entry

    selected_keys = {_case_key(case) for case in outcome.cases}
    for key in sorted(set(resolved) | set(unresolved) | set(errored) | set(empty_patch)):
        if key not in selected_keys:
            unmatched.append(
                {
                    "group_id": outcome.group_id,
                    "instance_id": f"{key[0]}/{key[1]}:pr-{key[2]}",
                    "reason": "not in selection",
                }
            )

    results: list[dict[str, Any]] = []
    for case in outcome.cases:
        key = _case_key(case)
        entry = summary_by_key.get(key)
        per_case_path, per_case = _per_case_report(workdir, case)
        results.append(
            _normalize_case(
                case,
                key,
                entry,
                per_case_path,
                per_case,
                resolved,
                unresolved,
                errored,
                empty_patch,
                outcome,
                ctx,
            )
        )
    return results


def _normalize_case(
    case: CaseRef,
    key: tuple[str, str, int],
    entry: dict[str, Any] | None,
    per_case_path: Path | None,
    per_case: dict[str, Any] | None,
    resolved: set[tuple[str, str, int]],
    unresolved: set[tuple[str, str, int]],
    errored: set[tuple[str, str, int]],
    empty_patch: set[tuple[str, str, int]],
    outcome: GroupOutcome,
    ctx: PlanContext,
) -> dict[str, Any]:
    artifacts = {
        "patches": ctx.run_paths.relative(outcome.group_dir / "patches" / "patches.jsonl"),
        "eval": ctx.run_paths.relative(outcome.group_dir / "eval" / "final_report.json"),
        "workdir": ctx.run_paths.relative(outcome.group_dir / "eval_workdir"),
    }
    if per_case_path is not None:
        artifacts["report"] = ctx.run_paths.relative(per_case_path)

    base: dict[str, Any] = {
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
        "error_message": "case absent from HWE artifacts",
        "agent": ctx.options.agent,
        "model": ctx.options.model,
        "backend": {
            "framework": "harbor",
            "image": case.row.get("docker_image"),
        },
        "artifacts": artifacts,
    }

    if key in resolved:
        base.update(status="resolved", resolved=True, score=1.0, error_category=None, error_message=None)
    elif key in unresolved:
        base.update(status="unresolved", resolved=False, score=0.0, error_category=None, error_message=None)
    elif key in empty_patch and key not in errored:
        base.update(status="no_patch", resolved=False, score=0.0, error_category="empty_patch", error_message=None)
    elif key in errored:
        base.update(
            status="error",
            resolved=False,
            score=None,
            error_category="evaluator_error",
            error_message="evaluator reported error or incomplete instance",
        )
    elif entry is not None:
        if entry.get("error"):
            base.update(
                status="error",
                resolved=False,
                score=None,
                error_category="agent_or_trial_error",
                error_message=str(entry.get("error")),
            )
        elif entry.get("has_patch") is False:
            base.update(status="no_patch", resolved=False, score=0.0, error_category="empty_patch")
        else:
            base.update(
                status="error",
                resolved=False,
                score=None,
                error_category="no_evaluator_decision",
                error_message="patch collected but evaluator produced no decision",
            )
    else:
        return base

    if per_case is not None:
        fix = per_case.get("fix_patch_result") or {}
        if isinstance(fix, dict):
            total = fix.get("all_count")
            if isinstance(total, int):
                base["tests_total"] = total
                base["tests_passed"] = int(fix.get("passed_count") or 0)
                base["tests_failed"] = int(fix.get("failed_count") or 0)
        if per_case.get("valid") is False and per_case.get("error_msg"):
            base["error_message"] = _trim(per_case["error_msg"])
            if base["status"] in ("resolved", "unresolved"):
                base["status"] = "error"
                base["resolved"] = False
                base["score"] = None
                base["error_category"] = "invalid_report"
    return base


def _per_case_report(
    workdir: Path, case: CaseRef
) -> tuple[Path | None, dict[str, Any] | None]:
    org = str(case.native.get("org", ""))
    repo = str(case.native.get("repo", ""))
    number = int(case.native.get("number", 0))
    candidates = [
        workdir / org / repo / "evals" / f"pr-{number}" / "report.json",
        workdir / org.lower() / repo.lower() / "evals" / f"pr-{number}" / "report.json",
    ]
    for path in candidates:
        if path.is_file():
            return path, _read_object(path)
    return None, None


def _case_key(case: CaseRef) -> tuple[str, str, int]:
    native = case.native
    if native.get("org") and native.get("repo") and native.get("number") is not None:
        return (
            str(native.get("org", "")).lower(),
            str(native.get("repo", "")).lower(),
            int(native.get("number", 0)),
        )
    native_id = case.native_id
    if "/" in native_id and "#" in native_id:
        org_repo, number = native_id.split("#", 1)
        org, repo = org_repo.split("/", 1)
        try:
            return (org.lower(), repo.lower(), int(number))
        except ValueError:
            pass
    return ("", "", 0)


def _parse_instance(instance_id: str) -> tuple[str, str, int] | None:
    try:
        org_repo, pr_part = instance_id.split(":")
        org, repo = org_repo.split("/")
        if not pr_part.startswith("pr-"):
            return None
        return (org.lower(), repo.lower(), int(pr_part[3:]))
    except (ValueError, TypeError):
        return None


def _keys(values: Any) -> set[tuple[str, str, int]]:
    if not isinstance(values, list):
        return set()
    keys: set[tuple[str, str, int]] = set()
    for value in values:
        key = _parse_instance(str(value))
        if key is not None:
            keys.add(key)
    return keys


def _read_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = read_json(path)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _read_list(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        value = read_json(path)
    except (OSError, ValueError):
        return []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _catalog_rows(root: Path, manifest: Manifest) -> list[dict[str, Any]]:
    from ..selection import load_catalog

    return load_catalog(root, manifest)


def _all_native_cases(ctx: PlanContext, manifest: Manifest) -> list[CaseRef]:
    from ..selection import SelectionFilters, select_cases

    return select_cases(ctx.root, manifest, SelectionFilters(runnable=False)).cases


def _trim(value: Any, limit: int = 2000) -> str:
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "\n... [truncated]"


def _docker_check() -> Check:
    docker = shutil.which("docker")
    if not docker:
        return check("docker", "error", "docker executable not found")
    try:
        completed = subprocess.run(
            [docker, "info"], capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return check("docker", "error", f"docker info failed: {exc}")
    return check(
        "docker",
        "ok" if completed.returncode == 0 else "error",
        "daemon reachable" if completed.returncode == 0 else _first_line(completed.stderr),
    )


def _image_checks(rows: list[dict[str, Any]], images: dict[str, Any]) -> list[Check]:
    local_prefix = str(images.get("local_prefix") or "hwebench")
    repos: dict[tuple[str, str], int] = {}
    for row in rows:
        if row.get("runnability_status") == LOCAL_BUILD:
            continue
        native_id = str(row.get("native_id") or "")
        if "#" not in native_id:
            continue
        org_repo = native_id.split("#", 1)[0]
        if "/" not in org_repo:
            continue
        org, repo = org_repo.split("/", 1)
        repos[(org, repo)] = repos.get((org, repo), 0) + 1

    checks: list[Check] = []
    for (org, repo), count in sorted(repos.items()):
        image = f"{local_prefix}/{org.lower()}_m_{repo.lower()}:base"
        try:
            completed = subprocess.run(
                ["docker", "image", "inspect", image],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            checks.append(check(f"images.{org}/{repo}", "warn", f"inspect failed: {exc}"))
            continue
        checks.append(
            check(
                f"images.{org}/{repo}",
                "ok" if completed.returncode == 0 else "warn",
                f"{image} present ({count} cases)"
                if completed.returncode == 0
                else f"{image} missing; run pull-images or scripts/pull_images.sh",
            )
        )
    return checks


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""
