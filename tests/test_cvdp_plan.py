from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from hqbd_bench.adapters.cvdp import CvdpAdapter
from hqbd_bench.io import read_json, write_json
from hqbd_bench.model import ExecutionOptions
from hqbd_bench.paths import RunPaths
from hqbd_bench.registry import load_manifest
from hqbd_bench.runner import build_context
from hqbd_bench.selection import SelectionFilters, select_cases
from tests.support import build_dataset


class CvdpPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        rows = [
            {
                "hqbd_id": "hqbd_copilot_demo_0001",
                "representation": "cvdp_copilot",
                "runnable": True,
                "runnability_status": "cvdp_harness",
            },
            {
                "hqbd_id": "hqbd_agentic_heavy_demo_0002",
                "representation": "cvdp_agentic_heavy",
                "runnable": True,
                "runnability_status": "cvdp_heavy_bundle",
            },
        ]
        natives = [
            {"id": "hqbd_copilot_demo_0001"},
            {"id": "hqbd_agentic_heavy_demo_0002", "context": {"repo": "demo.bundle"}},
        ]
        build_dataset(
            self.root,
            name="cvdp",
            adapter="cvdp",
            rows=rows,
            natives=natives,
            backend={
                "root": ".",
                "python": [sys.executable],
                "heavy_repos_path": "heavy",
            },
        )
        self.manifest = load_manifest(self.root, "cvdp")
        self.adapter = CvdpAdapter()
        self.selection = select_cases(
            self.root, self.manifest, SelectionFilters()
        )
        self.run_paths = RunPaths(self.root, "run")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _context(self, options: ExecutionOptions):
        return build_context(
            self.root,
            self.manifest,
            self.selection,
            options,
            self.run_paths,
            self.adapter,
        )

    def test_groups_by_source_file_and_builds_argv(self) -> None:
        options = ExecutionOptions(mode="llm", model="test-model", threads=3)
        plans = self.adapter.plan(self.manifest, self.selection, self._context(options))
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        self.assertEqual(plan.id, "cvdp")
        self.assertEqual(len(plan.cases), 2)
        self.assertEqual(len(plan.stages), 1)
        stage = plan.stages[0]
        argv = stage.commands[0].argv
        self.assertIn("-f", argv)
        self.assertIn("-p", argv)
        self.assertEqual(argv[argv.index("-t") + 1], "3")
        self.assertEqual(argv[argv.index("-m") + 1], "test-model")
        self.assertIn("-l", argv)
        self.assertEqual(
            stage.completion.keys,
            ["hqbd_copilot_demo_0001", "hqbd_agentic_heavy_demo_0002"],
        )
        self.assertEqual(stage.resume, "skip_if_complete")
        self.assertEqual(
            stage.commands[0].env["CVDP_HEAVY_REPOS_PATH"],
            str((self.root / "heavy").resolve()),
        )

    def test_agent_mode_and_golden_guard(self) -> None:
        options = ExecutionOptions(mode="agent", agent="agent-image")
        plans = self.adapter.plan(self.manifest, self.selection, self._context(options))
        argv = plans[0].stages[0].commands[0].argv
        self.assertEqual(argv[argv.index("-g") + 1], "agent-image")

        golden = ExecutionOptions(mode="golden")
        self.assertEqual(
            self.adapter.validate_options(self.manifest, golden),
            ["--mode golden is only allowed with --allow-golden"],
        )

    def test_partial_raw_result_is_archived(self) -> None:
        options = ExecutionOptions(mode="llm", model="m")
        plan = self.adapter.plan(
            self.manifest, self.selection, self._context(options)
        )[0]
        raw = plan.stages[0].completion.path
        raw.parent.mkdir(parents=True)
        write_json(raw, {"hqbd_copilot_demo_0001": {"tests": []}})
        plan.stages[0].before_run()
        self.assertFalse(raw.exists())
        archived = list(raw.parent.glob("raw_result.partial.*.json"))
        self.assertEqual(len(archived), 1)
        self.assertEqual(read_json(archived[0])["hqbd_copilot_demo_0001"]["tests"], [])


if __name__ == "__main__":
    unittest.main()
