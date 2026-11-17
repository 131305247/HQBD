from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from hqbd_bench.adapters.cvdp import CvdpAdapter
from hqbd_bench.adapters.generic import GenericAdapter
from hqbd_bench.adapters.hwe import HweAdapter
from hqbd_bench.registry import list_manifests, load_adapter, load_manifest
from tests.support import build_dataset


class ManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = build_dataset(
            self.root,
            rows=[{"hqbd_id": "hqbd_demo_0001"}],
            select={"source": "Synthetic"},
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_load_manifest_and_hash(self) -> None:
        manifest = load_manifest(self.root, "demo")
        self.assertEqual(manifest.name, "demo")
        self.assertEqual(manifest.adapter_spec, "generic")
        self.assertEqual(len(manifest.sha256), 64)
        self.assertEqual(manifest.expect("records"), None)

    def test_missing_and_invalid_manifests(self) -> None:
        with self.assertRaises(ValueError):
            load_manifest(self.root, "absent")
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        payload["schema_version"] = 99
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_manifest(self.root, "demo")

    def test_list_manifests_skips_broken_files(self) -> None:
        (self.root / "benchmarks" / "broken.json").write_text("{", encoding="utf-8")
        names = [manifest.name for manifest in list_manifests(self.root)]
        self.assertEqual(names, ["demo"])

    def test_builtin_adapters(self) -> None:
        manifest = load_manifest(self.root, "demo")
        self.assertIsInstance(load_adapter(manifest), GenericAdapter)
        manifest.data["adapter"] = "cvdp"
        self.assertIsInstance(load_adapter(manifest), CvdpAdapter)
        manifest.data["adapter"] = "hwe"
        self.assertIsInstance(load_adapter(manifest), HweAdapter)

    def test_plugin_adapter(self) -> None:
        package = self.root / "plugpkg"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "adapter.py").write_text(
            "class Adapter:\n"
            "    name = 'plug'\n"
            "    version = '1'\n"
            "    capabilities = {}\n"
            "    def validate(self, manifest, ctx): return []\n"
            "    def doctor(self, manifest, ctx, check_images=False): return []\n"
            "    def validate_options(self, manifest, options): return []\n"
            "    def plan(self, manifest, selection, ctx): return []\n"
            "    def collect(self, manifest, outcomes, ctx):\n"
            "        from hqbd_bench.model import CollectResult\n"
            "        return CollectResult(results=[])\n",
            encoding="utf-8",
        )
        sys.path.insert(0, str(self.root))
        try:
            manifest = load_manifest(self.root, "demo")
            manifest.data["adapter"] = "plugpkg.adapter:Adapter"
            adapter = load_adapter(manifest)
            self.assertEqual(adapter.name, "plug")
        finally:
            sys.path.remove(str(self.root))
            for module in list(sys.modules):
                if module.startswith("plugpkg"):
                    del sys.modules[module]


if __name__ == "__main__":
    unittest.main()
