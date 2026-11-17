"""Adapter protocol.

An adapter turns a selection into per-group stage plans and normalizes the
backend's artifacts back into the common result schema.  It never scores
anything itself.
"""

from __future__ import annotations

from typing import Any, Protocol

from ..model import (
    Check,
    ExecutionOptions,
    GroupOutcome,
    GroupPlan,
    PlanContext,
    Selection,
)
from ..registry import Manifest


class Adapter(Protocol):
    name: str
    version: str
    capabilities: dict[str, Any]

    def validate(self, manifest: Manifest, ctx: PlanContext) -> list[Check]:
        ...

    def doctor(
        self, manifest: Manifest, ctx: PlanContext, check_images: bool = False
    ) -> list[Check]:
        ...

    def validate_options(
        self, manifest: Manifest, options: ExecutionOptions
    ) -> list[str]:
        ...

    def plan(
        self, manifest: Manifest, selection: Selection, ctx: PlanContext
    ) -> list[GroupPlan]:
        ...

    def collect(
        self, manifest: Manifest, outcomes: list[GroupOutcome], ctx: PlanContext
    ) -> Any:
        ...


class BaseAdapter:
    name = "base"
    version = "1"
    capabilities: dict[str, Any] = {}

    def validate(self, manifest: Manifest, ctx: PlanContext) -> list[Check]:
        return []

    def doctor(
        self, manifest: Manifest, ctx: PlanContext, check_images: bool = False
    ) -> list[Check]:
        return []

    def validate_options(
        self, manifest: Manifest, options: ExecutionOptions
    ) -> list[str]:
        return []

    def plan(self, manifest: Manifest, selection: Selection, ctx: PlanContext):
        raise NotImplementedError

    def collect(self, manifest: Manifest, outcomes: list[GroupOutcome], ctx: PlanContext):
        raise NotImplementedError


def check(name: str, status: str, message: str = "") -> Check:
    return Check(name=name, status=status, message=message)


def require(value: Any, label: str) -> str | None:
    if value is None or value == "":
        return f"{label} is required"
    return None
