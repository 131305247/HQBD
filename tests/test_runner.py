from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from hqbd_bench.io import write_json
from hqbd_bench.model import ExecutionOptions
from hqbd_bench.paths import RunPaths
from hqbd_bench.registry import load_adapter, load_manifest
from hqbd_bench.runner import RunError, _acquire_lock, execute
from hqbd_bench.selection import SelectionFilters, select_cases
from tests.support import RESULT_SCRIPT, generic_manifest


class RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        generic_manifest(self.root, rows=[{"hqbd_id": "hqbd_demo_0001"}], script=RESULT_SCRIPT)
        self.manifest = load_manifest(self.root, "demo")
        self.manifest.data["generic"]["stages"][0]["resume"] = "skip_if_complete"
        self.adapter = load_adapter(self.manifest)
        self.selection = select_cases(self.root, self.manifest, SelectionFilters())
        self.options = ExecutionOptions()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _execute(self, run_id: str, **kwargs):
        return execute(
            root=self.root,
            manifest=self.manifest,
            adapter=self.adapter,
            selection=self.selection,
            options=self.options,
            run_paths=RunPaths(self.root, run_id),
            **kwargs,
        )

    def test_resume_skips_complete_stage(self) -> None:
        self._execute("run")
        counter = self.root / "backend" / "counter.txt"
        self.assertEqual(counter.read_text(encoding="utf-8"), "1")

        result = self._execute("run", resume=True)
        self.assertEqual(result.status, "completed")
        self.assertEqual(counter.read_text(encoding="utf-8"), "1")
        self.assertEqual(result.outcomes[0].stages["run"], "ok")

    def test_force_rebuild_archives_and_reruns(self) -> None:
        self._execute("run")
        counter = self.root / "backend" / "counter.txt"
        self._execute("run", force_rebuild=True)
        self.assertEqual(counter.read_text(encoding="utf-8"), "2")
        stale = list((self.root / "runs").glob("run.stale.*"))
        self.assertEqual(len(stale), 1)

    def test_existing_run_requires_resume_flag(self) -> None:
        self._execute("run")
        with self.assertRaises(RunError):
            self._execute("run")

    def test_lock_blocks_a_second_process(self) -> None:
        run_paths = RunPaths(self.root, "locked")
        run_paths.run_dir.mkdir(parents=True)
        write_json(run_paths.lock_file, {"pid": os.getpid(), "started_at": "now"})
        with self.assertRaises(RunError):
            _acquire_lock(run_paths)


if __name__ == "__main__":
    unittest.main()
