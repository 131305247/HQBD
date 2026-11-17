"""Small synthetic datasets for tests (no Docker, no real data)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from hqbd_bench.io import write_json, write_jsonl


def build_dataset(
    root: Path,
    *,
    name: str = "demo",
    adapter: str = "generic",
    source: str = "Synthetic",
    rows: list[dict[str, Any]],
    natives: list[dict[str, Any]] | None = None,
    select: dict[str, Any] | None = None,
    defaults: dict[str, Any] | None = None,
    backend: dict[str, Any] | None = None,
    expect: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    """Write a manifest, a unified-style index, and a native JSONL file."""

    (root / "benchmarks").mkdir(parents=True, exist_ok=True)
    (root / "data").mkdir(parents=True, exist_ok=True)
    native_name = f"{name}.jsonl"
    native_path = root / "data" / native_name

    if natives is None:
        natives = [{"id": row["hqbd_id"]} for row in rows]
    if len(natives) != len(rows):
        raise ValueError("rows and natives must have the same length")
    for index, (row, native) in enumerate(zip(rows, natives), start=1):
        native.setdefault("id", row["hqbd_id"])
        row.setdefault("source", source)
        row.setdefault("source_file", native_name)
        row.setdefault("source_line", index)
        row.setdefault("representation", "synthetic")
        row.setdefault("task_type", "test")
        row.setdefault("runnable", True)
        row.setdefault("runnability_status", "generic")
    write_jsonl(native_path, natives)
    write_jsonl(root / "data" / "index.jsonl", rows)

    data: dict[str, Any] = {
        "schema_version": 1,
        "name": name,
        "description": "synthetic test dataset",
        "adapter": adapter,
        "catalog": {
            "path": "data/index.jsonl",
            "id_field": "hqbd_id",
            "native_base": "data",
            "native_file_field": "source_file",
            "native_line_field": "source_line",
        },
        "defaults": {"runnable": True, "mode": "llm"},
        "backend": backend
        or {"root": ".", "python": [sys.executable]},
        "expect": expect or {},
    }
    if select:
        data["select"] = select
    if extra:
        data.update(extra)
    path = root / "benchmarks" / f"{name}.json"
    write_json(path, data)
    return path


def generic_manifest(
    root: Path,
    *,
    name: str = "demo",
    rows: list[dict[str, Any]],
    script: str,
    expected_output: bool = True,
) -> Path:
    script_path = root / "backend" / "runner.py"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(script, encoding="utf-8", newline="\n")
    extra = {
        "generic": {
            "group_by": ["source"],
            "stages": [
                {
                    "id": "run",
                    "commands": [
                        {
                            "argv": [
                                sys.executable,
                                str(script_path),
                                "--dataset",
                                "{dataset}",
                                "--output",
                                "{group}/results.json",
                            ],
                            "cwd": "{backend_root}",
                        }
                    ],
                    "expected_outputs": (
                        ["{group}/results.json"] if expected_output else []
                    ),
                }
            ],
            "collect": {
                "results_path": "{group}/results.json",
                "id_field": "id",
                "status_field": "status",
                "status_map": {"pass": "resolved", "fail": "unresolved"},
            },
        }
    }
    return build_dataset(root, name=name, rows=rows, extra=extra)


RESULT_SCRIPT = """
import argparse, json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--dataset", required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()

counter = Path(__file__).parent / "counter.txt"
count = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(count))

records = []
for line in Path(args.dataset).read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    row = json.loads(line)
    records.append({"id": row["id"], "status": "pass"})
Path(args.output).parent.mkdir(parents=True, exist_ok=True)
Path(args.output).write_text(json.dumps({"results": records}), encoding="utf-8")
"""
