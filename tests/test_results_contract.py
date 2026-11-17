from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hqbd_bench.io import read_json, read_jsonl
from hqbd_bench.model import CollectResult, GroupOutcome
from hqbd_bench.paths import RunPaths
from hqbd_bench.registry import Manifest
from hqbd_bench.results import write_outputs


class ResultsContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.run_paths = RunPaths(self.root, "run")
        self.run_paths.run_dir.mkdir(parents=True)
        self.manifest = Manifest(self.root / "benchmarks" / "demo.json", {"name": "demo"})
        self.state = {
            "run_id": "run",
            "dataset": "demo",
            "adapter": "generic",
            "status": "completed",
            "started_at": "now",
            "finished_at": "now",
            "selection": {"ids": ["a", "b"], "sha256": "x", "filters": {}},
        }
        self.outcomes = [
            GroupOutcome(
                group_id="g",
                cases=[],
                status="ok",
                stages={"run": "ok"},
                group_dir=self.run_paths.group_dir("g"),
                input_path=self.run_paths.group_input("g"),
            )
        ]

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_forbidden_material_never_reaches_artifacts(self) -> None:
        results = [
            {
                "hqbd_id": "a",
                "status": "resolved",
                "score": 1.0,
                "source": "HWE-bench",
                "representation": "hwe_native",
                "task_type": "bug_repair",
                "group_id": "g",
                "fix_patch": "SECRET PATCH",
                "tb_script": "SECRET TEST",
                "nested": {"test_patch": "SECRET", "keep": "ok"},
            },
            {
                "hqbd_id": "b",
                "status": "no_patch",
                "score": 0.0,
                "source": "HWE-bench",
                "representation": "hwe_native",
                "task_type": "bug_repair",
                "group_id": "g",
            },
        ]
        summary = write_outputs(
            self.run_paths,
            manifest=self.manifest,
            state=self.state,
            outcomes=self.outcomes,
            collected=CollectResult(
                results=results,
                unmatched=[{"group_id": "g", "fix_patch": "SECRET"}],
            ),
            command_text="python -m hqbd_bench run demo",
        )
        rows = read_jsonl(self.run_paths.results_jsonl)
        self.assertEqual(rows[0]["nested"], {"keep": "ok"})
        for path in (
            self.run_paths.results_jsonl,
            self.run_paths.summary_json,
            self.run_paths.report_md,
        ):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("SECRET", text)
        self.assertEqual(summary["counts"]["resolved"], 1)
        self.assertEqual(summary["counts"]["no_patch"], 1)
        self.assertEqual(summary["counts"]["submitted"], 1)
        self.assertEqual(summary["metrics"]["resolve_rate_selected"], 0.5)
        self.assertEqual(summary["metrics"]["resolve_rate_submitted"], 1.0)
        self.assertEqual(read_json(self.run_paths.summary_json)["schema_version"], "1.0")


if __name__ == "__main__":
    unittest.main()
