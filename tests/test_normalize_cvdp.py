from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hqbd_bench.adapters.cvdp import _normalize_case
from hqbd_bench.model import CaseRef, GroupOutcome


class FakePaths:
    @staticmethod
    def relative(path: Path) -> str:
        return str(path)

    @staticmethod
    def group_log(group_id: str, stage_id: str) -> Path:
        return Path(group_id) / "logs" / f"{stage_id}.log"


class FakeContext:
    run_id = "run"
    run_paths = FakePaths()


class CvdpNormalizeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.group_dir = Path(self.temp.name) / "group"
        self.group_dir.mkdir()
        self.case = CaseRef(
            hqbd_id="hqbd_copilot_demo_0001",
            row={
                "source": "CVDP",
                "native_id": "cvdp_copilot_demo_0001",
                "representation": "cvdp_copilot",
                "task_type": "code_generation",
            },
            native={"id": "hqbd_copilot_demo_0001"},
        )
        self.outcome = GroupOutcome(
            group_id="g",
            cases=[self.case],
            status="ok",
            stages={},
            group_dir=self.group_dir,
            input_path=self.group_dir / "inputs" / "dataset.jsonl",
        )
        self.ctx = FakeContext()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _normalize(self, raw):
        return _normalize_case(self.case, raw, self.outcome, self.ctx)

    def test_all_tests_pass(self) -> None:
        row = self._normalize(
            {"tests": [{"result": 0}, {"result": 0}], "errors": 0}
        )
        self.assertEqual(row["status"], "resolved")
        self.assertTrue(row["resolved"])
        self.assertEqual(row["score"], 1.0)
        self.assertEqual((row["tests_passed"], row["tests_failed"]), (2, 0))

    def test_failed_test_is_unresolved(self) -> None:
        row = self._normalize(
            {"tests": [{"result": 0}, {"result": 1, "error_msg": "mismatch"}]}
        )
        self.assertEqual(row["status"], "unresolved")
        self.assertEqual(row["score"], 0.0)
        self.assertEqual(row["error_message"], "mismatch")

    def test_no_patch(self) -> None:
        row = self._normalize({"agent_status": "completed_no_patch", "tests": []})
        self.assertEqual(row["status"], "no_patch")
        self.assertEqual(row["error_category"], "agent_no_patch")

    def test_harness_errors_take_precedence(self) -> None:
        row = self._normalize({"tests": [{"result": 0}], "errors": 2})
        self.assertEqual(row["status"], "error")
        self.assertEqual(row["error_category"], "harness_error")

    def test_timeout_and_missing(self) -> None:
        row = self._normalize({"agent_status": "timeout"})
        self.assertEqual(row["status"], "timeout")
        missing = self._normalize(None)
        self.assertEqual(missing["status"], "missing")
        self.assertEqual(missing["error_category"], "no_result")

    def test_forbidden_keys_are_not_copied(self) -> None:
        row = self._normalize(
            {
                "tests": [{"result": 0}],
                "fix_patch": "SECRET",
                "tb_script": "SECRET",
            }
        )
        self.assertNotIn("fix_patch", row)
        self.assertNotIn("tb_script", row)


if __name__ == "__main__":
    unittest.main()
