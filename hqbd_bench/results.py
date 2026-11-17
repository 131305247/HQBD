"""Common result normalization output, aggregates, and reports."""

from __future__ import annotations

from collections import Counter, OrderedDict
from typing import Any

from .io import read_json, read_jsonl, write_json, write_jsonl
from .model import (
    CaseRef,
    CollectResult,
    ExecutionOptions,
    GroupOutcome,
    Selection,
)
from .paths import RunPaths
from .registry import Manifest

STATUSES = ("resolved", "unresolved", "no_patch", "error", "missing", "skipped", "timeout")
FORBIDDEN_KEYS = ("fix_patch", "tb_patch", "test_patch", "tb_script", "problem_statement", "prompt")


def write_outputs(
    run_paths: RunPaths,
    *,
    manifest: Manifest,
    state: dict[str, Any],
    outcomes: list[GroupOutcome],
    collected: CollectResult,
    command_text: str,
) -> dict[str, Any]:
    order = {case_id: index for index, case_id in enumerate(state.get("selection", {}).get("ids", []))}
    results = [
        _sanitize(row)
        for row in sorted(
            collected.results, key=lambda row: order.get(str(row.get("hqbd_id")), 10**9)
        )
    ]
    unmatched = [_sanitize(item) for item in collected.unmatched]
    summary = summarize(results, state, outcomes, unmatched)
    write_jsonl(run_paths.results_jsonl, results)
    write_json(run_paths.summary_json, summary)
    run_paths.report_md.write_text(
        render_markdown(manifest, state, summary, results, outcomes, command_text),
        encoding="utf-8",
        newline="\n",
    )
    return summary


def summarize(
    results: list[dict[str, Any]],
    state: dict[str, Any],
    outcomes: list[GroupOutcome],
    unmatched: list[dict[str, Any]],
) -> dict[str, Any]:
    status_counts = Counter(str(row.get("status") or "missing") for row in results)
    selected = len(state.get("selection", {}).get("ids", [])) or len(results)
    resolved = status_counts.get("resolved", 0)
    unresolved = status_counts.get("unresolved", 0)
    errored = status_counts.get("error", 0)
    submitted = resolved + unresolved + errored
    scores = [
        float(row["score"])
        for row in results
        if row.get("status") in ("resolved", "unresolved", "error")
        and isinstance(row.get("score"), (int, float))
    ]

    stage_failures: list[dict[str, Any]] = []
    for outcome in outcomes:
        for stage_id, status in outcome.stages.items():
            if status in ("failed", "timeout", "artifact_missing"):
                stage_failures.append(
                    {"group_id": outcome.group_id, "stage": stage_id, "status": status}
                )

    return {
        "schema_version": "1.0",
        "run_id": state.get("run_id"),
        "dataset": state.get("dataset"),
        "adapter": state.get("adapter"),
        "status": state.get("status"),
        "started_at": state.get("started_at"),
        "finished_at": state.get("finished_at"),
        "selection": {
            "total": selected,
            "sha256": (state.get("selection") or {}).get("sha256"),
            "filters": (state.get("selection") or {}).get("filters"),
        },
        "counts": {
            "selected": selected,
            "resolved": resolved,
            "unresolved": unresolved,
            "no_patch": status_counts.get("no_patch", 0),
            "error": errored,
            "missing": status_counts.get("missing", 0),
            "skipped": status_counts.get("skipped", 0),
            "timeout": status_counts.get("timeout", 0),
            "submitted": submitted,
        },
        "metrics": {
            "resolve_rate_selected": _rate(resolved, selected),
            "resolve_rate_submitted": _rate(resolved, submitted),
            "mean_score_on_submitted": (sum(scores) / len(scores)) if scores else None,
        },
        "by_source": _breakdown(results, "source"),
        "by_representation": _breakdown(results, "representation"),
        "by_task_type": _breakdown(results, "task_type"),
        "by_group": _breakdown(results, "group_id"),
        "stage_failures": stage_failures,
        "unmatched_results": unmatched,
    }


def _sanitize(value: Any) -> Any:
    """Drop evaluation material from anything headed for a report artifact."""

    if isinstance(value, dict):
        return {
            key: _sanitize(item)
            for key, item in value.items()
            if key not in FORBIDDEN_KEYS
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value


def _rate(numerator: int, denominator: int) -> float | None:
    return (numerator / denominator) if denominator else None


def _breakdown(results: list[dict[str, Any]], field: str) -> dict[str, dict[str, Any]]:
    groups: "OrderedDict[str, list[dict[str, Any]]]" = OrderedDict()
    for row in results:
        groups.setdefault(str(row.get(field) or ""), []).append(row)
    output: dict[str, dict[str, Any]] = {}
    for key, rows in groups.items():
        counts = Counter(str(row.get("status") or "missing") for row in rows)
        selected = len(rows)
        resolved = counts.get("resolved", 0)
        output[key] = {
            "selected": selected,
            "resolved": resolved,
            "unresolved": counts.get("unresolved", 0),
            "no_patch": counts.get("no_patch", 0),
            "error": counts.get("error", 0),
            "missing": counts.get("missing", 0),
            "resolve_rate": _rate(resolved, selected),
        }
    return output


def render_markdown(
    manifest: Manifest,
    state: dict[str, Any],
    summary: dict[str, Any],
    results: list[dict[str, Any]],
    outcomes: list[GroupOutcome],
    command_text: str,
) -> str:
    counts = summary["counts"]
    metrics = summary["metrics"]
    lines = [
        f"# HQBD run `{state.get('run_id')}`",
        "",
        f"- Dataset: `{state.get('dataset')}`",
        f"- Adapter: `{state.get('adapter')}`",
        f"- Status: `{state.get('status')}`",
        f"- Manifest: `{state.get('manifest_path')}`",
        f"- Manifest SHA-256: `{state.get('manifest_sha256')}`",
        f"- Selection SHA-256: `{(state.get('selection') or {}).get('sha256')}`",
        "",
        "## Command",
        "",
        "```text",
        command_text or "(not recorded)",
        "```",
        "",
        "## Results",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Selected | {counts['selected']} |",
        f"| Resolved | {counts['resolved']} |",
        f"| Unresolved | {counts['unresolved']} |",
        f"| No patch | {counts['no_patch']} |",
        f"| Error | {counts['error']} |",
        f"| Missing | {counts['missing']} |",
        f"| Submitted (resolved + unresolved + error) | {counts['submitted']} |",
        f"| Resolve rate (selected) | {_pct(metrics['resolve_rate_selected'])} |",
        f"| Resolve rate (submitted) | {_pct(metrics['resolve_rate_submitted'])} |",
        "",
        "The submitted denominator excludes empty patches, matching the upstream "
        "HWE-bench convention. Use the selected denominator when comparing runs "
        "where agents may submit nothing.",
        "",
        "## Groups",
        "",
        "| Group | Selected | Resolved | Rate | Stages |",
        "|---|---:|---:|---:|---|",
    ]
    by_group = summary.get("by_group") or {}
    for outcome in outcomes:
        row = by_group.get(outcome.group_id, {})
        stages = ", ".join(f"{key}:{value}" for key, value in outcome.stages.items())
        lines.append(
            f"| `{outcome.group_id}` | {row.get('selected', len(outcome.cases))} | "
            f"{row.get('resolved', 0)} | {_pct(row.get('resolve_rate'))} | {stages} |"
        )

    failures = summary.get("stage_failures") or []
    if failures:
        lines.extend(["", "## Stage failures", "", "| Group | Stage | Status |", "|---|---|---|"])
        for item in failures:
            lines.append(
                f"| `{item['group_id']}` | `{item['stage']}` | `{item['status']}` |"
            )

    unmatched = summary.get("unmatched_results") or []
    if unmatched:
        lines.extend(
            [
                "",
                "## Unmatched backend results",
                "",
                f"{len(unmatched)} artifact rows did not map to the selection. "
                "See `summary.json` for the full list.",
            ]
        )

    lines.extend(
        [
            "",
            "## Artifacts",
            "",
            f"- Results: `runs/{state.get('run_id')}/results.jsonl`",
            f"- Summary: `runs/{state.get('run_id')}/summary.json`",
            f"- Per-group logs and backend outputs: `runs/{state.get('run_id')}/groups/`",
            "",
            "## Resume",
            "",
            "```bash",
            f"python -m hqbd_bench run {manifest.name} --run-id {state.get('run_id')} --resume",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def _pct(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{100.0 * float(value):.2f}%"


def outcomes_from_run(run_paths: RunPaths) -> list[GroupOutcome]:
    """Reconstruct group outcomes from a finished or partial run directory."""

    state = read_json(run_paths.run_json)
    selection_rows = {row["hqbd_id"]: row for row in read_jsonl(run_paths.selection_jsonl)}
    outcomes: list[GroupOutcome] = []
    for group in state.get("groups") or []:
        group_id = str(group.get("id"))
        input_path = run_paths.group_input(group_id)
        native_rows = read_jsonl(input_path) if input_path.is_file() else []
        native_by_id = {str(row.get("id")): row for row in native_rows}
        cases: list[CaseRef] = []
        for case_id in group.get("cases") or []:
            row = selection_rows.get(case_id)
            if row is None:
                continue
            native = native_by_id.get(case_id) or {}
            cases.append(CaseRef(hqbd_id=case_id, row=row, native=native))
        stages = {
            str(stage_id): str(record.get("status") or "unknown")
            for stage_id, record in (group.get("stages") or {}).items()
        }
        outcomes.append(
            GroupOutcome(
                group_id=group_id,
                cases=cases,
                status=str(group.get("status") or "unknown"),
                stages=stages,
                group_dir=run_paths.group_dir(group_id),
                input_path=input_path,
            )
        )
    return outcomes


def options_from_state(state: dict[str, Any]) -> ExecutionOptions:
    resolved = state.get("resolved") or {}
    return ExecutionOptions(
        mode=resolved.get("mode"),
        agent=resolved.get("agent"),
        model=resolved.get("model"),
        agent_kwargs=[tuple(item) for item in resolved.get("agent_kwargs") or []],
        agent_env=list(resolved.get("agent_env") or []),
        threads=int(resolved.get("threads") or 1),
        concurrency=int(resolved.get("concurrency") or 1),
        attempts=int(resolved.get("attempts") or 1),
        retries=int(resolved.get("retries") or 2),
        timeout_s=resolved.get("timeout_s"),
        env={},
        allow_local_build=bool(resolved.get("allow_local_build")),
        allow_golden=bool(resolved.get("allow_golden")),
        force_agentic=bool(resolved.get("force_agentic")),
        force_copilot=bool(resolved.get("force_copilot")),
    )


def selection_from_state(run_paths: RunPaths, state: dict[str, Any]) -> Selection:
    rows = read_jsonl(run_paths.selection_jsonl)
    native_by_id: dict[str, dict[str, Any]] = {}
    for group in state.get("groups") or []:
        input_path = run_paths.group_input(str(group.get("id")))
        if not input_path.is_file():
            continue
        for native in read_jsonl(input_path):
            if native.get("id") is not None:
                native_by_id[str(native["id"])] = native
    cases = [
        CaseRef(
            hqbd_id=str(row.get("hqbd_id")),
            row=row,
            native=native_by_id.get(str(row.get("hqbd_id")), {}),
        )
        for row in rows
        if row.get("hqbd_id")
    ]
    return Selection(
        cases=cases,
        rows=rows,
        sha256=str((state.get("selection") or {}).get("sha256") or ""),
        filters=(state.get("selection") or {}).get("filters") or {},
    )
