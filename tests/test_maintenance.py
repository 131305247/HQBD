from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hqbd_bench.cli import _validation_context
from hqbd_bench.io import read_jsonl, write_jsonl
from hqbd_bench.maintenance import create_dataset, validate_dataset
from hqbd_bench.registry import load_adapter, load_manifest
from tests.support import RESULT_SCRIPT, generic_manifest


class MaintenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        generic_manifest(
            self.root,
            rows=[{"hqbd_id": "hqbd_demo_0001"}, {"hqbd_id": "hqbd_demo_0002"}],
            script=RESULT_SCRIPT,
        )
        self.manifest = load_manifest(self.root, "demo")
        self.manifest.data["expect"] = {"records": 2}

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _checks(self, **kwargs):
        adapter = load_adapter(self.manifest)
        ctx = _validation_context(self.root, self.manifest, adapter)
        return validate_dataset(ctx, **kwargs)

    def test_valid_dataset(self) -> None:
        checks = self._checks()
        self.assertFalse([item for item in checks if item.status == "error"])
        names = {item.name for item in checks}
        self.assertIn("catalog.pointers", names)
        self.assertIn("generic.stages", names)

    def test_expect_mismatch_and_duplicate_ids(self) -> None:
        self.manifest.data["expect"] = {"records": 3}
        index = self.root / "data" / "index.jsonl"
        rows = read_jsonl(index)
        rows[1]["hqbd_id"] = rows[0]["hqbd_id"]
        write_jsonl(index, rows)
        checks = self._checks()
        errors = {item.name for item in checks if item.status == "error"}
        self.assertIn("catalog.ids", errors)
        self.assertIn("catalog.expect", errors)
        self.assertIn("catalog.pointers", errors)

    def test_pointer_line_mismatch(self) -> None:
        index = self.root / "data" / "index.jsonl"
        rows = read_jsonl(index)
        rows[0]["source_line"] = 2
        write_jsonl(index, rows)
        checks = self._checks()
        pointers = next(item for item in checks if item.name == "catalog.pointers")
        self.assertEqual(pointers.status, "error")

    def test_strict_promotes_warnings(self) -> None:
        self.manifest.data["name"] = "other"
        self.manifest.data["expect"] = {}
        checks = self._checks(strict=True)
        name_check = next(item for item in checks if item.name == "manifest.name")
        self.assertEqual(name_check.status, "error")

    def test_create_dataset(self) -> None:
        path = create_dataset(self.root, "newset", "generic")
        self.assertTrue(path.is_file())
        created = load_manifest(self.root, "newset")
        self.assertEqual(created.name, "newset")
        with self.assertRaises(ValueError):
            create_dataset(self.root, "newset", "generic")
        with self.assertRaises(ValueError):
            create_dataset(self.root, "cvdp", "generic")
        plugin = create_dataset(self.root, "custom", "python")
        self.assertTrue(plugin.is_file())
        self.assertTrue((self.root / "benchmarks" / "plugins" / "custom.py").is_file())
        manifest = load_manifest(self.root, "custom")
        self.assertIn("benchmarks.plugins.custom:", manifest.adapter_spec)


if __name__ == "__main__":
    unittest.main()
