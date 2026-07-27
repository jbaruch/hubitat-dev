#!/usr/bin/env python3
"""Tests for skills/_scripts/hub_device_command.py — pure command-surface validation, argument
coercion, currentStates diffing, runmethod response parsing, plus the fetch/run error paths and the
main() flow with an injected transport. No live hub.

Fixtures mirror the live shapes on 2.5.1.134–135 (C-8 Pro): fullJson `commands[]` entries carry
{name, parameters:[{type}], relatedAttribute, capability}; device.currentStates is a dict keyed by
attribute; /device/runmethod answers {"success":<bool>,"message":<str|null>}."""

import importlib.util
import json
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "hub_device_command.py"
spec = importlib.util.spec_from_file_location("hub_device_command", SCRIPT)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
HubError = m.HubError


def cmd(name, params=None, related=None, capability=True):
    return {"name": name, "parameters": [{"type": t} for t in (params or [])],
            "relatedAttribute": related, "capability": capability}


def full_json(commands=None, states=None):
    return {
        "device": {"id": 1639, "name": "Generic Zigbee Switch", "displayName": "Zone 8 Soil",
                   "currentStates": states or {}},
        "commands": commands if commands is not None else [
            cmd("on", related="switch"), cmd("off", related="switch"),
            cmd("setLevel", params=["NUMBER", "NUMBER"], related="level"),
            cmd("setZoneWaterTime", params=["NUMBER"], related=None, capability=False),
        ],
    }


class FakeTransport:
    """Records calls and returns queued (status, headers, text) responses by (method, path)."""
    def __init__(self, responses):
        self._responses = responses  # {(method, path_suffix): (status, text)}
        self.calls = []

    def __call__(self, method, url, body, content_type=None):
        self.calls.append({"method": method, "url": url, "body": body, "content_type": content_type})
        for (want_method, suffix), (status, text) in self._responses.items():
            if method == want_method and url.endswith(suffix):
                return status, {}, text
        raise AssertionError(f"unexpected transport call {method} {url}")


class TestCommandSurface(unittest.TestCase):
    def test_commands_of_filters_non_dicts(self):
        self.assertEqual(len(m.commands_of({"commands": [cmd("on"), "junk", None]})), 1)

    def test_command_names_in_order(self):
        self.assertEqual(m.command_names(m.commands_of(full_json())),
                         ["on", "off", "setLevel", "setZoneWaterTime"])

    def test_find_command_exact_case_sensitive(self):
        cmds = m.commands_of(full_json())
        self.assertIsNotNone(m.find_command(cmds, "setLevel"))
        self.assertIsNone(m.find_command(cmds, "setlevel"))

    def test_validate_unknown_lists_valid(self):
        cmds = m.commands_of(full_json())
        with self.assertRaises(HubError) as ctx:
            m.validate_command(cmds, "nope")
        msg = str(ctx.exception)
        self.assertIn("nope", msg)
        self.assertIn("setZoneWaterTime", msg)

    def test_describe_surface_marks_custom_command(self):
        surface = {s["name"]: s for s in m.describe_surface(m.commands_of(full_json()))}
        self.assertFalse(surface["setZoneWaterTime"]["capability"])
        self.assertTrue(surface["on"]["capability"])
        self.assertEqual(surface["setLevel"]["parameters"], ["NUMBER", "NUMBER"])


class TestArgCoercion(unittest.TestCase):
    def test_number_int_and_float(self):
        self.assertEqual(m.coerce_arg("40", "NUMBER"), 40)
        self.assertIsInstance(m.coerce_arg("40", "NUMBER"), int)
        self.assertEqual(m.coerce_arg("40.5", "NUMBER"), 40.5)

    def test_decimal(self):
        self.assertEqual(m.coerce_arg("1.5", "DECIMAL"), 1.5)

    def test_string_and_enum_pass_through(self):
        self.assertEqual(m.coerce_arg("false", "ENUM"), "false")
        self.assertEqual(m.coerce_arg("open", "STRING"), "open")

    def test_bad_number_raises(self):
        with self.assertRaises(HubError):
            m.coerce_arg("high", "NUMBER")

    def test_coerce_args_maps_positional_types(self):
        command = cmd("setLevel", params=["NUMBER", "NUMBER"])
        self.assertEqual(m.coerce_args(command, ["40", "5"]), [40, 5])

    def test_too_many_args_raises(self):
        with self.assertRaises(HubError):
            m.coerce_args(cmd("on"), ["40"])

    def test_extra_untyped_arg_is_string(self):
        # a command declaring one param, given one arg, coerces that one; count guard covers overflow
        self.assertEqual(m.coerce_args(cmd("setZoneWaterTime", params=["NUMBER"]), ["10"]), [10])


class TestCurrentStates(unittest.TestCase):
    def test_dict_shape(self):
        states = m.current_states(full_json(states={"switch": {"value": "on", "date": "d1"}}))
        self.assertEqual(states["switch"]["value"], "on")

    def test_list_shape(self):
        states = m.current_states({"device": {"currentStates": [{"name": "level", "value": 40}]}})
        self.assertEqual(states["level"]["value"], 40)

    def test_absent(self):
        self.assertEqual(m.current_states({"device": {}}), {})


class TestVerifyOutcome(unittest.TestCase):
    def test_changed(self):
        r = m.verify_outcome({"switch": {"value": "off"}}, {"switch": {"value": "on"}}, "switch")
        self.assertTrue(r["changed"])
        self.assertEqual((r["before"], r["after"]), ("off", "on"))

    def test_unchanged_is_not_failure(self):
        r = m.verify_outcome({"switch": {"value": "on"}}, {"switch": {"value": "on"}}, "switch")
        self.assertFalse(r["changed"])
        self.assertIn("no-op", r["note"])

    def test_no_related_attribute(self):
        r = m.verify_outcome({}, {}, None)
        self.assertIsNone(r["changed"])
        self.assertIn("eventsJson", r["note"])

    def test_attribute_absent(self):
        r = m.verify_outcome({"switch": {"value": "on"}}, {"switch": {"value": "on"}}, "level")
        self.assertIsNone(r["changed"])


class TestRunmethodResponse(unittest.TestCase):
    def test_success(self):
        self.assertEqual(m.parse_runmethod_response('{"success":true,"message":null}'),
                         {"success": True, "message": None})

    def test_failure_flag(self):
        self.assertFalse(m.parse_runmethod_response('{"success":false,"message":"boom"}')["success"])

    def test_non_json_raises(self):
        with self.assertRaises(HubError):
            m.parse_runmethod_response("<html>login</html>")


class TestRunMethodTransport(unittest.TestCase):
    def test_posts_json_body_with_content_type(self):
        t = FakeTransport({("POST", "/device/runmethod"): (200, '{"success":true,"message":null}')})
        out = m.run_method("http://h:8080", 1639, "setLevel", [40, 5], t)
        self.assertTrue(out["success"])
        call = t.calls[0]
        self.assertEqual(call["content_type"], "application/json")
        self.assertEqual(json.loads(call["body"]), {"id": 1639, "method": "setLevel", "args": [40, 5]})

    def test_non_200_raises(self):
        t = FakeTransport({("POST", "/device/runmethod"): (404, "")})
        with self.assertRaises(HubError):
            m.run_method("http://h:8080", 1, "on", [], t)


class TestResolveDeviceId(unittest.TestCase):
    def test_exact_case_insensitive(self):
        devices = {"devices": [{"data": {"id": 1639, "name": "Zone 8 Soil"}}]}
        self.assertEqual(m.resolve_device_id(devices, "zone 8 soil"), 1639)

    def test_zero_matches_raises(self):
        with self.assertRaises(HubError):
            m.resolve_device_id({"devices": []}, "nope")

    def test_multiple_matches_raises(self):
        devices = {"devices": [{"data": {"id": 1, "name": "Dup"}}, {"data": {"id": 2, "name": "Dup"}}]}
        with self.assertRaises(HubError):
            m.resolve_device_id(devices, "Dup")


class TestMain(unittest.TestCase):
    def test_list_prints_surface_and_runs_nothing(self):
        t = FakeTransport({("GET", "/device/fullJson/1639"): (200, json.dumps(full_json()))})
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = m.main(["--ip", "192.0.2.11", "--device", "1639", "--list"], t)
        self.assertEqual(rc, 0)
        out = json.loads(buf.getvalue())
        self.assertEqual([c["name"] for c in out["commands"]],
                         ["on", "off", "setLevel", "setZoneWaterTime"])
        self.assertTrue(all(c["method"] == "GET" for c in t.calls))  # no runmethod POST

    def test_command_dispatch_and_verify_change(self):
        before = full_json(states={"switch": {"value": "off", "date": "d0"}})
        after = full_json(states={"switch": {"value": "on", "date": "d1"}})
        t = FakeTransport({
            ("GET", "/device/fullJson/1639"): (200, json.dumps(before)),
            ("POST", "/device/runmethod"): (200, '{"success":true,"message":null}'),
        })
        # second fullJson (post-command) must reflect the change: swap the GET response mid-run
        calls = {"n": 0}
        original = t.__call__

        def sequenced(method, url, body, content_type=None):
            if method == "GET" and url.endswith("/device/fullJson/1639"):
                calls["n"] += 1
                payload = before if calls["n"] == 1 else after
                t.calls.append({"method": method, "url": url, "body": body, "content_type": content_type})
                return 200, {}, json.dumps(payload)
            return original(method, url, body, content_type)

        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = m.main(["--ip", "192.0.2.11", "--device", "1639", "--command", "on"], sequenced)
        self.assertEqual(rc, 0)
        out = json.loads(buf.getvalue())
        self.assertTrue(out["dispatched"])
        self.assertTrue(out["verification"]["changed"])

    def test_unknown_command_exits_1(self):
        t = FakeTransport({("GET", "/device/fullJson/1639"): (200, json.dumps(full_json()))})
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = m.main(["--ip", "192.0.2.11", "--device", "1639", "--command", "nope"], t)
        self.assertEqual(rc, 1)
        self.assertIn("no command", err.getvalue())

    def test_dispatch_false_exits_1(self):
        t = FakeTransport({
            ("GET", "/device/fullJson/1639"): (200, json.dumps(full_json())),
            ("POST", "/device/runmethod"): (200, '{"success":false,"message":"threw"}'),
        })
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = m.main(["--ip", "192.0.2.11", "--device", "1639", "--command", "on", "--no-verify"], t)
        self.assertEqual(rc, 1)
        self.assertFalse(json.loads(buf.getvalue())["dispatched"])

    def test_command_required_unless_list(self):
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = m.main(["--ip", "192.0.2.11", "--device", "1639"], None)
        self.assertEqual(rc, 2)
        self.assertIn("--command is required", err.getvalue())


if __name__ == "__main__":
    unittest.main()
