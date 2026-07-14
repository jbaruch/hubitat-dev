#!/usr/bin/env python3
"""Tests for scripts/hubclient.py — config resolution and deploy logic, no live hub."""

import importlib.util
import json
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "hubclient.py"
spec = importlib.util.spec_from_file_location("hubclient", SCRIPT)
assert spec and spec.loader
hubclient = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hubclient)


class FakeTransport:
    """Callable transport for tests. routes: dict mapping (method, path-suffix) ->
    (status, headers, text). Path match is by 'startswith' so query strings are ignored."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, method, url, body):
        path = "/" + url.split("://", 1)[1].split("/", 1)[1]
        self.calls.append((method, path, body))
        for (m, suffix), resp in self.routes.items():
            if m == method and path.startswith(suffix):
                return resp
        raise AssertionError(f"no route for {method} {path}")


def make_transport(routes):
    return FakeTransport(routes)


CFG = {"schema_version": 1, "default": "main",
       "hubs": {"main": {"ip": "192.168.30.2"},
                "garage": {"ip": "192.168.30.16", "port": 8080}}}


class TestResolveHub(unittest.TestCase):
    def test_default(self):
        h = hubclient.resolve_hub(CFG)
        self.assertEqual(h["name"], "main")
        self.assertEqual(h["base"], "http://192.168.30.2:8080")

    def test_named(self):
        self.assertEqual(hubclient.resolve_hub(CFG, "garage")["ip"], "192.168.30.16")

    def test_single_hub_needs_no_default(self):
        cfg = {"schema_version": 1, "hubs": {"only": {"ip": "10.0.0.5"}}}
        self.assertEqual(hubclient.resolve_hub(cfg)["name"], "only")

    def test_unknown_name_raises(self):
        with self.assertRaises(hubclient.HubError):
            hubclient.resolve_hub(CFG, "nope")

    def test_no_default_multiple_raises(self):
        cfg = {"schema_version": 1, "hubs": {"a": {"ip": "1"}, "b": {"ip": "2"}}}
        with self.assertRaises(hubclient.HubError):
            hubclient.resolve_hub(cfg)


class TestDeployDecision(unittest.TestCase):
    def test_create_when_no_existing(self):
        self.assertEqual(hubclient.decide_deploy_action(None, None), {"action": "create"})

    def test_update_when_existing(self):
        self.assertEqual(hubclient.decide_deploy_action(42, 7),
                         {"action": "update", "id": 42, "version": 7})


class TestIdFromLocation(unittest.TestCase):
    def test_extracts_driver_id(self):
        self.assertEqual(hubclient._id_from_location({"Location": "/driver/editor/305"}, "driver"), 305)

    def test_extracts_app_id_case_insensitive(self):
        self.assertEqual(hubclient._id_from_location({"location": "/app/editor/12?x=1"}, "app"), 12)

    def test_none_when_absent(self):
        self.assertIsNone(hubclient._id_from_location({}, "app"))


class TestEnumerateAndPull(unittest.TestCase):
    def test_enumerate(self):
        t = make_transport({("GET", "/hub2/userDeviceTypes"):
                            (200, {}, json.dumps([{"id": 1, "name": "D"}]))})
        c = hubclient.HubClient("http://h:8080", t)
        self.assertEqual(c.enumerate("driver"), [{"id": 1, "name": "D"}])

    def test_pull_shapes_response(self):
        t = make_transport({("GET", "/driver/ajax/code"):
                            (200, {}, json.dumps({"id": 9, "version": 4, "source": "x"}))})
        c = hubclient.HubClient("http://h:8080", t)
        self.assertEqual(c.pull("driver", 9), {"id": 9, "name": None, "version": 4, "source": "x"})

    def test_find_id_matches_by_name(self):
        t = make_transport({("GET", "/hub2/userAppTypes"):
                            (200, {}, json.dumps([{"id": 1, "name": "Other"}, {"id": 2, "name": "Mine"}]))})
        c = hubclient.HubClient("http://h:8080", t)
        self.assertEqual(c.find_id("app", "Mine"), 2)
        self.assertIsNone(c.find_id("app", "Absent"))


class TestDeploy(unittest.TestCase):
    def test_create_returns_new_id_from_location(self):
        t = make_transport({("POST", "/driver/save"): (302, {"Location": "/driver/editor/77"}, "")})
        c = hubclient.HubClient("http://h:8080", t)
        self.assertEqual(c.deploy("driver", "source"), {"action": "create", "id": 77})

    def test_update_pulls_version_then_saves(self):
        t = make_transport({
            ("GET", "/driver/ajax/code"): (200, {}, json.dumps({"id": 5, "version": 3, "source": "old"})),
            ("POST", "/driver/ajax/update"): (200, {}, json.dumps({"status": "success"})),
        })
        c = hubclient.HubClient("http://h:8080", t)
        result = c.deploy("driver", "new source", code_id=5)
        self.assertEqual(result, {"action": "update", "id": 5, "version": 3})
        # the update POST carried the pulled version
        post = [call for call in t.calls if call[0] == "POST"][0]
        self.assertIn("version=3", post[2])

    def test_stale_version_raises_conflict(self):
        t = make_transport({
            ("GET", "/driver/ajax/code"): (200, {}, json.dumps({"id": 5, "version": 3, "source": "old"})),
            ("POST", "/driver/ajax/update"): (200, {}, json.dumps({"error": "version mismatch"})),
        })
        c = hubclient.HubClient("http://h:8080", t)
        with self.assertRaises(hubclient.DeployConflict):
            c.deploy("driver", "x", code_id=5)


if __name__ == "__main__":
    unittest.main()
