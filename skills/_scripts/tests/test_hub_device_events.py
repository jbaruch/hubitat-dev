#!/usr/bin/env python3
"""Tests for skills/_scripts/hub_device_events.py — declared-attribute reading, command-row
exclusion, per-attribute counting, silent-channel detection, the presence assertion, fetch error
paths, and the main() flow with an injected transport. No live hub.

Fixtures mirror the live shapes on 2.5.1.x (C-8 Pro): fullJson renders `device.currentStates` as a
dict keyed by attribute and (on some builds) as a list of rows; eventsJson is a list carrying
attribute changes and `command-*` rows side by side, and `[]` for a device that has never evented.

The dead-channel case is the regression guard for the 2026-07-27 multi-sensor: contact frozen at
`closed` with the window physically open, zero contact events out of 33 retained."""

import contextlib
import importlib.util
import io as _io
import json
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "hub_device_events.py"
spec = importlib.util.spec_from_file_location("hub_device_events", SCRIPT)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
HubError = m.HubError


def evt(name, date, value="x", kind="DEVICE"):
    return {"name": name, "date": date, "value": value, "type": kind}


def multisensor_full():
    """A contact/temperature/battery multi-sensor, currentStates as a dict."""
    return {"device": {"id": 119, "currentStates": {
        "contact": {"name": "contact", "value": "closed"},
        "temperature": {"name": "temperature", "value": "71"},
        "battery": {"name": "battery", "value": "84"}}}}


def dead_contact_events():
    """Temperature and battery advancing, contact silent — the 2026-07-27 shape."""
    return ([evt("temperature", f"2026-07-27 0{i}:00:00") for i in range(1, 6)]
            + [evt("battery", f"2026-07-26 0{i}:00:00") for i in range(1, 4)])


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, body, content_type=None):
        self.calls.append({"method": method, "url": url})
        if not self.responses:
            raise AssertionError(f"unexpected extra request: {method} {url}")
        return self.responses.pop(0)


class TestDeclaredAttributes(unittest.TestCase):
    def test_dict_form(self):
        self.assertEqual(m.declared_attributes(multisensor_full()),
                         ["battery", "contact", "temperature"])

    def test_list_form(self):
        full = {"device": {"currentStates": [{"name": "water"}, {"name": "battery"}]}}
        self.assertEqual(m.declared_attributes(full), ["battery", "water"])

    def test_list_form_drops_nameless_rows(self):
        full = {"device": {"currentStates": [{"name": "water"}, {}, None]}}
        self.assertEqual(m.declared_attributes(full), ["water"])

    def test_missing_is_empty(self):
        self.assertEqual(m.declared_attributes({}), [])
        self.assertEqual(m.declared_attributes({"device": {}}), [])


class TestAttributeEvents(unittest.TestCase):
    def test_command_type_rows_excluded(self):
        rows = [evt("switch", "d1"), evt("command-on", "d2", kind="command")]
        self.assertEqual([r["name"] for r in m.attribute_events(rows)], ["switch"])

    def test_command_prefixed_rows_excluded_regardless_of_type(self):
        rows = [evt("command-refresh", "d1", kind="DEVICE"), evt("switch", "d2")]
        self.assertEqual([r["name"] for r in m.attribute_events(rows)], ["switch"])

    def test_nameless_and_non_dict_rows_dropped(self):
        self.assertEqual(m.attribute_events([{"date": "d"}, None, 7]), [])

    def test_empty_log(self):
        """23 of 156 devices had never evented at all."""
        self.assertEqual(m.attribute_events([]), [])


class TestCountByAttribute(unittest.TestCase):
    def test_counts_and_bounds(self):
        counts = m.count_by_attribute(dead_contact_events())
        self.assertEqual(counts["temperature"]["count"], 5)
        self.assertEqual(counts["temperature"]["newest"], "2026-07-27 05:00:00")
        self.assertEqual(counts["temperature"]["oldest"], "2026-07-27 01:00:00")

    def test_silent_channel_absent_from_counts(self):
        self.assertNotIn("contact", m.count_by_attribute(dead_contact_events()))

    def test_commands_do_not_inflate_a_channel(self):
        rows = [evt("command-on", "d1", kind="command")] * 5
        self.assertEqual(m.count_by_attribute(rows), {})


class TestSilentAttributes(unittest.TestCase):
    def test_dead_channel_is_named(self):
        """The whole point: siblings healthy, one channel dead."""
        declared = m.declared_attributes(multisensor_full())
        counts = m.count_by_attribute(dead_contact_events())
        self.assertEqual(m.silent_attributes(declared, counts), ["contact"])

    def test_all_reporting_is_empty(self):
        declared = ["temperature"]
        counts = m.count_by_attribute([evt("temperature", "d1")])
        self.assertEqual(m.silent_attributes(declared, counts), [])

    def test_a_device_that_never_evented_is_all_silent(self):
        declared = m.declared_attributes(multisensor_full())
        self.assertEqual(m.silent_attributes(declared, {}),
                         ["battery", "contact", "temperature"])

    def test_undeclared_attribute_with_events_is_not_reported_silent(self):
        self.assertEqual(m.silent_attributes([], m.count_by_attribute(dead_contact_events())), [])


class TestCheckExpected(unittest.TestCase):
    def test_none_asserts_nothing(self):
        self.assertIsNone(m.check_expected(["contact"], ["contact"], None))

    def test_silent_attribute(self):
        r = m.check_expected(["contact"], ["contact", "temperature"], "contact")
        self.assertEqual(r, {"attribute": "contact", "declared": True, "silent": True})

    def test_reporting_attribute(self):
        r = m.check_expected(["contact"], ["contact", "temperature"], "temperature")
        self.assertEqual(r, {"attribute": "temperature", "declared": True, "silent": False})

    def test_undeclared_attribute(self):
        r = m.check_expected([], ["contact"], "water")
        self.assertEqual(r, {"attribute": "water", "declared": False, "silent": False})


class TestFetch(unittest.TestCase):
    def test_full_json_wrong_shape_raises(self):
        t = FakeTransport([(200, {}, "null")])
        with self.assertRaises(HubError):
            m.fetch_full_json("http://h:8080", 119, t)

    def test_full_json_non_json_names_hub_security(self):
        t = FakeTransport([(200, {}, "<html>")])
        with self.assertRaises(HubError) as ctx:
            m.fetch_full_json("http://h:8080", 119, t)
        self.assertIn("Hub Security", str(ctx.exception))

    def test_events_must_be_a_list(self):
        t = FakeTransport([(200, {}, "{}")])
        with self.assertRaises(HubError):
            m.fetch_events("http://h:8080", 119, t)

    def test_events_empty_list_is_valid(self):
        t = FakeTransport([(200, {}, "[]")])
        self.assertEqual(m.fetch_events("http://h:8080", 119, t), [])

    def test_non_200_raises(self):
        t = FakeTransport([(404, {}, "")])
        with self.assertRaises(HubError):
            m.fetch_events("http://h:8080", 119, t)


class TestMain(unittest.TestCase):
    def setUp(self):
        self.out, self.err = _io.StringIO(), _io.StringIO()
        ctx = contextlib.ExitStack()
        ctx.enter_context(contextlib.redirect_stdout(self.out))
        ctx.enter_context(contextlib.redirect_stderr(self.err))
        self.addCleanup(ctx.close)

    def _transport(self, full=None, events=None):
        return FakeTransport([(200, {}, json.dumps(full if full is not None else multisensor_full())),
                              (200, {}, json.dumps(events if events is not None
                                                   else dead_contact_events()))])

    def test_reports_and_exits_zero(self):
        rc = m.main(["--ip", "192.0.2.11", "--device", "119"], transport=self._transport())
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(self.out.getvalue())["silent_attributes"], ["contact"])

    def test_expected_silent_exits_one(self):
        rc = m.main(["--ip", "192.0.2.11", "--device", "119", "--expect-attribute", "contact"],
                    transport=self._transport())
        self.assertEqual(rc, 1)
        self.assertIn("physical state", self.err.getvalue())

    def test_expected_reporting_exits_zero(self):
        rc = m.main(["--ip", "192.0.2.11", "--device", "119", "--expect-attribute", "temperature"],
                    transport=self._transport())
        self.assertEqual(rc, 0)

    def test_undeclared_expected_attribute_exits_one(self):
        rc = m.main(["--ip", "192.0.2.11", "--device", "119", "--expect-attribute", "water"],
                    transport=self._transport())
        self.assertEqual(rc, 1)
        self.assertIn("not published", self.err.getvalue())

    def test_silent_case_still_emits_the_result(self):
        m.main(["--ip", "192.0.2.11", "--device", "119", "--expect-attribute", "contact"],
               transport=self._transport())
        self.assertEqual(json.loads(self.out.getvalue())["expected"]["silent"], True)

    def test_hub_error_exits_one_with_no_json(self):
        t = FakeTransport([(500, {}, "")])
        rc = m.main(["--ip", "192.0.2.11", "--device", "119"], transport=t)
        self.assertEqual(rc, 1)
        self.assertEqual(self.out.getvalue(), "")

    def test_no_hub_selector_exits_two(self):
        self.assertEqual(m.main(["--device", "119"], transport=FakeTransport([])), 2)


if __name__ == "__main__":
    unittest.main()
