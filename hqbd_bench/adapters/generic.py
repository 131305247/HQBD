"""Config-only adapter for simple external runners.

The manifest declares group keys, stages, and a result mapping.  Commands are
argv lists (never shell strings) with these placeholders:

``{root}``, ``{backend_root}``, ``{run_dir}``, ``{group}``, ``{dataset}``,
``{ids_file}``, ``{env:NAME}``.
"""

from __future__ import annotations

import os
from collections import OrderedDict
from pathlib import Path
from typing import Any

from ..io import read_json, read_jsonl
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

ALLOWED_STATUS = {
    "resolved",
    "unresolved",
    "no_patch",
    "error",
    "missing",
    "skipped",
    "timeout",
}


class GenericAdapter(BaseAdapter):
    name = "generic"
    version = "1"
    capabilities = {
        "groups": "manifest",
        "modes": ["agent", "llm", "golden"],
        "requires_docker": False,
        "native_format": "manifest",
    }

    def validate(self, manifest: Manifest, ctx: PlanContext) -> list[Check]:
        checks: list[Check] = []
        config = manifest.section("generic")
        stages = config.get("stages")
        checks.append(
            check(
                "generic.stages",
                "ok" if isinstance(stages, list) and stages else "error",
                f"{len(stages)} stages" if isinstance(stages, list) else "generic.stages missing",
            )
        )
        collect = config.get("collect")
        checks.append(
            check(
                "generic.collect",
                "ok" if isinstance(collect, dict) and collect.get("results_path") else "error",
                "collect.results_path configured"
                if isinstance(collect, dict) and collect.get("results_path")
                else "collect.results_path missing",
            )
        )
        if isinstance(stages, list):
            unknown: list[str] = []
            for stage in stages:
                if not isinstance(stage, dict):
                    unknown.append("non-object stage")
                    continue
                for command in stage.get("commands") or []:
                    if not isinstance(command, dict):
                        unknown.append("non-object command")
                        continue
                    for token in _flatten(command.get("argv") or []):
                        unknown.extend(_unknown_placeholders(str(token)))
            checks.append(
                check(
                    "generic.placeholders",
                    "ok" if not unknown else "error",
                    "all placeholders known"
                    if not unknown
                    else f"unknown placeholders: {sorted(set(unknown))}",
                )
            )
        return checks

    def doctor(
        self, manifest: Manifest, ctx: PlanContext, check_images: bool = False
    ) -> list[Check]:
        checks: list[Check] = []
        checks.append(
            check(
                "generic.backend",
                "ok" if ctx.backend_root.is_dir() else "warn",
                str(ctx.backend_root),
            )
        )
        executable = ctx.python_argv[0] if ctx.python_argv else ""
        if executable and os.sep in executable:
            checks.append(
                check(
                    "generic.python",
                    "ok" if Path(executable).exists() else "error",
                    executable,
                )
            )
        return checks

    def validate_options(
        self, manifest: Manifest, options: ExecutionOptions
    ) -> list[str]:
        return []

    def plan(
        self, manifest: Manifest, selection: Selection, ctx: PlanContext
    ) -> list[GroupPlan]:
        config = manifest.section("generic")
        group_by = [str(value) for value in config.get("group_by") or ["source"]]
        groups: "OrderedDict[str, list[CaseRef]]" = OrderedDict()
        for case in selection.cases:
            values = [str(case.row.get(key, "")) for key in group_by]
            group_id = slugify("__".join(value for value in values if value) or "all")
            groups.setdefault(group_id, []).append(case)

        stage_specs = config.get("stages")
        if not isinstance(stage_specs, list) or not stage_specs:
            raise ValueError(f"{manifest.path}: generic.stages must be a non-empty list")

        plans: list[GroupPlan] = []
        for group_id, cases in groups.items():
            input_path = ctx.run_paths.group_input(group_id)
            ids_file = ctx.run_paths.group_ids(group_id)
            stages: list[Stage] = []
            for spec in stage_specs:
                if not isinstance(spec, dict) or not spec.get("id"):
                    raise ValueError(f"{manifest.path}: stage entries need an 'id'")
                commands = [
                    _command(entry, group_id, input_path, ids_file, ctx)
                    for entry in spec.get("commands") or []
                ]
                if not commands:
                    raise ValueError(
                        f"{manifest.path}: stage {spec.get('id')!r} has no commands"
                    )
                stage_id = str(spec["id"])
                outputs = [
                    Path(str(_expand(path, group_id, input_path, ids_file, ctx)))
                    for path in spec.get("expected_outputs") or []
                ]
                stages.append(
                    Stage(
                        id=stage_id,
                        commands=commands,
                        depends_on=[str(value) for value in spec.get("depends_on") or []],
                        optional=bool(spec.get("optional", False)),
                        collect_on_failure=bool(spec.get("collect_on_failure", False)),
                        expected_outputs=outputs,
                        completion=Completion(kind="all_exist"),
                        resume=str(spec.get("resume") or "rerun"),
                    )
                )
            plans.append(
                GroupPlan(
                    id=group_id,
                    cases=cases,
                    stages=stages,
                    framework="generic",
                    input_path=input_path,
                )
            )
        return plans

    def collect(
        self, manifest: Manifest, outcomes: list[GroupOutcome], ctx: PlanContext
    ) -> CollectResult:
        config = manifest.section("generic").get("collect") or {}
        if not isinstance(config, dict):
            config = {}
        results: list[dict[str, Any]] = []
        unmatched: list[dict[str, Any]] = []
        for outcome in outcomes:
            path = _expand(
                str(config.get("results_path") or ""),
                outcome.group_id,
                outcome.input_path,
                ctx.run_paths.group_ids(outcome.group_id),
                ctx,
            )
            records = _load_records(path)
            by_id: dict[str, dict[str, Any]] = {}
            id_field = str(config.get("id_field") or "id")
            for record in records:
                value = record.get(id_field)
                if value is not None:
                    by_id[str(value)] = record
            selected = {case.hqbd_id for case in outcome.cases}
            for extra in sorted(set(by_id) - selected):
                unmatched.append({"group_id": outcome.group_id, "id": extra})
            for case in outcome.cases:
                record = by_id.get(case.hqbd_id)
                results.append(_normalize(case, record, outcome, ctx, config))
        return CollectResult(results=results, unmatched=unmatched)


def _normalize(
    case: CaseRef,
    record: dict[str, Any] | None,
    outcome: GroupOutcome,
    ctx: PlanContext,
    config: dict[str, Any],
) -> dict[str, Any]:
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
        "error_message": "case absent from generic results",
        "backend": {"framework": "generic", "image": None},
        "artifacts": {},
    }
    if record is None:
        return base

    status_field = str(config.get("status_field") or "status")
    status_map = config.get("status_map") or {}
    raw_status = str(record.get(status_field) or "")
    status = str(status_map.get(raw_status, raw_status)) if isinstance(status_map, dict) else raw_status
    if status not in ALLOWED_STATUS:
        status = "error"
        base["error_message"] = f"unknown status {raw_status!r}"
        base["error_category"] = "unknown_status"
    base["status"] = status

    resolved_field = config.get("resolved_field")
    if resolved_field and resolved_field in record:
        base["resolved"] = bool(record[resolved_field])
    else:
        base["resolved"] = status == "resolved"

    score_field = config.get("score_field")
    if score_field and record.get(score_field) is not None:
        try:
            base["score"] = float(record[score_field])
        except (TypeError, ValueError):
            base["score"] = None
    else:
        base["score"] = 1.0 if status == "resolved" else (0.0 if status in ("unresolved", "no_patch") else None)

    for source_key, target_key in (
        ("tests_passed", "tests_passed"),
        ("tests_failed", "tests_failed"),
        ("tests_total", "tests_total"),
    ):
        value = record.get(source_key)
        if isinstance(value, int):
            base[target_key] = value

    error = record.get("error") or record.get("error_message")
    if error:
        base["error_message"] = str(error)[:2000]
        base["error_category"] = str(record.get("error_category") or "backend_error")
    return base


def _command(
    spec: dict[str, Any],
    group_id: str,
    input_path: Path,
    ids_file: Path,
    ctx: PlanContext,
) -> Command:
    argv = [_expand(str(token), group_id, input_path, ids_file, ctx) for token in spec.get("argv") or []]
    if not argv:
        raise ValueError("generic command has an empty argv")
    cwd = spec.get("cwd")
    return Command(
        argv=argv,
        cwd=Path(str(_expand(str(cwd), group_id, input_path, ids_file, ctx)))
        if cwd
        else ctx.backend_root,
        timeout_s=spec.get("timeout_s", ctx.options.timeout_s),
    )


def _expand(
    template: str,
    group_id: str,
    input_path: Path,
    ids_file: Path,
    ctx: PlanContext,
) -> Path | str:
    values = {
        "root": str(ctx.root),
        "backend_root": str(ctx.backend_root),
        "run_dir": str(ctx.run_paths.run_dir),
        "group": str(ctx.run_paths.group_dir(group_id)),
        "dataset": str(input_path),
        "ids_file": str(ids_file),
        "run_id": ctx.run_id,
    }
    text = template
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    while "{env:" in text:
        start = text.index("{env:")
        end = text.index("}", start)
        name = text[start + 5 : end]
        text = text[:start] + os.environ.get(name, "") + text[end + 1 :]
    return text


def _unknown_placeholders(token: str) -> list[str]:
    found: list[str] = []
    index = 0
    while True:
        start = token.find("{", index)
        if start < 0:
            return found
        end = token.find("}", start)
        if end < 0:
            return found
        content = token[start + 1 : end]
        if content.startswith("env:"):
            index = end + 1
            continue
        if content not in {
            "root",
            "backend_root",
            "run_dir",
            "group",
            "dataset",
            "ids_file",
            "run_id",
        }:
            found.append("{" + content + "}")
        index = end + 1


def _flatten(values: list[Any]) -> list[Any]:
    flat: list[Any] = []
    for value in values:
        if isinstance(value, list):
            flat.extend(_flatten(value))
        else:
            flat.append(value)
    return flat


def _load_records(path: str | Path) -> list[dict[str, Any]]:
    if not path:
        return []
    target = Path(path)
    if not target.is_file():
        return []
    if target.suffix == ".jsonl":
        return read_jsonl(target)
    try:
        value = read_json(target)
    except (OSError, ValueError):
        return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        if isinstance(value.get("results"), list):
            return [item for item in value["results"] if isinstance(item, dict)]
        return [
            {**item, "id": key}
            for key, item in value.items()
            if isinstance(item, dict)
        ]
    return []
