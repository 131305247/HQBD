"""Stage execution, logging, fingerprints, resume, and locks."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import TOOL_NAME, __version__
from .io import (
    append_jsonl,
    canonical_json,
    hash_many,
    read_json,
    sha256_text,
    write_json,
    write_jsonl,
)
from .model import (
    ExecutionOptions,
    GroupOutcome,
    GroupPlan,
    PlanContext,
    Selection,
    Stage,
)
from .paths import RunPaths
from .registry import Manifest, backend_command, backend_root


class RunError(Exception):
    pass


@dataclass
class RunResult:
    run_id: str
    status: str
    outcomes: list[GroupOutcome]
    state: dict[str, Any]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def make_run_id(dataset: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{dataset}-{stamp}"


def build_context(
    root: Path,
    manifest: Manifest,
    selection: Selection,
    options: ExecutionOptions,
    run_paths: RunPaths,
    adapter: Any,
    python_override: list[str] | None = None,
    harbor_override: list[str] | None = None,
) -> PlanContext:
    python_argv = backend_command(root, manifest, "python", python_override)
    harbor_argv = backend_command(root, manifest, "harbor", harbor_override)
    return PlanContext(
        root=root,
        run_paths=run_paths,
        run_id=run_paths.run_id,
        options=options,
        manifest=manifest,
        backend_root=backend_root(root, manifest),
        python_argv=python_argv,
        harbor_argv=harbor_argv,
        adapter_name=adapter.name,
        adapter_version=adapter.version,
        tool_version=__version__,
        selection_sha256=selection.sha256,
    )


def execute(
    *,
    root: Path,
    manifest: Manifest,
    adapter: Any,
    selection: Selection,
    options: ExecutionOptions,
    run_paths: RunPaths,
    resume: bool = False,
    force_rebuild: bool = False,
    keep_going: bool = False,
    only_stage: list[str] | None = None,
    dry_run: bool = False,
    python_override: list[str] | None = None,
    harbor_override: list[str] | None = None,
) -> RunResult:
    problems = adapter.validate_options(manifest, options)
    if problems:
        raise RunError("invalid options:\n  - " + "\n  - ".join(problems))

    ctx = build_context(
        root,
        manifest,
        selection,
        options,
        run_paths,
        adapter,
        python_override,
        harbor_override,
    )
    plans = adapter.plan(manifest, selection, ctx)
    if not plans:
        raise RunError("selection produced no runnable groups")

    if dry_run:
        return RunResult(
            run_id=run_paths.run_id,
            status="planned",
            outcomes=[],
            state={"groups": [plan.to_dict() for plan in plans]},
        )

    state, resumed = _prepare_run_dir(
        run_paths, manifest, adapter, selection, options, ctx, plans,
        resume=resume, force_rebuild=force_rebuild,
    )
    _acquire_lock(run_paths)

    outcomes: list[GroupOutcome] = []
    aborted = False
    try:
        _write_selection(run_paths, selection)
        for plan in plans:
            group_state = _group_state(state, plan.id)
            if aborted:
                group_state["status"] = "not_run"
                outcomes.append(
                    GroupOutcome(
                        group_id=plan.id,
                        cases=plan.cases,
                        status="not_run",
                        stages={},
                        group_dir=run_paths.group_dir(plan.id),
                        input_path=plan.input_path,
                    )
                )
                continue
            outcome = _run_group(
                run_paths,
                plan,
                group_state,
                state,
                ctx,
                keep_going=keep_going,
                only_stage=only_stage,
            )
            outcomes.append(outcome)
            if outcome.status == "failed" and not keep_going:
                aborted = True

        status = "completed"
        if any(outcome.status == "failed" for outcome in outcomes):
            status = "completed_with_failures"
        state["status"] = status
        state["finished_at"] = utc_now()
        write_json(run_paths.run_json, state)
        return RunResult(run_id=run_paths.run_id, status=status, outcomes=outcomes, state=state)
    except KeyboardInterrupt:
        state["status"] = "interrupted"
        state["finished_at"] = utc_now()
        write_json(run_paths.run_json, state)
        raise
    finally:
        _release_lock(run_paths)


def _prepare_run_dir(
    run_paths: RunPaths,
    manifest: Manifest,
    adapter: Any,
    selection: Selection,
    options: ExecutionOptions,
    ctx: PlanContext,
    plans: list[GroupPlan],
    *,
    resume: bool,
    force_rebuild: bool,
) -> tuple[dict[str, Any], bool]:
    if run_paths.run_dir.exists():
        if force_rebuild:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            archive = run_paths.run_dir.with_name(run_paths.run_dir.name + f".stale.{stamp}")
            shutil.move(str(run_paths.run_dir), str(archive))
        elif not resume:
            raise RunError(
                f"run directory already exists: {run_paths.run_dir} "
                "(use --resume or --force-rebuild)"
            )

    run_paths.run_dir.mkdir(parents=True, exist_ok=True)

    if resume:
        if not run_paths.run_json.is_file():
            raise RunError(f"cannot resume: missing {run_paths.run_json}")
        state = read_json(run_paths.run_json)
        _check_resume_compatibility(state, manifest, adapter, selection, ctx)
        return state, True

    state = {
        "schema_version": 1,
        "tool": TOOL_NAME,
        "tool_version": __version__,
        "run_id": run_paths.run_id,
        "dataset": manifest.name,
        "adapter": adapter.name,
        "adapter_version": adapter.version,
        "root": str(ctx.root),
        "manifest_path": str(manifest.path),
        "manifest_sha256": manifest.sha256,
        "selection": {
            "sha256": selection.sha256,
            "count": len(selection.cases),
            "ids": [case.hqbd_id for case in selection.cases],
            "filters": selection.filters,
        },
        "resolved": options.to_dict(),
        "backend": {
            "root": str(ctx.backend_root),
            "python": ctx.python_argv,
            "harbor": ctx.harbor_argv,
        },
        "started_at": utc_now(),
        "finished_at": None,
        "status": "running",
        "groups": [
            {
                "id": plan.id,
                "cases": [case.hqbd_id for case in plan.cases],
                "status": "pending",
                "stages": {},
            }
            for plan in plans
        ],
    }
    write_json(run_paths.run_json, state)
    append_jsonl(
        run_paths.events_jsonl,
        {"ts": utc_now(), "event": "run_start", "run_id": run_paths.run_id},
    )
    return state, False


def _check_resume_compatibility(
    state: dict[str, Any],
    manifest: Manifest,
    adapter: Any,
    selection: Selection,
    ctx: PlanContext,
) -> None:
    expected = {
        "manifest_sha256": manifest.sha256,
        "adapter": adapter.name,
        "adapter_version": adapter.version,
        "tool_version": __version__,
    }
    diffs: list[str] = []
    for key, value in expected.items():
        if state.get(key) != value:
            diffs.append(f"{key}: {state.get(key)!r} != {value!r}")
    recorded_selection = (state.get("selection") or {}).get("sha256")
    if recorded_selection != selection.sha256:
        diffs.append(
            f"selection.sha256: {recorded_selection!r} != {selection.sha256!r}"
        )
    if diffs:
        raise RunError(
            "cannot resume: inputs changed\n  " + "\n  ".join(diffs)
            + "\nUse a new --run-id or --force-rebuild."
        )
    if state.get("status") in ("completed", "completed_with_failures"):
        # Resuming a completed run is allowed; complete stages are skipped.
        pass


def _group_state(state: dict[str, Any], group_id: str) -> dict[str, Any]:
    for group in state.get("groups") or []:
        if group.get("id") == group_id:
            return group
    group = {"id": group_id, "cases": [], "status": "pending", "stages": {}}
    state.setdefault("groups", []).append(group)
    return group


def _run_group(
    run_paths: RunPaths,
    plan: GroupPlan,
    group_state: dict[str, Any],
    state: dict[str, Any],
    ctx: PlanContext,
    *,
    keep_going: bool,
    only_stage: list[str] | None,
) -> GroupOutcome:
    group_dir = run_paths.group_dir(plan.id)
    group_dir.mkdir(parents=True, exist_ok=True)
    from .selection import materialize_inputs

    materialize_inputs(run_paths, plan.id, plan.cases)
    write_json(run_paths.group_plan(plan.id), plan.to_dict())
    group_state["status"] = "running"
    write_json(run_paths.run_json, state)

    stage_status: dict[str, str] = {}
    blocked = False
    suffered_required_failure = False
    for stage in plan.stages:
        record = group_state.setdefault("stages", {}).setdefault(stage.id, {})
        if only_stage and stage.id not in only_stage:
            record["status"] = "skipped"
            record["reason"] = "not selected by --only-stage"
            stage_status[stage.id] = "skipped"
            continue
        if blocked:
            record["status"] = "not_run"
            stage_status[stage.id] = "not_run"
            continue

        outcome = _run_stage(run_paths, plan, stage, record, ctx, state)
        stage_status[stage.id] = outcome
        if outcome in ("failed", "timeout", "artifact_missing"):
            if stage.optional:
                print(
                    f"[{plan.id}] {stage.id}: {outcome} (optional stage; continuing)",
                    file=sys.stderr,
                )
                continue
            suffered_required_failure = True
            if stage.collect_on_failure:
                print(
                    f"[{plan.id}] {stage.id}: {outcome} (continuing to collect artifacts)",
                    file=sys.stderr,
                )
            else:
                blocked = True

    group_state["status"] = "failed" if suffered_required_failure else "ok"
    write_json(run_paths.run_json, state)
    append_jsonl(
        run_paths.events_jsonl,
        {
            "ts": utc_now(),
            "event": "group_end",
            "group": plan.id,
            "status": group_state["status"],
            "stages": stage_status,
        },
    )
    return GroupOutcome(
        group_id=plan.id,
        cases=plan.cases,
        status=group_state["status"],
        stages=stage_status,
        group_dir=group_dir,
        input_path=plan.input_path,
    )


def _run_stage(
    run_paths: RunPaths,
    plan: GroupPlan,
    stage: Stage,
    record: dict[str, Any],
    ctx: PlanContext,
    state: dict[str, Any],
) -> str:
    if stage.condition is not None and not _condition_ok(stage.condition):
        record.update(status="skipped", reason=f"condition not met: {stage.condition.kind}")
        append_jsonl(
            run_paths.events_jsonl,
            {"ts": utc_now(), "event": "stage_skip", "group": plan.id, "stage": stage.id},
        )
        return "skipped"

    fingerprint = _stage_fingerprint(ctx, plan.id, stage)
    if _should_skip(stage, record, fingerprint):
        record["status"] = "ok"
        record["reason"] = "resume: already complete"
        append_jsonl(
            run_paths.events_jsonl,
            {
                "ts": utc_now(),
                "event": "stage_skip",
                "group": plan.id,
                "stage": stage.id,
                "reason": "resume",
            },
        )
        print(f"[{plan.id}] {stage.id}: skipped (complete)", file=sys.stderr)
        return "ok"

    _apply_reset(stage, record)

    if stage.before_run is not None:
        try:
            stage.before_run()
        except OSError as exc:
            record.update(status="failed", error=f"before_run failed: {exc}")
            return "failed"

    record.update(
        status="running",
        fingerprint=fingerprint,
        started_at=utc_now(),
        finished_at=None,
        exit_codes=[],
        error=None,
    )
    write_json(run_paths.run_json, state)
    append_jsonl(
        run_paths.events_jsonl,
        {
            "ts": utc_now(),
            "event": "stage_start",
            "group": plan.id,
            "stage": stage.id,
            "fingerprint": fingerprint,
        },
    )
    log_path = run_paths.group_log(plan.id, stage.id)
    print(f"[{plan.id}] {stage.id}: running", file=sys.stderr)

    status = "ok"
    for command in stage.commands:
        code, timed_out = _execute_command(
            command, log_path, extra_env=ctx.options.env
        )
        record.setdefault("exit_codes", []).append(code)
        if timed_out:
            status = "timeout"
            break
        if code != 0:
            status = "failed"
            break

    if status == "ok":
        complete, reason = _completion_ok(stage)
        if not complete:
            status = "artifact_missing"
            record["error"] = reason

    record["status"] = status
    record["finished_at"] = utc_now()
    write_json(run_paths.run_json, state)
    append_jsonl(
        run_paths.events_jsonl,
        {
            "ts": utc_now(),
            "event": "stage_end",
            "group": plan.id,
            "stage": stage.id,
            "status": status,
            "exit_codes": record.get("exit_codes"),
        },
    )
    print(f"[{plan.id}] {stage.id}: {status}", file=sys.stderr)
    return status


def _stage_fingerprint(ctx: PlanContext, group_id: str, stage: Stage) -> str:
    payload = {
        "manifest_sha256": ctx.manifest.sha256,
        "adapter": ctx.adapter_name,
        "adapter_version": ctx.adapter_version,
        "tool_version": ctx.tool_version,
        "selection_sha256": ctx.selection_sha256,
        "group_id": group_id,
        "stage_id": stage.id,
        "commands": [
            {
                "argv": command.safe_argv(),
                "cwd": str(command.cwd) if command.cwd else None,
                "env_keys": sorted(command.env),
                "timeout_s": command.timeout_s,
            }
            for command in stage.commands
        ],
        "depends_on": stage.depends_on,
        "condition": stage.condition.to_dict() if stage.condition else None,
    }
    return sha256_text(canonical_json(payload))


def _should_skip(stage: Stage, record: dict[str, Any], fingerprint: str) -> bool:
    if stage.resume == "rerun":
        return False
    if record.get("status") != "ok":
        return False
    if record.get("fingerprint") != fingerprint:
        return False
    complete, _ = _completion_ok(stage)
    return complete


def _completion_ok(stage: Stage) -> tuple[bool, str]:
    completion = stage.completion
    if completion is None:
        missing = [str(path) for path in stage.expected_outputs if not path.exists()]
        return (not missing, "missing outputs: " + ", ".join(missing) if missing else "ok")
    if completion.kind == "json_keys":
        path = completion.path
        if path is None or not path.is_file():
            return False, f"missing completion file: {path}"
        try:
            data = read_json(path)
        except (OSError, ValueError) as exc:
            return False, f"invalid completion JSON: {exc}"
        if not isinstance(data, dict):
            return False, "completion JSON is not an object"
        missing = sorted(set(completion.keys) - set(data))
        extra = sorted(set(data) - set(completion.keys))
        if missing or extra:
            return False, f"result keys mismatch (missing={missing[:5]}, extra={extra[:5]})"
        return True, "ok"
    if completion.kind == "nonempty_file":
        path = completion.path
        return (
            bool(path and path.is_file() and path.stat().st_size > 0),
            f"missing or empty: {path}",
        )
    missing = [str(path) for path in stage.expected_outputs if not path.exists()]
    return (not missing, "missing outputs: " + ", ".join(missing) if missing else "ok")


def _condition_ok(condition: Any) -> bool:
    path = condition.path
    if condition.kind == "exists":
        return path.exists()
    if condition.kind == "nonempty":
        return path.is_file() and path.stat().st_size > 0
    if condition.kind == "missing":
        return not path.exists()
    return False


def _apply_reset(stage: Stage, record: dict[str, Any]) -> None:
    if not stage.reset_on_change:
        return
    current = hash_many(stage.reset_on_change)
    previous = record.get("reset_hashes") or {}
    if previous and previous != current:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        for path in stage.reset_paths:
            _archive_path(path, stamp)
        record["reset_archived_at"] = stamp
    record["reset_hashes"] = current


def _archive_path(path: Path, stamp: str) -> None:
    if not path.exists():
        return
    destination = path.with_name(path.name + f".stale.{stamp}")
    counter = 1
    while destination.exists():
        destination = path.with_name(path.name + f".stale.{stamp}.{counter}")
        counter += 1
    path.rename(destination)


def _execute_command(
    command: Any, log_path: Path, extra_env: dict[str, str] | None = None
) -> tuple[int, bool]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(command.env)
    env.update(extra_env or {})
    argv_text = subprocess.list2cmdline(command.safe_argv())
    with log_path.open("a", encoding="utf-8", newline="\n") as log:
        log.write("\n" + "=" * 78 + "\n")
        log.write(f"started: {utc_now()}\n")
        log.write(f"cwd: {command.cwd or Path.cwd()}\n")
        log.write(f"argv: {argv_text}\n")
        log.flush()
        kwargs: dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
        else:
            kwargs["start_new_session"] = True
        try:
            process = subprocess.Popen(
                command.argv,
                cwd=str(command.cwd) if command.cwd else None,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                shell=False,
                **kwargs,
            )
        except OSError as exc:
            log.write(f"failed to start: {exc}\n")
            return 127, False
        timed_out = False
        try:
            code = process.wait(timeout=command.timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_tree(process)
            try:
                code = process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                code = -9
        log.write(f"finished: {utc_now()} exit={code} timeout={timed_out}\n")
        log.flush()
    return code, timed_out


def _kill_tree(process: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except OSError:
            process.kill()


def _write_selection(run_paths: RunPaths, selection: Selection) -> None:
    rows = []
    for case in selection.cases:
        safe = {
            key: case.row.get(key)
            for key in (
                "hqbd_id",
                "source",
                "source_version",
                "native_id",
                "source_file",
                "source_line",
                "task_type",
                "representation",
                "split",
                "runnable",
                "runnability_status",
                "docker_image",
                "usage_license",
            )
        }
        safe["hqbd_id"] = case.hqbd_id
        rows.append(safe)
    write_jsonl(run_paths.selection_jsonl, rows)


def _acquire_lock(run_paths: RunPaths) -> None:
    lock = run_paths.lock_file
    if lock.is_file():
        try:
            data = read_json(lock)
        except (OSError, ValueError):
            data = {}
        pid = data.get("pid")
        if isinstance(pid, int) and _pid_alive(pid):
            raise RunError(
                f"run directory is locked by process {pid}: {run_paths.run_dir}"
            )
        lock.unlink(missing_ok=True)
    write_json(lock, {"pid": os.getpid(), "started_at": utc_now()})


def _release_lock(run_paths: RunPaths) -> None:
    try:
        run_paths.lock_file.unlink()
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
