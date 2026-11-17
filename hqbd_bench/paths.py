"""Path resolution and the per-run directory layout."""

from __future__ import annotations

import os
import re
from pathlib import Path


def resolve_root(value: str | os.PathLike[str] | None) -> Path:
    if value:
        root = Path(value).expanduser()
    else:
        root = Path(__file__).resolve().parent.parent
    return root.resolve()


def resolve_path(root: Path, value: str | os.PathLike[str]) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (root / path).resolve()


def ensure_within(root: Path, path: Path, label: str = "path") -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} escapes the HQBD root: {resolved}") from exc
    return resolved


def slugify(value: str, fallback: str = "group") -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    slug = re.sub(r"-{2,}", "-", slug).strip("-._")
    return slug.lower() or fallback


class RunPaths:
    def __init__(self, root: Path, run_id: str):
        self.root = root
        self.run_id = run_id
        self.run_dir = root / "runs" / run_id

    @property
    def run_json(self) -> Path:
        return self.run_dir / "run.json"

    @property
    def selection_jsonl(self) -> Path:
        return self.run_dir / "selection.jsonl"

    @property
    def events_jsonl(self) -> Path:
        return self.run_dir / "events.jsonl"

    @property
    def results_jsonl(self) -> Path:
        return self.run_dir / "results.jsonl"

    @property
    def summary_json(self) -> Path:
        return self.run_dir / "summary.json"

    @property
    def report_md(self) -> Path:
        return self.run_dir / "report.md"

    @property
    def lock_file(self) -> Path:
        return self.run_dir / ".lock"

    @property
    def groups_dir(self) -> Path:
        return self.run_dir / "groups"

    def group_dir(self, group_id: str) -> Path:
        return self.groups_dir / group_id

    def group_input(self, group_id: str) -> Path:
        return self.group_dir(group_id) / "inputs" / "dataset.jsonl"

    def group_ids(self, group_id: str) -> Path:
        return self.group_dir(group_id) / "inputs" / "ids.txt"

    def group_plan(self, group_id: str) -> Path:
        return self.group_dir(group_id) / "plan.json"

    def group_log(self, group_id: str, stage_id: str) -> Path:
        return self.group_dir(group_id) / "logs" / f"{stage_id}.log"

    def relative(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.run_dir.resolve()).as_posix()
        except ValueError:
            return str(path)
