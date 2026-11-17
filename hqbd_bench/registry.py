"""Dataset manifests and adapter discovery."""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .io import read_json, sha256_file
from .paths import resolve_path

SCHEMA_VERSION = 1
BUILTIN_ADAPTERS = ("cvdp", "hwe", "generic")


@dataclass
class Manifest:
    path: Path
    data: dict[str, Any]

    @property
    def name(self) -> str:
        return str(self.data.get("name") or self.path.stem)

    @property
    def adapter_spec(self) -> str:
        return str(self.data.get("adapter") or "")

    @property
    def sha256(self) -> str:
        return sha256_file(self.path)

    def section(self, key: str) -> dict[str, Any]:
        value = self.data.get(key)
        return value if isinstance(value, dict) else {}

    def expect(self, key: str) -> Any:
        return self.section("expect").get(key)


def benchmark_dir(root: Path) -> Path:
    return root / "benchmarks"


def list_manifests(root: Path) -> list[Manifest]:
    directory = benchmark_dir(root)
    if not directory.is_dir():
        return []
    manifests: list[Manifest] = []
    for path in sorted(directory.glob("*.json")):
        try:
            manifests.append(load_manifest(root, path.stem))
        except (OSError, ValueError):
            # Broken manifests are surfaced by `dataset validate`, not here.
            continue
    return manifests


def manifest_path(root: Path, name: str) -> Path:
    candidate = Path(name)
    if candidate.suffix == ".json":
        return candidate if candidate.is_absolute() else (root / candidate)
    return benchmark_dir(root) / f"{name}.json"


def load_manifest(root: Path, name: str) -> Manifest:
    path = manifest_path(root, name)
    if not path.is_file():
        raise ValueError(f"unknown dataset manifest: {path}")
    data = read_json(path)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: manifest must be a JSON object")
    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ValueError(
            f"{path}: unsupported schema_version {version!r} (expected {SCHEMA_VERSION})"
        )
    if not data.get("name"):
        raise ValueError(f"{path}: missing 'name'")
    if not data.get("adapter"):
        raise ValueError(f"{path}: missing 'adapter'")
    if not isinstance(data.get("catalog"), dict):
        raise ValueError(f"{path}: missing 'catalog' object")
    return Manifest(path=path, data=data)


def load_adapter(manifest: Manifest) -> Any:
    spec = manifest.adapter_spec
    if spec == "cvdp":
        from .adapters.cvdp import CvdpAdapter

        return CvdpAdapter()
    if spec == "hwe":
        from .adapters.hwe import HweAdapter

        return HweAdapter()
    if spec == "generic":
        from .adapters.generic import GenericAdapter

        return GenericAdapter()
    if ":" in spec:
        module_name, class_name = spec.rsplit(":", 1)
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            raise ValueError(f"cannot import adapter module {module_name!r}: {exc}") from exc
        try:
            adapter_class = getattr(module, class_name)
        except AttributeError as exc:
            raise ValueError(
                f"adapter module {module_name!r} has no class {class_name!r}"
            ) from exc
        return adapter_class()
    raise ValueError(
        f"unknown adapter {spec!r}; use one of {', '.join(BUILTIN_ADAPTERS)} "
        "or a 'module:Class' plugin path"
    )


def backend_root(root: Path, manifest: Manifest) -> Path:
    value = manifest.section("backend").get("root", ".")
    return resolve_path(root, value)


def backend_command(
    root: Path,
    manifest: Manifest,
    key: str,
    override: list[str] | None = None,
) -> list[str]:
    if override:
        return list(override)
    value = manifest.section("backend").get(key)
    if key == "python" and value in (None, ["python"]):
        candidate = _venv_python(backend_root(root, manifest))
        if candidate is not None:
            return [str(candidate)]
        return [str(value[0])] if value else [sys.executable]
    if isinstance(value, list) and value:
        return [str(item) for item in value]
    if key == "python":
        return [sys.executable]
    if key == "harbor":
        return ["harbor"]
    raise ValueError(f"manifest backend.{key} must be a non-empty argv list")


def _venv_python(root: Path) -> Path | None:
    candidates = (
        root / ".venv" / "Scripts" / "python.exe",
        root / ".venv" / "bin" / "python",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None
