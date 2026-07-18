#!/usr/bin/env python3
"""Tests for skills/_scripts/hubclient.py — config resolution and deploy logic, no live hub."""

import importlib.util
import json
import os
import tempfile
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
       "hubs": {"main": {"ip": "192.0.2.10"},
                "garage": {"ip": "192.0.2.12", "port": 8080}}}


class TestResolveHub(unittest.TestCase):
    def test_default(self):
        h = hubclient.resolve_hub(CFG)
        self.assertEqual(h["name"], "main")
        self.assertEqual(h["base"], "http://192.0.2.10:8080")

    def test_named(self):
        self.assertEqual(hubclient.resolve_hub(CFG, "garage")["ip"], "192.0.2.12")

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

    def test_create_without_new_id_raises(self):
        # A create that redirects somewhere without an editor id must not return {id: None}.
        t = make_transport({("POST", "/driver/save"): (302, {"Location": "/error"}, "")})
        c = hubclient.HubClient("http://h:8080", t)
        with self.assertRaises(hubclient.HubError):
            c.deploy("driver", "source")

    def test_update_unsuccessful_status_is_not_accepted(self):
        # "unsuccessful" contains the substring "success" — must NOT be read as a success.
        t = make_transport({
            ("GET", "/driver/ajax/code"): (200, {}, json.dumps({"id": 5, "version": 3, "source": "old"})),
            ("POST", "/driver/ajax/update"): (200, {}, json.dumps({"status": "unsuccessful"})),
        })
        c = hubclient.HubClient("http://h:8080", t)
        with self.assertRaises(hubclient.HubError):
            c.deploy("driver", "x", code_id=5)


class TestResolveBaseFromArgs(unittest.TestCase):
    def test_ip_wins(self):
        self.assertEqual(hubclient.resolve_base_from_args(ip="1.2.3.4", port=8080), "http://1.2.3.4:8080")

    def test_hub_flag_defaults_to_hubs_json_in_cwd(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "hubs.json").write_text(json.dumps(CFG))
            old = os.getcwd()
            os.chdir(d)
            try:
                self.assertEqual(hubclient.resolve_base_from_args(hub="main"), "http://192.0.2.10:8080")
            finally:
                os.chdir(old)

    def test_no_ip_no_hub_raises(self):
        with self.assertRaises(hubclient.HubError):
            hubclient.resolve_base_from_args()


class TestLoadHubs(unittest.TestCase):
    def test_missing_file_raises_huberror(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(hubclient.HubError) as ctx:
                hubclient.load_hubs(str(Path(d) / "absent.json"))
            self.assertIn("not found", str(ctx.exception))

    def test_malformed_json_raises_huberror(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "hubs.json"
            p.write_text("{ not json")
            with self.assertRaises(hubclient.HubError):
                hubclient.load_hubs(str(p))

    def test_wrong_schema_version_raises_huberror(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "hubs.json"
            p.write_text(json.dumps({"schema_version": 999, "hubs": {}}))
            with self.assertRaises(hubclient.HubError):
                hubclient.load_hubs(str(p))


class TestNonJsonResponses(unittest.TestCase):
    def test_enumerate_on_html_raises_actionable_error(self):
        # Hub Security on returns an HTML login page, not JSON.
        t = make_transport({("GET", "/hub2/userDeviceTypes"):
                            (200, {}, "<html><body>The login information...</body></html>")})
        c = hubclient.HubClient("http://h:8080", t)
        with self.assertRaises(hubclient.HubError) as ctx:
            c.enumerate("driver")
        self.assertIn("did not return JSON", str(ctx.exception))
        self.assertIn("Hub Security", str(ctx.exception))

    def test_pull_on_html_raises_actionable_error(self):
        t = make_transport({("GET", "/driver/ajax/code"): (200, {}, "<html>login</html>")})
        c = hubclient.HubClient("http://h:8080", t)
        with self.assertRaises(hubclient.HubError):
            c.pull("driver", 5)


if __name__ == "__main__":
    unittest.main()
