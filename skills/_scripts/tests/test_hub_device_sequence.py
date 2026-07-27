#!/usr/bin/env python3
"""Tests for skills/_scripts/hub_device_sequence.py — pure id parsing, plan building, and result
summarizing, plus run_sequence and main() with an injected transport, a no-op sleeper, and a
capturing announce. No live hub, no real clock."""

import importlib.util
import json
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "hub_device_sequence.py"
spec = importlib.util.spec_from_file_location("hub_device_sequence", SCRIPT)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
HubError = m.HubError


def full_json(device_id=1639, name="Zone 8 Soil", commands=None):
    return {
        "device": {"id": device_id, "displayName": name, "currentStates": {}},
        "commands": commands if commands is not None else [
            {"name": "on", "parameters": [], "relatedAttribute": "switch", "capability": True},
            {"name": "off", "parameters": [], "relatedAttribute": "switch", "capability": True},
        ],
    }


class FakeTransport:
    """Serves GET /device/fullJson/<id> and POST /device/runmethod. runmethod success is keyed by
    device id via `fail_ids`."""
    def __init__(self, devices, fail_ids=(), fail_methods=()):
        self.devices = devices  # {id: full_json}
        self.fail_ids = set(fail_ids)
        self.fail_methods = set(fail_methods)  # command names that return success:false
        self.runmethod_calls = []

    def __call__(self, method, url, body, _content_type=None):
        if method == "GET" and "/device/fullJson/" in url:
            did = int(url.rsplit("/", 1)[1])
            if did in self.devices:
                return 200, {}, json.dumps(self.devices[did])
            return 404, {}, ""
        if method == "POST" and url.endswith("/device/runmethod"):
            payload = json.loads(body)
            self.runmethod_calls.append(payload)
            ok = payload["id"] not in self.fail_ids and payload["method"] not in self.fail_methods
            return 200, {}, json.dumps({"success": ok, "message": None if ok else "threw"})
        raise AssertionError(f"unexpected call {method} {url}")


class TestParseDeviceIds(unittest.TestCase):
    def test_ordered_ints(self):
        self.assertEqual(m.parse_device_ids("1639, 1640 ,1641"), [1639, 1640, 1641])

    def test_empty_raises(self):
        with self.assertRaises(HubError):
            m.parse_device_ids("  ")

    def test_non_integer_raises(self):
        with self.assertRaises(HubError):
            m.parse_device_ids("1639,zone2")


class TestBuildPlan(unittest.TestCase):
    def test_indices_and_total(self):
        plan = m.build_plan([10, 11], "on", [], 30.0, off_command="off")
        self.assertEqual([s["index"] for s in plan], [1, 2])
        self.assertTrue(all(s["total"] == 2 for s in plan))
        self.assertEqual(plan[0]["off_command"], "off")
        self.assertEqual(plan[1]["device_id"], 11)


class TestSummarize(unittest.TestCase):
    def test_counts(self):
        steps = [{"dispatched": True}, {"dispatched": False, "error": "x"}, {"dispatched": True}]
        self.assertEqual(m.summarize(steps), {"total": 3, "dispatched": 2, "failed": 1})


class TestRunSequence(unittest.TestCase):
    def setUp(self):
        self.slept = []
        self.announced = []

    def _sleeper(self, seconds):
        self.slept.append(seconds)

    def _announce(self, text):
        self.announced.append(text)

    def test_runs_each_in_order_and_holds(self):
        t = FakeTransport({10: full_json(10, "A"), 11: full_json(11, "B")})
        plan = m.build_plan([10, 11], "on", [], 5.0)
        results = m.run_sequence("http://h:8080", plan, t, self._sleeper, self._announce)
        self.assertEqual([r["device_id"] for r in results], [10, 11])
        self.assertTrue(all(r["dispatched"] for r in results))
        self.assertEqual(self.slept, [5.0, 5.0])  # held once per dispatched device
        self.assertEqual([c["id"] for c in t.runmethod_calls], [10, 11])

    def test_failed_dispatch_skips_hold_and_continues(self):
        t = FakeTransport({10: full_json(10, "A"), 11: full_json(11, "B")}, fail_ids={10})
        plan = m.build_plan([10, 11], "on", [], 5.0)
        results = m.run_sequence("http://h:8080", plan, t, self._sleeper, self._announce)
        self.assertFalse(results[0]["dispatched"])
        self.assertIn("error", results[0])
        self.assertTrue(results[1]["dispatched"])
        self.assertEqual(self.slept, [5.0])  # only the successful device was held

    def test_unreachable_device_does_not_abort(self):
        t = FakeTransport({10: full_json(10, "A")})  # 11 missing -> 404
        plan = m.build_plan([10, 11], "on", [], 1.0)
        results = m.run_sequence("http://h:8080", plan, t, self._sleeper, self._announce)
        self.assertTrue(results[0]["dispatched"])
        self.assertFalse(results[1]["dispatched"])
        self.assertIn("error", results[1])

    def test_off_command_runs_after_hold(self):
        t = FakeTransport({10: full_json(10, "A")})
        plan = m.build_plan([10], "on", [], 2.0, off_command="off")
        results = m.run_sequence("http://h:8080", plan, t, self._sleeper, self._announce)
        self.assertTrue(results[0]["off_dispatched"])
        # on then off for device 10
        self.assertEqual([(c["id"], c["method"]) for c in t.runmethod_calls],
                         [(10, "on"), (10, "off")])

    def test_off_command_failure_recorded_and_counted(self):
        t = FakeTransport({10: full_json(10, "A")}, fail_methods={"off"})
        plan = m.build_plan([10], "on", [], 1.0, off_command="off")
        results = m.run_sequence("http://h:8080", plan, t, self._sleeper, self._announce)
        self.assertTrue(results[0]["dispatched"])          # on succeeded
        self.assertFalse(results[0]["off_dispatched"])     # off returned success:false
        self.assertIn("off_error", results[0])
        self.assertEqual(m.summarize(results)["failed"], 1)

    def test_unknown_command_captured_per_device(self):
        t = FakeTransport({10: full_json(10, "A", commands=[
            {"name": "refresh", "parameters": [], "relatedAttribute": None, "capability": True}])})
        plan = m.build_plan([10], "on", [], 1.0)
        results = m.run_sequence("http://h:8080", plan, t, self._sleeper, self._announce)
        self.assertFalse(results[0]["dispatched"])
        self.assertIn("no command", results[0]["error"])


class TestMain(unittest.TestCase):
    def test_devices_flow_and_exit_zero(self):
        t = FakeTransport({10: full_json(10, "A"), 11: full_json(11, "B")})
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = m.main(["--ip", "192.0.2.11", "--devices", "10,11", "--duration", "3"],
                        transport=t, sleeper=lambda _s: None)
        self.assertEqual(rc, 0)
        out = json.loads(buf.getvalue())
        self.assertEqual(out["summary"], {"total": 2, "dispatched": 2, "failed": 0})

    def test_any_failure_exits_one(self):
        t = FakeTransport({10: full_json(10, "A"), 11: full_json(11, "B")}, fail_ids={11})
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = m.main(["--ip", "192.0.2.11", "--devices", "10,11"],
                        transport=t, sleeper=lambda _s: None)
        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(buf.getvalue())["summary"]["failed"], 1)

    def test_bad_device_id_exits_two(self):
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = m.main(["--ip", "192.0.2.11", "--devices", "10,bad"],
                        transport=None, sleeper=lambda _s: None)
        self.assertEqual(rc, 2)
        self.assertIn("not an integer", err.getvalue())

    def test_negative_duration_exits_two(self):
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = m.main(["--ip", "192.0.2.11", "--devices", "10", "--duration", "-5"],
                        transport=None, sleeper=lambda _s: None)
        self.assertEqual(rc, 2)
        self.assertIn("must be >= 0", err.getvalue())

    def test_names_resolved_in_order(self):
        devices_list = {"devices": [{"data": {"id": 10, "name": "A"}}, {"data": {"id": 11, "name": "B"}}]}

        class NamesTransport(FakeTransport):
            def __call__(self, method, url, body, content_type=None):
                if method == "GET" and url.endswith("/hub2/devicesList"):
                    return 200, {}, json.dumps(devices_list)
                return super().__call__(method, url, body, content_type)

        t = NamesTransport({10: full_json(10, "A"), 11: full_json(11, "B")})
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = m.main(["--ip", "192.0.2.11", "--names", "A,B"], transport=t, sleeper=lambda _s: None)
        self.assertEqual(rc, 0)
        self.assertEqual([s["device_id"] for s in json.loads(buf.getvalue())["steps"]], [10, 11])


if __name__ == "__main__":
    unittest.main()
