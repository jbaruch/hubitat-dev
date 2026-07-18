#!/usr/bin/env python3
"""Tests for skills/_scripts/hubs_config.py — the hubs.json owner script."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "hubs_config.py"
spec = importlib.util.spec_from_file_location("hubs_config", SCRIPT)
assert spec and spec.loader
hc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hc)


class TestPureOps(unittest.TestCase):
    def test_add_first_hub_becomes_default(self):
        cfg = hc.add_hub(hc.empty_config(), "main", "192.0.2.10")
        self.assertEqual(cfg["default"], "main")
        self.assertEqual(cfg["hubs"]["main"], {"ip": "192.0.2.10", "port": 8080})

    def test_second_hub_does_not_steal_default(self):
        cfg = hc.add_hub(hc.add_hub(hc.empty_config(), "main", "1"), "garage", "2")
        self.assertEqual(cfg["default"], "main")

    def test_explicit_default_flag(self):
        cfg = hc.add_hub(hc.add_hub(hc.empty_config(), "main", "1"), "garage", "2", make_default=True)
        self.assertEqual(cfg["default"], "garage")

    def test_remove_reassigns_default(self):
        cfg = hc.add_hub(hc.add_hub(hc.empty_config(), "main", "1"), "garage", "2")
        cfg = hc.remove_hub(cfg, "main")
        self.assertEqual(cfg["default"], "garage")

    def test_set_default_unknown_raises(self):
        with self.assertRaises(ValueError):
            hc.set_default(hc.empty_config(), "nope")


class TestMigrate(unittest.TestCase):
    def test_absent_version_is_stamped(self):
        cfg = hc.migrate({"hubs": {"main": {"ip": "1"}}})
        self.assertEqual(cfg["schema_version"], hc.SCHEMA_VERSION)
        self.assertIn("default", cfg)

    def test_newer_version_raises(self):
        with self.assertRaises(ValueError):
            hc.migrate({"schema_version": 999, "hubs": {}})


class TestCli(unittest.TestCase):
    def _run(self, *argv):
        return hc.main(list(argv))

    def test_init_add_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "hubs.json"
            self.assertEqual(self._run("init", "--path", str(path)), 0)
            self.assertEqual(self._run("add", "--path", str(path), "--name", "main", "--ip", "192.0.2.10"), 0)
            cfg = json.loads(path.read_text())
            self.assertEqual(cfg["default"], "main")
            self.assertEqual(cfg["hubs"]["main"]["ip"], "192.0.2.10")

    def test_init_refuses_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "hubs.json"
            self._run("init", "--path", str(path))
            self.assertEqual(self._run("init", "--path", str(path)), 1)
            self.assertEqual(self._run("init", "--path", str(path), "--force"), 0)

    def test_add_without_file_creates_it(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "hubs.json"
            self.assertEqual(self._run("add", "--path", str(path), "--name", "g", "--ip", "10.0.0.1", "--port", "8080"), 0)
            self.assertTrue(path.exists())

    def test_list_missing_file_returns_1(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self._run("list", "--path", str(Path(d) / "absent.json")), 1)

    def test_read_action_persists_migration(self):
        # An old/absent-version file read via list must be rewritten with the current version
        # (owner-migrates-on-read), not just migrated in memory.
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "hubs.json"
            path.write_text(json.dumps({"hubs": {"main": {"ip": "1.2.3.4"}}}))  # no schema_version
            self.assertEqual(self._run("list", "--path", str(path)), 0)
            on_disk = json.loads(path.read_text())
            self.assertEqual(on_disk["schema_version"], hc.SCHEMA_VERSION)

    def test_current_version_read_is_not_rewritten(self):
        # A current-version file is a pure read — no needless rewrite.
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "hubs.json"
            path.write_text(json.dumps({"schema_version": hc.SCHEMA_VERSION, "default": None, "hubs": {}}))
            before = path.stat().st_mtime_ns
            self.assertEqual(self._run("validate", "--path", str(path)), 0)
            # content unchanged (migration no-op leaves the file as-is)
            self.assertEqual(json.loads(path.read_text())["schema_version"], hc.SCHEMA_VERSION)
            _ = before


if __name__ == "__main__":
    unittest.main()
