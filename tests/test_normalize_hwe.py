from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hqbd_bench.adapters.hwe import HweAdapter
from hqbd_bench.io import write_json
from hqbd_bench.model import CaseRef, GroupOutcome
from hqbd_bench.paths import RunPaths


class HweNormalizeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.run_paths = RunPaths(self.root, "run")
        self.group_dir = self.run_paths.group_dir("lowrisc__ibex")
        self.group_dir.mkdir(parents=True)
        self.cases = []
        for number in (1, 2, 3, 4):
            self.cases.append(
                CaseRef(
                    hqbd_id=f"hqbd_hwe_ibex_{number}",
                    row={
                        "source": "HWE-bench",
                        "native_id": f"lowRISC/ibex#{number}",
                        "representation": "hwe_native",
                        "task_type": "bug_repair",
                        "docker_image": "ghcr.io/pku-liang/lowrisc_m_ibex:pr-1",
                    },
                    native={
                        "id": f"hqbd_hwe_ibex_{number}",
                        "org": "lowRISC",
                        "repo": "ibex",
                        "number": number,
                    },
                )
            )
        self.outcome = GroupOutcome(
            group_id="lowrisc__ibex",
            cases=self.cases,
            status="ok",
            stages={},
            group_dir=self.group_dir,
            input_path=self.group_dir / "inputs" / "dataset.jsonl",
        )
        self.ctx = type(
            "Ctx",
            (),
            {"run_id": "run", "options": type("O", (), {"agent": "a", "model": "m"})(), "run_paths": self.run_paths},
        )()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_artifacts(self) -> None:
        write_json(
            self.group_dir / "eval" / "final_report.json",
            {
                "resolved_ids": ["lowRISC/ibex:pr-1"],
                "unresolved_ids": ["lowRISC/ibex:pr-2"],
                "empty_patch_ids": ["lowRISC/ibex:pr-3"],
                "error_ids": ["lowRISC/ibex:pr-4"],
            },
        )
        write_json(
            self.group_dir / "patches" / "collection_summary.json",
            [
                {"instance_id": "lowRISC/ibex:pr-1", "has_patch": True},
                {"instance_id": "lowRISC/ibex:pr-2", "has_patch": True},
                {"instance_id": "lowRISC/ibex:pr-3", "has_patch": False},
                {"instance_id": "lowRISC/ibex:pr-4", "has_patch": True, "error": "trial failed"},
            ],
        )
        report = (
            self.group_dir
            / "eval_workdir"
            / "lowrisc"
            / "ibex"
            / "evals"
            / "pr-1"
            / "report.json"
        )
        write_json(
            report,
            {
                "valid": True,
                "fix_patch_result": {
                    "all_count": 3,
                    "passed_count": 3,
                    "failed_count": 0,
                },
            },
        )

    def test_status_mapping_and_artifacts(self) -> None:
        self._write_artifacts()
        collected = HweAdapter().collect(
            None, [self.outcome], self.ctx  # type: ignore[arg-type]
        )
        by_id = {row["hqbd_id"]: row for row in collected.results}
        self.assertEqual(by_id["hqbd_hwe_ibex_1"]["status"], "resolved")
        self.assertEqual(by_id["hqbd_hwe_ibex_2"]["status"], "unresolved")
        self.assertEqual(by_id["hqbd_hwe_ibex_3"]["status"], "no_patch")
        self.assertEqual(by_id["hqbd_hwe_ibex_4"]["status"], "error")
        self.assertEqual(by_id["hqbd_hwe_ibex_1"]["tests_total"], 3)
        self.assertEqual(by_id["hqbd_hwe_ibex_1"]["tests_passed"], 3)
        self.assertIn("lowrisc/ibex/evals/pr-1/report.json", by_id["hqbd_hwe_ibex_1"]["artifacts"]["report"])
        self.assertEqual(collected.unmatched, [])

    def test_missing_artifacts_are_missing(self) -> None:
        collected = HweAdapter().collect(
            None, [self.outcome], self.ctx  # type: ignore[arg-type]
        )
        self.assertEqual({row["status"] for row in collected.results}, {"missing"})
        self.assertTrue(
            all(row["error_category"] == "no_result" for row in collected.results)
        )

    def test_summary_only_no_patch(self) -> None:
        write_json(
            self.group_dir / "patches" / "collection_summary.json",
            [
                {"instance_id": "lowRISC/ibex:pr-1", "has_patch": False},
                {"instance_id": "lowRISC/ibex:pr-2", "has_patch": False},
                {"instance_id": "lowRISC/ibex:pr-3", "has_patch": False},
                {"instance_id": "lowRISC/ibex:pr-4", "has_patch": False},
            ],
        )
        collected = HweAdapter().collect(
            None, [self.outcome], self.ctx  # type: ignore[arg-type]
        )
        self.assertEqual({row["status"] for row in collected.results}, {"no_patch"})


if __name__ == "__main__":
    unittest.main()
