"""Command-line entry point for the HQBD orchestration layer."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any

from . import TOOL_NAME, __version__
from .io import read_json, write_json
from .maintenance import create_dataset, doctor_dataset, validate_dataset
from .model import Check, ExecutionOptions, PlanContext
from .paths import RunPaths, resolve_root
from .registry import (
    BUILTIN_ADAPTERS,
    Manifest,
    list_manifests,
    load_adapter,
    load_manifest,
)
from .results import (
    options_from_state,
    outcomes_from_run,
    selection_from_state,
    write_outputs,
)
from .runner import RunError, build_context, execute, make_run_id
from .selection import Selection, SelectionFilters, load_catalog, select_cases


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except (RunError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# parser


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description="Run and maintain HQBD benchmark datasets through one entry point.",
    )
    parser.add_argument(
        "--root",
        default=os.environ.get("HQBD_ROOT"),
        help="HQBD root (default: the directory containing hqbd_bench)",
    )
    parser.add_argument("--version", action="version", version=f"{TOOL_NAME} {__version__}")

    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="select cases and run a dataset")
    run.add_argument("dataset")
    _add_selection_args(run)
    _add_execution_args(run)
    run.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    run.add_argument("--print-plan", action="store_true", help="print the plan and exit")
    run.add_argument("--json", action="store_true", help="emit JSON")
    _add_backend_args(run)

    collect = commands.add_parser("collect", help="re-normalize results from a run directory")
    collect.add_argument("run_id")
    collect.add_argument("--force", action="store_true")
    collect.add_argument("--json", action="store_true")

    report = commands.add_parser("report", help="print a run summary or Markdown report")
    report.add_argument("run_id")
    report.add_argument("--format", choices=("md", "json"), default="md")

    runs = commands.add_parser("runs", help="inspect recorded runs")
    run_commands = runs.add_subparsers(dest="runs_command", required=True)
    run_commands.add_parser("list")
    show = run_commands.add_parser("show")
    show.add_argument("run_id")

    dataset = commands.add_parser("dataset", help="list and validate dataset manifests")
    dataset_commands = dataset.add_subparsers(dest="dataset_command", required=True)
    dataset_commands.add_parser("list")
    dataset_show = dataset_commands.add_parser("show")
    dataset_show.add_argument("name")
    dataset_show.add_argument("--json", action="store_true")
    dataset_validate = dataset_commands.add_parser("validate")
    dataset_validate.add_argument("name", nargs="?")
    dataset_validate.add_argument("--all", action="store_true")
    dataset_validate.add_argument("--strict", action="store_true")
    dataset_validate.add_argument("--check-hashes", action="store_true")
    dataset_validate.add_argument("--json", action="store_true")
    dataset_new = dataset_commands.add_parser("new")
    dataset_new.add_argument("name")
    dataset_new.add_argument("--adapter", default="generic")
    dataset_new.add_argument("--force", action="store_true")

    adapter = commands.add_parser("adapter", help="inspect adapters")
    adapter_commands = adapter.add_subparsers(dest="adapter_command", required=True)
    adapter_commands.add_parser("list")
    adapter_doctor = adapter_commands.add_parser("doctor")
    adapter_doctor.add_argument("name")
    adapter_doctor.add_argument("--check-images", action="store_true")

    doctor = commands.add_parser("doctor", help="check prerequisites for datasets")
    doctor.add_argument("name", nargs="?")
    doctor.add_argument("--all", action="store_true")
    doctor.add_argument("--check-images", action="store_true")
    doctor.add_argument("--json", action="store_true")

    return parser


def _add_selection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--ids", help="comma-separated HQBD case IDs")
    parser.add_argument("--source")
    parser.add_argument("--representation")
    parser.add_argument("--task-type")
    parser.add_argument("--usage-license")
    parser.add_argument(
        "--runnable",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="select runnable cases (default from the manifest)",
    )
    parser.add_argument(
        "--allow-local-build",
        action="store_true",
        help="include cases whose runnability is local_build_required",
    )
    parser.add_argument(
        "--where",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="filter on a unified-index field (repeatable)",
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--shard", metavar="I/N")


def _add_execution_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--mode", choices=("agent", "llm", "golden"))
    parser.add_argument("--agent")
    parser.add_argument("--model")
    parser.add_argument("--agent-kwarg", action="append", default=[], metavar="K=V")
    parser.add_argument(
        "--agent-env",
        action="append",
        default=[],
        metavar="NAME",
        help="pass an environment variable to the agent; only the name is recorded",
    )
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--timeout", type=float, help="per-command timeout in seconds")
    parser.add_argument("--env", action="append", default=[], metavar="K=V")
    parser.add_argument("--run-id")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--force-rebuild", action="store_true")
    parser.add_argument("--keep-going", action="store_true")
    parser.add_argument("--only-stage", action="append", default=[])
    parser.add_argument("--allow-golden", action="store_true")
    parser.add_argument("--force-agentic", action="store_true")
    parser.add_argument("--force-copilot", action="store_true")


def _add_backend_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--backend-python",
        help="override backend.python as a shell-like argv string",
    )
    parser.add_argument(
        "--harbor-command",
        help="override backend.harbor as a shell-like argv string",
    )


# ---------------------------------------------------------------------------
# dispatch


def _dispatch(args: argparse.Namespace) -> int:
    root = resolve_root(args.root)
    if args.command == "run":
        return _cmd_run(root, args)
    if args.command == "collect":
        return _cmd_collect(root, args)
    if args.command == "report":
        return _cmd_report(root, args)
    if args.command == "runs":
        return _cmd_runs(root, args)
    if args.command == "dataset":
        return _cmd_dataset(root, args)
    if args.command == "adapter":
        return _cmd_adapter(root, args)
    if args.command == "doctor":
        return _cmd_doctor(root, args)
    raise ValueError(f"unknown command: {args.command}")


# ---------------------------------------------------------------------------
# run / collect / report


def _cmd_run(root: Path, args: argparse.Namespace) -> int:
    manifest = load_manifest(root, args.dataset)
    adapter = load_adapter(manifest)
    filters = _selection_filters(args)
    selection = select_cases(root, manifest, filters)
    options = _execution_options(args, manifest, adapter.name)
    run_id = args.run_id or make_run_id(manifest.name)
    if args.resume and not args.run_id:
        raise ValueError("--resume requires --run-id")
    run_paths = RunPaths(root, run_id)
    python_override = shlex.split(args.backend_python) if args.backend_python else None
    harbor_override = shlex.split(args.harbor_command) if args.harbor_command else None

    result = execute(
        root=root,
        manifest=manifest,
        adapter=adapter,
        selection=selection,
        options=options,
        run_paths=run_paths,
        resume=args.resume,
        force_rebuild=args.force_rebuild,
        keep_going=args.keep_going,
        only_stage=args.only_stage or None,
        dry_run=args.dry_run or args.print_plan,
        python_override=python_override,
        harbor_override=harbor_override,
    )

    if args.dry_run or args.print_plan:
        payload = {
            "run_id": run_id,
            "dataset": manifest.name,
            "adapter": adapter.name,
            "selection": {
                "count": len(selection.cases),
                "sha256": selection.sha256,
                "filters": selection.filters,
            },
            "options": options.to_dict(),
            "groups": result.state["groups"],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    state = result.state
    state["command"] = _safe_command(sys.argv[1:])
    result.state = state
    ctx = build_context(
        root, manifest, selection, options, run_paths, adapter,
        python_override, harbor_override,
    )
    summary = _collect(
        manifest, adapter, selection, options, run_paths, ctx,
        result.outcomes, command_text=state["command"],
    )
    _print_run_summary(summary, args.json)
    return 0 if result.status == "completed" else 1


def _cmd_collect(root: Path, args: argparse.Namespace) -> int:
    run_paths = RunPaths(root, args.run_id)
    state = _load_run_state(run_paths)
    if run_paths.summary_json.is_file() and not args.force:
        print(
            f"{args.run_id}: outputs already exist; use --force to rewrite",
            file=sys.stderr,
        )
        return 0

    manifest = load_manifest(root, str(state.get("dataset")))
    adapter = load_adapter(manifest)
    selection = selection_from_state(run_paths, state)
    if not selection.cases:
        raise ValueError(f"{args.run_id}: selection or group inputs are missing")
    options = options_from_state(state)
    ctx = build_context(root, manifest, selection, options, run_paths, adapter)
    outcomes = outcomes_from_run(run_paths)
    summary = _collect(
        manifest, adapter, selection, options, run_paths, ctx, outcomes,
        command_text=str(state.get("command") or ""),
    )
    _print_run_summary(summary, args.json)
    return 0


def _collect(
    manifest: Manifest,
    adapter: Any,
    selection: Selection,
    options: ExecutionOptions,
    run_paths: RunPaths,
    ctx: PlanContext,
    outcomes: list[Any],
    *,
    command_text: str,
) -> dict[str, Any]:
    collected = adapter.collect(manifest, outcomes, ctx)
    state = read_json(run_paths.run_json)
    state["command"] = command_text
    write_json(run_paths.run_json, state)
    return write_outputs(
        run_paths,
        manifest=manifest,
        state=state,
        outcomes=outcomes,
        collected=collected,
        command_text=command_text,
    )


def _cmd_report(root: Path, args: argparse.Namespace) -> int:
    run_paths = RunPaths(root, args.run_id)
    _load_run_state(run_paths)
    if args.format == "json":
        if not run_paths.summary_json.is_file():
            raise ValueError(f"{args.run_id}: no summary.json; run `collect {args.run_id}` first")
        print(
            json.dumps(
                read_json(run_paths.summary_json), indent=2, ensure_ascii=False
            )
        )
        return 0
    if not run_paths.report_md.is_file():
        raise ValueError(f"{args.run_id}: no report.md; run `collect {args.run_id}` first")
    print(run_paths.report_md.read_text(encoding="utf-8"))
    return 0


def _cmd_runs(root: Path, args: argparse.Namespace) -> int:
    if args.runs_command == "list":
        runs_dir = root / "runs"
        entries: list[dict[str, Any]] = []
        if runs_dir.is_dir():
            for path in sorted(runs_dir.iterdir()):
                state_path = path / "run.json"
                if not state_path.is_file():
                    continue
                try:
                    state = read_json(state_path)
                except (OSError, ValueError):
                    continue
                entries.append(state)
        print(f"{'RUN ID':40} {'DATASET':10} {'STATUS':22} {'SELECTED':>8}  STARTED")
        for state in entries:
            selected = (state.get("selection") or {}).get("count", 0)
            print(
                f"{str(state.get('run_id')):40} {str(state.get('dataset')):10} "
                f"{str(state.get('status')):22} {selected:>8}  {state.get('started_at')}"
            )
        return 0

    run_paths = RunPaths(root, args.run_id)
    state = _load_run_state(run_paths)
    if run_paths.summary_json.is_file():
        try:
            state["_summary"] = read_json(run_paths.summary_json)
        except (OSError, ValueError):
            pass
    print(json.dumps(state, indent=2, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# dataset


def _cmd_dataset(root: Path, args: argparse.Namespace) -> int:
    command = args.dataset_command
    if command == "list":
        manifests = list_manifests(root)
        if not manifests:
            print("no dataset manifests under benchmarks/")
            return 1
        print(f"{'NAME':16} {'ADAPTER':24} {'RECORDS':>8} {'RUNNABLE':>9}  DESCRIPTION")
        for manifest in manifests:
            try:
                rows = load_catalog(root, manifest)
                records = len(rows)
                runnable = sum(1 for row in rows if row.get("runnable"))
            except (OSError, ValueError):
                records = runnable = -1
            print(
                f"{manifest.name:16} {manifest.adapter_spec:24} {records:>8} "
                f"{runnable:>9}  {str(manifest.data.get('description') or '')}"
            )
        return 0

    if command == "show":
        manifest = load_manifest(root, args.name)
        payload = dict(manifest.data)
        payload["_manifest_path"] = str(manifest.path)
        payload["_manifest_sha256"] = manifest.sha256
        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0
        print(f"name:        {manifest.name}")
        print(f"description: {manifest.data.get('description') or ''}")
        print(f"adapter:     {manifest.adapter_spec}")
        print(f"manifest:    {manifest.path}")
        print(f"sha256:      {manifest.sha256}")
        print(f"catalog:     {manifest.section('catalog').get('path')}")
        print(f"backend:     {manifest.section('backend').get('root', '.')}")
        for key, value in (manifest.section("expect") or {}).items():
            print(f"expect.{key}: {value}")
        return 0

    if command == "validate":
        if args.all and args.name:
            raise ValueError("pass either a dataset name or --all, not both")
        if not args.all and not args.name:
            raise ValueError("pass a dataset name or --all")
        names = (
            [manifest.name for manifest in list_manifests(root)]
            if args.all
            else [args.name]
        )
        if not names:
            raise ValueError("no dataset manifests under benchmarks/")
        failed = False
        payload: dict[str, Any] = {}
        for name in names:
            manifest = load_manifest(root, name)
            adapter = load_adapter(manifest)
            ctx = _validation_context(root, manifest, adapter)
            checks = validate_dataset(
                ctx,
                strict=args.strict,
                check_hashes=args.check_hashes,
            )
            payload[name] = [item.to_dict() for item in checks]
            if not args.json:
                print(f"{name}:")
                _print_checks(checks)
            if any(item.status == "error" for item in checks):
                failed = True
        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 1 if failed else 0

    if command == "new":
        path = create_dataset(
            root, args.name, args.adapter, force=args.force
        )
        print(f"created {path}")
        print("next:")
        print(f"  1. put native data and the catalog under datasets/{args.name}/")
        print(f"  2. put the evaluator under evaluators/{args.name}/ (if new)")
        print(f"  3. python -m hqbd_bench dataset validate {args.name}")
        print(f"  4. python -m hqbd_bench run {args.name} --dry-run")
        return 0

    raise ValueError(f"unknown dataset command: {command}")


# ---------------------------------------------------------------------------
# adapter / doctor


def _cmd_adapter(root: Path, args: argparse.Namespace) -> int:
    if args.adapter_command == "list":
        print(f"{'NAME':20} {'SOURCE':10} {'MODES':20} GROUPS")
        specs: list[tuple[str, str]] = [
            (name, "builtin") for name in BUILTIN_ADAPTERS
        ]
        seen = set(BUILTIN_ADAPTERS)
        for manifest in list_manifests(root):
            spec = manifest.adapter_spec
            if spec and spec not in seen:
                specs.append((spec, "manifest"))
                seen.add(spec)
        for spec, source in specs:
            try:
                adapter = _load_adapter_spec(spec)
                capabilities = getattr(adapter, "capabilities", {}) or {}
                modes = ",".join(capabilities.get("modes") or [])
                groups = _stringify_groups(capabilities.get("groups"))
                print(f"{spec:20} {source:10} {modes:20} {groups}")
            except (ValueError, ImportError) as exc:
                print(f"{spec:20} {source:10} ERROR: {exc}")
        return 0

    manifest = load_manifest(root, args.name)
    adapter = load_adapter(manifest)
    ctx = _validation_context(root, manifest, adapter)
    checks = list(adapter.doctor(manifest, ctx, check_images=args.check_images))
    sample = _sample_plan(root, manifest, adapter, ctx)
    if not sample.cases:
        checks.append(Check("adapter.sample", "warn", "no cases selected for a sample plan"))
    else:
        options = ExecutionOptions(
            mode=_default_mode(manifest, adapter.name),
            agent="<agent>",
            model="<model>",
        )
        plan_ctx = build_context(
            root, manifest, sample, options, RunPaths(root, "__sample__"), adapter
        )
        try:
            plans = adapter.plan(manifest, sample, plan_ctx)
        except Exception as exc:
            checks.append(Check("adapter.sample", "error", f"{type(exc).__name__}: {exc}"))
        else:
            checks.append(
                Check(
                    "adapter.sample",
                    "ok" if plans else "error",
                    f"{len(plans)} group plan(s), {sum(len(p.stages) for p in plans)} stage(s)",
                )
            )
    _print_checks(checks)
    return 1 if any(item.status == "error" for item in checks) else 0


def _cmd_doctor(root: Path, args: argparse.Namespace) -> int:
    if args.all and args.name:
        raise ValueError("pass either a dataset name or --all, not both")
    names = (
        [manifest.name for manifest in list_manifests(root)]
        if args.all or not args.name
        else [args.name]
    )
    if not names:
        raise ValueError("no dataset manifests under benchmarks/")
    failed = False
    payload: dict[str, Any] = {}
    for name in names:
        manifest = load_manifest(root, name)
        adapter = load_adapter(manifest)
        ctx = _validation_context(root, manifest, adapter)
        checks = doctor_dataset(ctx, check_images=args.check_images)
        payload[name] = [item.to_dict() for item in checks]
        if not args.json:
            print(f"{name}:")
            _print_checks(checks)
        if any(item.status == "error" for item in checks):
            failed = True
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 1 if failed else 0


# ---------------------------------------------------------------------------
# helpers


def _selection_filters(args: argparse.Namespace) -> SelectionFilters:
    shard = None
    if args.shard:
        try:
            left, right = args.shard.split("/", 1)
            shard = (int(left), int(right))
        except ValueError as exc:
            raise ValueError("--shard must be I/N") from exc
    where = [_parse_pair(value, "--where") for value in args.where]
    ids = [value.strip() for value in (args.ids or "").split(",") if value.strip()]
    return SelectionFilters(
        ids=ids,
        source=args.source,
        representation=args.representation,
        task_type=args.task_type,
        usage_license=args.usage_license,
        runnable=args.runnable,
        allow_local_build=args.allow_local_build,
        where=where,
        limit=args.limit,
        shard=shard,
    )


def _execution_options(
    args: argparse.Namespace, manifest: Manifest, adapter_name: str
) -> ExecutionOptions:
    mode = args.mode or _default_mode(manifest, adapter_name)
    agent = args.agent
    model = args.model
    if args.dry_run or args.print_plan:
        # Placeholders keep planning usable without credentials; they are
        # never executed and are visible in the printed plan.
        agent = agent or "<agent>"
        model = model or "<model>"
    return ExecutionOptions(
        mode=mode,
        agent=agent,
        model=model,
        agent_kwargs=[_parse_pair(value, "--agent-kwarg") for value in args.agent_kwarg],
        agent_env=[str(value) for value in args.agent_env],
        threads=args.threads,
        concurrency=args.concurrency,
        attempts=args.attempts,
        retries=args.retries,
        timeout_s=args.timeout,
        env=dict(_parse_pair(value, "--env") for value in args.env),
        allow_local_build=args.allow_local_build,
        allow_golden=args.allow_golden,
        force_agentic=args.force_agentic,
        force_copilot=args.force_copilot,
    )


def _default_mode(manifest: Manifest, adapter_name: str) -> str:
    value = manifest.section("defaults").get("mode")
    if value in ("agent", "llm", "golden"):
        return str(value)
    return "agent" if adapter_name == "hwe" else "llm"


def _parse_pair(value: str, flag: str) -> tuple[str, str]:
    if "=" not in value:
        raise ValueError(f"{flag} expects KEY=VALUE, got {value!r}")
    key, item = value.split("=", 1)
    if not key.strip():
        raise ValueError(f"{flag} expects a non-empty key")
    return key.strip(), item


def _validation_context(root: Path, manifest: Manifest, adapter: Any) -> PlanContext:
    selection = Selection(cases=[], rows=[], sha256="", filters={})
    options = ExecutionOptions(mode=_default_mode(manifest, adapter.name))
    return build_context(
        root,
        manifest,
        selection,
        options,
        RunPaths(root, "__validate__"),
        adapter,
    )


def _sample_plan(
    root: Path, manifest: Manifest, adapter: Any, ctx: PlanContext
) -> Selection:
    return select_cases(root, manifest, SelectionFilters(limit=1))


def _load_adapter_spec(spec: str) -> Any:
    from types import SimpleNamespace

    return load_adapter(SimpleNamespace(adapter_spec=spec))


def _stringify_groups(value: Any) -> str:
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    return str(value or "")


def _load_run_state(run_paths: RunPaths) -> dict[str, Any]:
    if not run_paths.run_json.is_file():
        raise ValueError(f"unknown run: {run_paths.run_id}")
    value = read_json(run_paths.run_json)
    if not isinstance(value, dict):
        raise ValueError(f"{run_paths.run_json}: run state must be an object")
    return value


def _print_checks(checks: list[Check]) -> None:
    for item in checks:
        print(f"  [{item.status:5}] {item.name}: {item.message}")


def _print_run_summary(summary: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return
    counts = summary.get("counts") or {}
    metrics = summary.get("metrics") or {}
    print(f"run:      {summary.get('run_id')} ({summary.get('status')})")
    print(
        "selected: {selected}  resolved: {resolved}  unresolved: {unresolved}  "
        "no_patch: {no_patch}  error: {error}  missing: {missing}".format(
            **{key: counts.get(key, 0) for key in (
                "selected", "resolved", "unresolved", "no_patch", "error", "missing"
            )}
        )
    )
    print(
        "resolve rate (selected): {selected}  (submitted): {submitted}".format(
            selected=_pct(metrics.get("resolve_rate_selected")),
            submitted=_pct(metrics.get("resolve_rate_submitted")),
        )
    )
    print(f"results:  runs/{summary.get('run_id')}/results.jsonl")
    print(f"summary:  runs/{summary.get('run_id')}/summary.json")
    print(f"report:   runs/{summary.get('run_id')}/report.md")


def _pct(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{100.0 * float(value):.2f}%"


def _safe_command(argv: list[str]) -> str:
    safe: list[str] = []
    hide_next = False
    for token in argv:
        if hide_next:
            if "=" in token:
                key = token.split("=", 1)[0]
                safe.append(f"{key}=***")
            else:
                safe.append("***")
            hide_next = False
            continue
        if token == "--env":
            safe.append(token)
            hide_next = True
            continue
        if token.startswith("--env="):
            key = token[len("--env=") :].split("=", 1)[0]
            safe.append(f"--env={key}=***")
            continue
        safe.append(token)
    return " ".join(shlex.quote(token) for token in safe)
