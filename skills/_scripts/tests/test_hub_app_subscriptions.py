#!/usr/bin/env python3
"""Tests for skills/_scripts/hub_app_subscriptions.py — subscription projection, attribute
filtering, device-id extraction, the presence assertion, plus fetch error paths and the main()
flow with an injected transport. No live hub.

Fixtures mirror the live shape on 2.5.1.169 (C-8 Pro): statusJson carries `eventSubscriptions[]`
rows of {handler, type, name, typeId}, DEVICE and LOCATION rows side by side, and the hub answers
a bare `{}` for an app id that does not exist."""

import importlib.util
import json
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "hub_app_subscriptions.py"
spec = importlib.util.spec_from_file_location("hub_app_subscriptions", SCRIPT)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
HubError = m.HubError


def sub(name, type_id, handler="waterHandler", kind="DEVICE"):
    return {"handler": handler, "type": kind, "name": name, "typeId": type_id}


def hsm_status(water_ids=(101, 102, 103), extra=()):
    """Hubitat Safety Monitor's shape: per-device water rows plus two LOCATION rows."""
    rows = [sub("water.wet", i) for i in water_ids]
    rows += [sub("mode", 0, handler="modeHandler", kind="LOCATION"),
             sub("hsmSetArm", 0, handler="armHandler", kind="LOCATION")]
    rows += list(extra)
    return {"label": "Hubitat Safety Monitor", "eventSubscriptions": rows,
            "appSettings": [{"name": "useAllWater", "value": "true"}]}


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, body, content_type=None):
        self.calls.append({"method": method, "url": url})
        if not self.responses:
            raise AssertionError(f"unexpected extra request: {method} {url}")
        return self.responses.pop(0)


class TestSubscriptionsOf(unittest.TestCase):
    def test_reads_the_rows(self):
        self.assertEqual(len(m.subscriptions_of(hsm_status())), 5)

    def test_missing_key_is_empty(self):
        self.assertEqual(m.subscriptions_of({}), [])

    def test_non_list_is_empty(self):
        self.assertEqual(m.subscriptions_of({"eventSubscriptions": "nope"}), [])

    def test_non_dict_rows_are_dropped(self):
        self.assertEqual(m.subscriptions_of({"eventSubscriptions": [1, None, {"name": "x"}]}),
                         [{"name": "x"}])


class TestFilterByAttribute(unittest.TestCase):
    def test_none_returns_everything(self):
        self.assertEqual(len(m.filter_by_attribute(m.subscriptions_of(hsm_status()), None)), 5)

    def test_exact_event_name(self):
        rows = m.filter_by_attribute(m.subscriptions_of(hsm_status()), "water.wet")
        self.assertEqual(len(rows), 3)

    def test_bare_attribute_matches_its_dotted_forms(self):
        rows = m.filter_by_attribute(m.subscriptions_of(hsm_status()), "water")
        self.assertEqual(len(rows), 3)

    def test_non_matching_attribute_is_empty(self):
        self.assertEqual(m.filter_by_attribute(m.subscriptions_of(hsm_status()), "smoke"), [])

    def test_a_prefix_does_not_match_a_longer_attribute(self):
        rows = m.filter_by_attribute([sub("waterLevel", 1)], "water")
        self.assertEqual(rows, [])


class TestNormalizeDeviceId(unittest.TestCase):
    def test_int_passes_through(self):
        self.assertEqual(m.normalize_device_id(1611), 1611)

    def test_decimal_string_normalizes(self):
        """The hub renders typeId as a bare int and as its decimal string."""
        self.assertEqual(m.normalize_device_id("1611"), 1611)

    def test_whitespace_tolerated(self):
        self.assertEqual(m.normalize_device_id(" 1611 "), 1611)

    def test_rule_machine_state_key_takes_the_id(self):
        self.assertEqual(m.normalize_device_id("1612:Motion"), 1612)

    def test_none_and_junk_are_not_device_ids(self):
        self.assertIsNone(m.normalize_device_id(None))
        self.assertIsNone(m.normalize_device_id("not-an-id"))
        self.assertIsNone(m.normalize_device_id(""))


class TestDeviceIds(unittest.TestCase):
    def test_device_rows_only(self):
        """A LOCATION row's typeId is not a device id."""
        self.assertEqual(m.device_ids(m.subscriptions_of(hsm_status())), [101, 102, 103])

    def test_distinct_and_sorted(self):
        rows = [sub("water.wet", 5), sub("temperature", 5), sub("water.wet", 2)]
        self.assertEqual(m.device_ids(rows), [2, 5])

    def test_missing_type_id_is_skipped(self):
        self.assertEqual(m.device_ids([sub("water.wet", None)]), [])

    def test_string_ids_normalize_and_dedupe_against_ints(self):
        """A string "1611" must not read as a different device from 1611."""
        self.assertEqual(m.device_ids([sub("water.wet", "1611"), sub("water.wet", 1611)]), [1611])

    def test_sorted_numerically_not_lexically(self):
        self.assertEqual(m.device_ids([sub("w", "20"), sub("w", "3")]), [3, 20])


class TestCheckExpected(unittest.TestCase):
    def test_none_asserts_nothing(self):
        self.assertIsNone(m.check_expected(m.subscriptions_of(hsm_status()), None))

    def test_present(self):
        r = m.check_expected(m.subscriptions_of(hsm_status()), 102)
        self.assertEqual(r, {"device_id": 102, "present": True})

    def test_string_type_id_is_found(self):
        """The false-negative this normalization exists to prevent: a string id reported absent
        would send the caller off to re-Done an app that is already correctly configured."""
        rows = [sub("water.wet", "953")]
        self.assertTrue(m.check_expected(rows, 953)["present"])

    def test_absent(self):
        """The device added after the last Done: configured everywhere, subscribed nowhere."""
        r = m.check_expected(m.subscriptions_of(hsm_status()), 953)
        self.assertEqual(r, {"device_id": 953, "present": False})

    def test_presence_not_a_count_delta(self):
        """One Done can add several missing subscriptions; presence is the assertion."""
        before = m.subscriptions_of(hsm_status(water_ids=(101,)))
        after = m.subscriptions_of(hsm_status(water_ids=(101, 953, 954)))
        self.assertFalse(m.check_expected(before, 953)["present"])
        self.assertTrue(m.check_expected(after, 953)["present"])


class TestGroupByAttribute(unittest.TestCase):
    def test_groups_device_rows(self):
        g = m.group_by_attribute(m.subscriptions_of(hsm_status()))
        self.assertEqual(g, {"water.wet": [101, 102, 103]})

    def test_location_rows_excluded(self):
        self.assertNotIn("mode", m.group_by_attribute(m.subscriptions_of(hsm_status())))

    def test_missing_type_id_is_dropped(self):
        """`null` is not a device id; device_ids skips it and this must agree."""
        g = m.group_by_attribute([sub("water.wet", None), sub("water.wet", 7)])
        self.assertEqual(g, {"water.wet": [7]})

    def test_duplicates_collapse(self):
        g = m.group_by_attribute([sub("water.wet", 7), sub("water.wet", 7)])
        self.assertEqual(g, {"water.wet": [7]})


class TestFetchStatus(unittest.TestCase):
    def test_returns_parsed(self):
        t = FakeTransport([(200, {}, json.dumps(hsm_status()))])
        self.assertEqual(m.fetch_status("http://h:8080", 61, t)["label"],
                         "Hubitat Safety Monitor")

    def test_non_200_raises(self):
        t = FakeTransport([(500, {}, "")])
        with self.assertRaises(HubError):
            m.fetch_status("http://h:8080", 61, t)

    def test_non_json_names_hub_security(self):
        t = FakeTransport([(200, {}, "<html>")])
        with self.assertRaises(HubError) as ctx:
            m.fetch_status("http://h:8080", 61, t)
        self.assertIn("Hub Security", str(ctx.exception))

    def test_empty_object_is_a_missing_app(self):
        """The hub answers {} for an id that does not exist."""
        t = FakeTransport([(200, {}, "{}")])
        with self.assertRaises(HubError) as ctx:
            m.fetch_status("http://h:8080", 999, t)
        self.assertIn("appsList", str(ctx.exception))

    def test_non_object_json_raises(self):
        t = FakeTransport([(200, {}, "[]")])
        with self.assertRaises(HubError):
            m.fetch_status("http://h:8080", 61, t)


class TestMain(unittest.TestCase):
    """main() prints to stdout and stderr by contract; every case here captures both so a passing
    suite stays quiet."""

    def setUp(self):
        import contextlib
        import io as _io
        self.out, self.err = _io.StringIO(), _io.StringIO()
        self._ctx = contextlib.ExitStack()
        self._ctx.enter_context(contextlib.redirect_stdout(self.out))
        self._ctx.enter_context(contextlib.redirect_stderr(self.err))
        self.addCleanup(self._ctx.close)

    def test_reports_and_exits_zero(self):
        t = FakeTransport([(200, {}, json.dumps(hsm_status()))])
        self.assertEqual(m.main(["--ip", "192.0.2.11", "--app", "61"], transport=t), 0)

    def test_expected_present_exits_zero(self):
        t = FakeTransport([(200, {}, json.dumps(hsm_status(water_ids=(101, 953))))])
        rc = m.main(["--ip", "192.0.2.11", "--app", "61", "--attribute", "water.wet",
                     "--expect-device", "953"], transport=t)
        self.assertEqual(rc, 0)

    def test_string_type_id_exits_zero(self):
        t = FakeTransport([(200, {}, json.dumps(
            {"label": "HSM", "eventSubscriptions": [sub("water.wet", "953")]}))])
        rc = m.main(["--ip", "192.0.2.11", "--app", "61", "--attribute", "water.wet",
                     "--expect-device", "953"], transport=t)
        self.assertEqual(rc, 0)

    def test_expected_absent_exits_one(self):
        t = FakeTransport([(200, {}, json.dumps(hsm_status()))])
        rc = m.main(["--ip", "192.0.2.11", "--app", "61", "--attribute", "water.wet",
                     "--expect-device", "953"], transport=t)
        self.assertEqual(rc, 1)

    def test_absent_still_emits_the_result(self):
        """The buckets are the diagnosis; exit 1 here is a verdict, not a fetch failure."""
        t = FakeTransport([(200, {}, json.dumps(hsm_status()))])
        m.main(["--ip", "192.0.2.11", "--app", "61", "--expect-device", "953"], transport=t)
        result = json.loads(self.out.getvalue())
        self.assertEqual(result["expected"], {"device_id": 953, "present": False})
        self.assertEqual(result["device_ids"], [101, 102, 103])

    def test_hub_error_exits_one_with_no_json(self):
        t = FakeTransport([(500, {}, "")])
        rc = m.main(["--ip", "192.0.2.11", "--app", "61"], transport=t)
        self.assertEqual(rc, 1)
        self.assertEqual(self.out.getvalue(), "")

    def test_no_hub_selector_exits_two(self):
        rc = m.main(["--app", "61"], transport=FakeTransport([]))
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
