"""Core datatypes shared by adapters and the runner."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class CaseRef:
    """One selected case: its catalog row plus its native record."""

    hqbd_id: str
    row: dict[str, Any]
    native: dict[str, Any]
    native_path: Path | None = None

    @property
    def source(self) -> str:
        return str(self.row.get("source") or "")

    @property
    def native_id(self) -> str:
        return str(self.row.get("native_id") or "")

    @property
    def representation(self) -> str:
        return str(self.row.get("representation") or "")

    @property
    def task_type(self) -> str:
        return str(self.row.get("task_type") or "")

    @property
    def runnability_status(self) -> str:
        return str(self.row.get("runnability_status") or "")


@dataclass
class Selection:
    cases: list[CaseRef]
    rows: list[dict[str, Any]]
    sha256: str
    filters: dict[str, Any]

    @property
    def ids(self) -> list[str]:
        return [case.hqbd_id for case in self.cases]


@dataclass
class Command:
    """One external process invocation.

    ``display_argv`` is what gets logged and fingerprinted.  Use it whenever
    ``argv`` contains a secret (for example Harbor ``--ae KEY=value``).
    """

    argv: list[str]
    display_argv: list[str] | None = None
    cwd: Path | None = None
    env: dict[str, str] = field(default_factory=dict)
    timeout_s: float | None = None

    def safe_argv(self) -> list[str]:
        return self.display_argv if self.display_argv is not None else self.argv


@dataclass
class Condition:
    kind: str  # exists | nonempty | missing
    path: Path

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "path": str(self.path)}


@dataclass
class Completion:
    """How to decide that a stage's outputs are complete."""

    kind: str = "all_exist"  # all_exist | json_keys | nonempty_file
    path: Path | None = None
    keys: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "path": str(self.path) if self.path else None,
            "keys": sorted(self.keys) if self.keys else [],
        }


@dataclass
class Stage:
    id: str
    commands: list[Command]
    depends_on: list[str] = field(default_factory=list)
    optional: bool = False
    collect_on_failure: bool = False
    condition: Condition | None = None
    expected_outputs: list[Path] = field(default_factory=list)
    completion: Completion | None = None
    resume: str = "rerun"  # rerun | skip_if_complete | skip_if_outputs
    reset_on_change: list[Path] = field(default_factory=list)
    reset_paths: list[Path] = field(default_factory=list)
    before_run: Callable[[], None] | None = field(
        default=None, repr=False, compare=False
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "depends_on": self.depends_on,
            "optional": self.optional,
            "collect_on_failure": self.collect_on_failure,
            "condition": self.condition.to_dict() if self.condition else None,
            "expected_outputs": [str(path) for path in self.expected_outputs],
            "completion": self.completion.to_dict() if self.completion else None,
            "resume": self.resume,
            "reset_on_change": [str(path) for path in self.reset_on_change],
            "reset_paths": [str(path) for path in self.reset_paths],
            "commands": [
                {
                    "argv": command.safe_argv(),
                    "cwd": str(command.cwd) if command.cwd else None,
                    "env_keys": sorted(command.env),
                    "timeout_s": command.timeout_s,
                }
                for command in self.commands
            ],
        }


@dataclass
class GroupPlan:
    id: str
    cases: list[CaseRef]
    stages: list[Stage]
    framework: str
    input_path: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "framework": self.framework,
            "input_path": str(self.input_path),
            "cases": [case.hqbd_id for case in self.cases],
            "stages": [stage.to_dict() for stage in self.stages],
        }


@dataclass
class ExecutionOptions:
    mode: str | None = None
    agent: str | None = None
    model: str | None = None
    agent_kwargs: list[tuple[str, str]] = field(default_factory=list)
    agent_env: list[str] = field(default_factory=list)
    threads: int = 1
    concurrency: int = 1
    attempts: int = 1
    retries: int = 2
    timeout_s: float | None = None
    env: dict[str, str] = field(default_factory=dict)
    allow_local_build: bool = False
    allow_golden: bool = False
    force_agentic: bool = False
    force_copilot: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "agent": self.agent,
            "model": self.model,
            "agent_kwargs": [list(item) for item in self.agent_kwargs],
            "agent_env": list(self.agent_env),
            "threads": self.threads,
            "concurrency": self.concurrency,
            "attempts": self.attempts,
            "retries": self.retries,
            "timeout_s": self.timeout_s,
            "env_keys": sorted(self.env),
            "allow_local_build": self.allow_local_build,
            "allow_golden": self.allow_golden,
            "force_agentic": self.force_agentic,
            "force_copilot": self.force_copilot,
        }


@dataclass
class PlanContext:
    root: Path
    run_paths: Any  # RunPaths, kept loose to avoid a circular import
    run_id: str
    options: ExecutionOptions
    manifest: Any  # Manifest
    backend_root: Path
    python_argv: list[str]
    harbor_argv: list[str]
    adapter_name: str
    adapter_version: str
    tool_version: str
    selection_sha256: str


@dataclass
class GroupOutcome:
    group_id: str
    cases: list[CaseRef]
    status: str
    stages: dict[str, str]
    group_dir: Path
    input_path: Path


@dataclass
class CollectResult:
    results: list[dict[str, Any]]
    unmatched: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Check:
    name: str
    status: str  # ok | warn | error
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status, "message": self.message}
