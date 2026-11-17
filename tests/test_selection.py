from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hqbd_bench.io import read_jsonl
from hqbd_bench.registry import load_manifest
from hqbd_bench.selection import (
    SelectionFilters,
    load_catalog,
    materialize_inputs,
    select_cases,
)
from hqbd_bench.paths import RunPaths
from tests.support import build_dataset


def _rows() -> list[dict]:
    return [
        {
            "hqbd_id": "hqbd_copilot_a_0001",
            "representation": "cvdp_copilot",
            "task_type": "code_generation",
            "runnable": True,
            "runnability_status": "cvdp_harness",
        },
        {
            "hqbd_id": "hqbd_copilot_a_0002",
            "representation": "cvdp_copilot",
            "task_type": "code_generation",
            "runnable": True,
            "runnability_status": "cvdp_harness",
        },
        {
            "hqbd_id": "hqbd_agentic_heavy_0001",
            "representation": "cvdp_agentic_heavy",
            "task_type": "code_generation",
            "runnable": False,
            "runnability_status": "local_build_required",
        },
        {
            "hqbd_id": "hqbd_copilot_comp_0001",
            "representation": "cvdp_copilot",
            "task_type": "code_comprehension",
            "runnable": False,
            "runnability_status": "comprehension_no_harness",
        },
        {
            "hqbd_id": "hqbd_other_0001",
            "source": "Other",
            "representation": "other",
            "task_type": "code_generation",
            "runnable": True,
            "runnability_status": "other",
        },
    ]


class SelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        build_dataset(
            self.root,
            rows=_rows(),
            select={"source": "Synthetic"},
        )
        self.manifest = load_manifest(self.root, "demo")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_manifest_select_limits_catalog(self) -> None:
        rows = load_catalog(self.root, self.manifest)
        self.assertEqual(len(rows), 4)
        self.assertNotIn("hqbd_other_0001", {row["hqbd_id"] for row in rows})

    def test_default_runnable_and_local_build(self) -> None:
        selection = select_cases(self.root, self.manifest, SelectionFilters())
        self.assertEqual(
            selection.ids, ["hqbd_copilot_a_0001", "hqbd_copilot_a_0002"]
        )
        widened = select_cases(
            self.root,
            self.manifest,
            SelectionFilters(allow_local_build=True),
        )
        self.assertEqual(len(widened.cases), 3)
        everything = select_cases(
            self.root, self.manifest, SelectionFilters(runnable=False)
        )
        self.assertEqual(len(everything.cases), 4)

    def test_filters_ids_where_limit_shard(self) -> None:
        by_id = select_cases(
            self.root,
            self.manifest,
            SelectionFilters(ids=["hqbd_copilot_a_0002"], runnable=False),
        )
        self.assertEqual(by_id.ids, ["hqbd_copilot_a_0002"])
        with self.assertRaises(ValueError):
            select_cases(
                self.root,
                self.manifest,
                SelectionFilters(ids=["hqbd_missing_0001"]),
            )

        where = select_cases(
            self.root,
            self.manifest,
            SelectionFilters(where=[("task_type", "code_generation")]),
        )
        self.assertEqual(len(where.cases), 2)

        limited = select_cases(
            self.root, self.manifest, SelectionFilters(limit=1)
        )
        self.assertEqual(limited.ids, ["hqbd_copilot_a_0001"])

        sharded = select_cases(
            self.root, self.manifest, SelectionFilters(shard=(2, 2))
        )
        self.assertEqual(sharded.ids, ["hqbd_copilot_a_0002"])

    def test_selection_hash_tracks_filters(self) -> None:
        first = select_cases(self.root, self.manifest, SelectionFilters())
        second = select_cases(
            self.root, self.manifest, SelectionFilters(limit=1)
        )
        self.assertNotEqual(first.sha256, second.sha256)

    def test_materialize_inputs_preserves_native_records(self) -> None:
        selection = select_cases(self.root, self.manifest, SelectionFilters())
        run_paths = RunPaths(self.root, "run")
        path = materialize_inputs(run_paths, "group", selection.cases)
        records = read_jsonl(path)
        self.assertEqual(
            [record["id"] for record in records],
            ["hqbd_copilot_a_0001", "hqbd_copilot_a_0002"],
        )
        self.assertEqual(
            run_paths.group_ids("group").read_text(encoding="utf-8").splitlines(),
            ["hqbd_copilot_a_0001", "hqbd_copilot_a_0002"],
        )


if __name__ == "__main__":
    unittest.main()
