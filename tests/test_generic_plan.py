from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hqbd_bench.io import read_json, read_jsonl
from hqbd_bench.model import ExecutionOptions
from hqbd_bench.paths import RunPaths
from hqbd_bench.registry import load_adapter, load_manifest
from hqbd_bench.results import write_outputs
from hqbd_bench.runner import build_context, execute
from hqbd_bench.selection import SelectionFilters, select_cases
from tests.support import RESULT_SCRIPT, generic_manifest


class GenericEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        rows = [
            {"hqbd_id": "hqbd_demo_0001"},
            {"hqbd_id": "hqbd_demo_0002"},
        ]
        generic_manifest(self.root, rows=rows, script=RESULT_SCRIPT)
        self.manifest = load_manifest(self.root, "demo")
        self.adapter = load_adapter(self.manifest)
        self.selection = select_cases(
            self.root, self.manifest, SelectionFilters()
        )
        self.options = ExecutionOptions()
        self.run_paths = RunPaths(self.root, "run")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_run_collect_and_report(self) -> None:
        result = execute(
            root=self.root,
            manifest=self.manifest,
            adapter=self.adapter,
            selection=self.selection,
            options=self.options,
            run_paths=self.run_paths,
        )
        self.assertEqual(result.status, "completed")
        self.assertEqual(len(result.outcomes), 1)
        self.assertEqual(result.outcomes[0].stages, {"run": "ok"})

        ctx = build_context(
            self.root,
            self.manifest,
            self.selection,
            self.options,
            self.run_paths,
            self.adapter,
        )
        collected = self.adapter.collect(self.manifest, result.outcomes, ctx)
        summary = write_outputs(
            self.run_paths,
            manifest=self.manifest,
            state=result.state,
            outcomes=result.outcomes,
            collected=collected,
            command_text="demo",
        )
        results = read_jsonl(self.run_paths.results_jsonl)
        self.assertEqual(len(results), 2)
        self.assertEqual({row["status"] for row in results}, {"resolved"})
        self.assertEqual(summary["counts"]["selected"], 2)
        self.assertEqual(summary["metrics"]["resolve_rate_selected"], 1.0)
        self.assertTrue(self.run_paths.report_md.is_file())
        self.assertEqual(
            read_json(self.run_paths.summary_json)["counts"]["resolved"], 2
        )



if __name__ == "__main__":
    unittest.main()
