#!/usr/bin/env python3
"""Tests for the hub_pull.py and hub_deploy.py CLIs, with an injected fake client."""

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, DIR / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hub_pull = _load("hub_pull")
hub_deploy = _load("hub_deploy")


class FakeClient:
    def __init__(self, base):
        self.base = base
        self.deployed = None

    def find_id(self, kind, name):
        return 42 if name == "Known" else None

    def pull(self, kind, code_id):
        return {"id": code_id, "name": "Known", "version": 7, "source": "def x() {}"}

    def deploy(self, kind, source, code_id=None):
        self.deployed = (kind, source, code_id)
        if code_id is None:
            return {"action": "create", "id": 99}
        return {"action": "update", "id": code_id, "version": 7}


def run(mod, argv, factory):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = mod.main(argv, client_factory=factory)
    return rc, out.getvalue(), err.getvalue()


class TestPull(unittest.TestCase):
    def test_pull_by_id_prints_metadata_and_source(self):
        rc, out, _ = run(hub_pull, ["--kind", "driver", "--id", "5", "--ip", "1.2.3.4"], FakeClient)
        self.assertEqual(rc, 0)
        meta = json.loads(out)
        self.assertEqual((meta["id"], meta["version"], meta["source"]), (5, 7, "def x() {}"))

    def test_pull_by_name_writes_out_file(self):
        with tempfile.TemporaryDirectory() as d:
            outfile = Path(d) / "x.groovy"
            rc, out, _ = run(hub_pull,
                             ["--kind", "driver", "--name", "Known", "--ip", "1.2.3.4", "--out", str(outfile)],
                             FakeClient)
            self.assertEqual(rc, 0)
            self.assertEqual(outfile.read_text(), "def x() {}")
            self.assertEqual(json.loads(out)["out"], str(outfile))

    def test_pull_unknown_name_returns_1(self):
        rc, _, err = run(hub_pull, ["--kind", "driver", "--name", "Nope", "--ip", "1.2.3.4"], FakeClient)
        self.assertEqual(rc, 1)
        self.assertIn("no driver named", err)


class TestDeploy(unittest.TestCase):
    def _src(self, d):
        f = Path(d) / "s.groovy"
        f.write_text("def on() {}")
        return str(f)

    def test_create_when_no_id_or_name(self):
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(hub_deploy, ["--kind", "driver", "--source", self._src(d), "--ip", "1.2.3.4"], FakeClient)
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out), {"kind": "driver", "action": "create", "id": 99})

    def test_update_when_name_matches(self):
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(hub_deploy,
                             ["--kind", "driver", "--source", self._src(d), "--name", "Known", "--ip", "1.2.3.4"],
                             FakeClient)
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out)["action"], "update")

    def test_conflict_returns_2(self):
        # Raise the SAME DeployConflict class hub_deploy's except clause references
        # (each module gets its own hubclient import instance).
        def factory(base):
            c = FakeClient(base)

            def boom(kind, source, code_id=None):
                raise hub_deploy.DeployConflict("stale")
            c.deploy = boom
            return c
        with tempfile.TemporaryDirectory() as d:
            rc, _, err = run(hub_deploy,
                             ["--kind", "driver", "--source", self._src(d), "--id", "5", "--ip", "1.2.3.4"],
                             factory)
            self.assertEqual(rc, 2)
            self.assertIn("stale", err)


if __name__ == "__main__":
    unittest.main()
