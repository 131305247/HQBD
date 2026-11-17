from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

from hqbd_bench.adapters.hwe import HweAdapter
from hqbd_bench.model import ExecutionOptions
from hqbd_bench.paths import RunPaths
from hqbd_bench.registry import load_manifest
from hqbd_bench.runner import build_context
from hqbd_bench.selection import SelectionFilters, select_cases
from tests.support import build_dataset


class HwePlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        rows = [
            {"hqbd_id": "hqbd_hwe_ibex_1", "source": "HWE-bench"},
            {"hqbd_id": "hqbd_hwe_ibex_2", "source": "HWE-bench"},
            {"hqbd_id": "hqbd_hwe_cva6_1", "source": "HWE-bench"},
        ]
        natives = [
            {"id": "hqbd_hwe_ibex_1", "org": "lowRISC", "repo": "ibex", "number": 1},
            {"id": "hqbd_hwe_ibex_2", "org": "lowRISC", "repo": "ibex", "number": 2},
            {"id": "hqbd_hwe_cva6_1", "org": "openhwgroup", "repo": "cva6", "number": 1},
        ]
        build_dataset(
            self.root,
            name="hwe",
            adapter="hwe",
            rows=rows,
            natives=natives,
            backend={
                "root": ".",
                "python": [sys.executable],
                "harbor": ["harbor"],
                "images": {"registry": "ghcr.io/pku-liang", "mode": "direct"},
            },
        )
        self.manifest = load_manifest(self.root, "hwe")
        self.adapter = HweAdapter()
        self.selection = select_cases(
            self.root, self.manifest, SelectionFilters()
        )
        self.run_paths = RunPaths(self.root, "run")

    def tearDown(self) -> None:
        os.environ.pop("HWE_SECRET", None)
        self.temp.cleanup()

    def test_groups_and_five_stages(self) -> None:
        os.environ["HWE_SECRET"] = "s3cr3t"
        options = ExecutionOptions(
            mode="agent",
            agent="claude-code",
            model="test-model",
            agent_env=["HWE_SECRET"],
            concurrency=2,
            attempts=3,
        )
        ctx = build_context(
            self.root,
            self.manifest,
            self.selection,
            options,
            self.run_paths,
            self.adapter,
        )
        plans = self.adapter.plan(self.manifest, self.selection, ctx)
        self.assertEqual([plan.id for plan in plans], ["lowrisc__ibex", "openhwgroup__cva6"])
        ibex = plans[0]
        self.assertEqual(
            [stage.id for stage in ibex.stages],
            ["pull-images", "adapter", "harbor", "verify", "evaluate"],
        )
        harbor = next(stage for stage in ibex.stages if stage.id == "harbor")
        actual = harbor.commands[0].argv
        display = harbor.commands[0].safe_argv()
        self.assertIn("HWE_SECRET=s3cr3t", actual)
        self.assertIn("HWE_SECRET=***", display)
        self.assertNotIn("s3cr3t", " ".join(display))
        self.assertEqual(actual[actual.index("-k") + 1], "3")
        self.assertEqual(actual[actual.index("--n-concurrent") + 1], "2")

        evaluate = next(stage for stage in ibex.stages if stage.id == "evaluate")
        self.assertEqual(evaluate.condition.kind, "nonempty")
        argv = evaluate.commands[0].argv
        workdir = argv[argv.index("--workdir") + 1]
        self.assertTrue(Path(workdir).is_absolute())
        self.assertEqual(len(ibex.cases), 2)

        payload = ibex.to_dict()
        self.assertNotIn("s3cr3t", str(payload))

    def test_local_build_cases_skip_pull(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            rows = [
                {
                    "hqbd_id": "hqbd_hwe_ot_1",
                    "source": "HWE-bench",
                    "runnable": False,
                    "runnability_status": "local_build_required",
                }
            ]
            natives = [
                {
                    "id": "hqbd_hwe_ot_1",
                    "org": "lowRISC",
                    "repo": "opentitan",
                    "number": 1,
                }
            ]
            build_dataset(
                root,
                name="hwe",
                adapter="hwe",
                rows=rows,
                natives=natives,
                backend={"root": ".", "python": [sys.executable]},
            )
            manifest = load_manifest(root, "hwe")
            selection = select_cases(
                root, manifest, SelectionFilters(runnable=False)
            )
            options = ExecutionOptions(mode="agent", agent="a", model="m")
            ctx = build_context(
                root, manifest, selection, options, RunPaths(root, "run"), self.adapter
            )
            plans = self.adapter.plan(manifest, selection, ctx)
            self.assertEqual(len(plans), 1)
            self.assertNotIn(
                "pull-images", [stage.id for stage in plans[0].stages]
            )


if __name__ == "__main__":
    unittest.main()
