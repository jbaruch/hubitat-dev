#!/usr/bin/env python3
"""Tests for skills/_scripts/hub_device_update.py — boolean encoding, form construction, drift
classification, --set parsing, plus the fetch/post error paths and the main() flow with an injected
transport. No live hub.

Fixtures mirror the live shapes on 2.5.1.135 / 2.5.1.169 (C-8 Pro): fullJson carries the device
record under `device`, booleans render as real booleans or as null on a device that never had them
set, and POST /device/update answers HTML or a 302 rather than JSON.

The encoding assertions here are the regression guard for the bug this script exists to prevent:
posting a literal "true" for a checkbox field clears it (measured on device 326, 2026-07-27)."""

import importlib.util
import json
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "hub_device_update.py"
spec = importlib.util.spec_from_file_location("hub_device_update", SCRIPT)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
HubError = m.HubError


def device_record(**overrides):
    """A settled Zigbee moisture sensor, the shape a healthy device reads back as."""
    record = {
        "name": "Zooz ZSE42", "label": "Kitchen Leak", "zigbeeId": "", "maxEvents": 11,
        "maxStates": 30, "spammyThreshold": 300, "deviceNetworkId": "0179", "deviceTypeId": 412,
        "deviceTypeReadableType": "usr", "roomId": 7,
        "meshEnabled": True, "retryEnabled": True, "meshFullSync": True, "homeKitEnabled": False,
        "locationId": 1, "hubId": 1, "groupId": 1, "dashboardIds": "", "tags": "",
        "defaultIcon": "", "notes": "", "id": 953, "version": 41, "controllerType": "ZWV",
    }
    record.update(overrides)
    return record


def full_json(**overrides):
    return {"device": device_record(**overrides)}


class FakeTransport:
    """Replays a queued list of (status, headers, body) and records every call."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, body, content_type=None):
        self.calls.append({"method": method, "url": url, "body": body,
                           "content_type": content_type})
        if not self.responses:
            raise AssertionError(f"unexpected extra request: {method} {url}")
        return self.responses.pop(0)


class TestAsBool(unittest.TestCase):
    def test_real_booleans_pass_through(self):
        self.assertIs(m.as_bool(True), True)
        self.assertIs(m.as_bool(False), False)

    def test_null_is_false(self):
        """A freshly linked mesh mirror reads null for fields it has never had set."""
        self.assertIs(m.as_bool(None), False)

    def test_string_forms_the_hub_uses(self):
        for value in ("true", "TRUE", "on", "yes", "1"):
            self.assertIs(m.as_bool(value), True, value)
        for value in ("false", "off", "no", "0", ""):
            self.assertIs(m.as_bool(value), False, value)

    def test_unreadable_value_raises(self):
        with self.assertRaises(HubError):
            m.as_bool("maybe")


class TestBuildForm(unittest.TestCase):
    def test_true_checkbox_is_on(self):
        pairs = dict(m.build_form(full_json(meshEnabled=True)))
        self.assertEqual(pairs["meshEnabled"], "on")

    def test_false_checkbox_is_omitted_entirely(self):
        """The bug: a false checkbox field must not appear in the form at all. Emitting the literal
        string "false" (or "true") is what cleared retryEnabled/homeKitEnabled on device 326."""
        keys = [k for k, _ in m.build_form(full_json(homeKitEnabled=False))]
        self.assertNotIn("homeKitEnabled", keys)

    def test_all_four_booleans_use_the_same_encoding(self):
        """There is no second encoding on this form — the claim this script was written to kill."""
        on = dict(m.build_form(full_json(meshEnabled=True, meshFullSync=True,
                                         retryEnabled=True, homeKitEnabled=True)))
        for field in ("meshEnabled", "meshFullSync", "retryEnabled", "homeKitEnabled"):
            self.assertEqual(on[field], "on", field)
        off_keys = [k for k, _ in m.build_form(full_json(
            meshEnabled=False, meshFullSync=False, retryEnabled=False, homeKitEnabled=False))]
        for field in ("meshEnabled", "meshFullSync", "retryEnabled", "homeKitEnabled"):
            self.assertNotIn(field, off_keys, field)

    def test_no_form_value_is_ever_the_literal_true_or_false(self):
        pairs = m.build_form(full_json(meshEnabled=True, homeKitEnabled=False))
        for key, value in pairs:
            self.assertNotIn(value, ("true", "false"), f"{key} serialized as a JSON-style literal")

    def test_every_non_checkbox_field_is_sent(self):
        """Omitting a key clears it, so the form is always whole."""
        keys = {k for k, _ in m.build_form(full_json())}
        for field in m.FORM_FIELDS:
            if field not in m.CHECKBOX_FIELDS:
                self.assertIn(field, keys, field)

    def test_field_order_matches_the_hub_form(self):
        """All checkboxes true, so every field is present and order is the hub's own."""
        keys = [k for k, _ in m.build_form(full_json(
            meshEnabled=True, meshFullSync=True, retryEnabled=True, homeKitEnabled=True))]
        self.assertEqual(keys, list(m.FORM_FIELDS))

    def test_null_scalar_becomes_empty_string(self):
        pairs = dict(m.build_form(full_json(label=None)))
        self.assertEqual(pairs["label"], "")

    def test_changes_override_the_read_value(self):
        pairs = dict(m.build_form(full_json(label="Old"), {"label": "New"}))
        self.assertEqual(pairs["label"], "New")

    def test_change_can_turn_a_checkbox_on(self):
        pairs = dict(m.build_form(full_json(homeKitEnabled=False), {"homeKitEnabled": True}))
        self.assertEqual(pairs["homeKitEnabled"], "on")

    def test_unknown_change_field_is_rejected(self):
        with self.assertRaises(HubError) as ctx:
            m.build_form(full_json(), {"notAField": "x"})
        self.assertIn("notAField", str(ctx.exception))

    def test_missing_device_record_raises(self):
        with self.assertRaises(HubError):
            m.build_form({})


class TestEncodeForm(unittest.TestCase):
    def test_encodes_ordered_pairs(self):
        self.assertEqual(m.encode_form([("a", "1"), ("b", "x y")]), "a=1&b=x+y")


class TestClassifyDrift(unittest.TestCase):
    def test_clean_round_trip_reports_nothing(self):
        before = m.read_fields(full_json())
        _, benign, unexpected = m.classify_drift(before, dict(before))
        self.assertEqual(benign, {})
        self.assertEqual(unexpected, {})

    def test_version_bump_is_not_drift(self):
        before = m.read_fields(full_json(version=41))
        after = m.read_fields(full_json(version=42))
        _, _, unexpected = m.classify_drift(before, after)
        self.assertEqual(unexpected, {})

    def test_fresh_mirror_normalizations_are_benign(self):
        """A freshly linked mesh mirror is born label=null, roomId=null and the hub normalizes
        both on the first round-trip. Reads as drift, is not."""
        before = m.read_fields(full_json(label=None, roomId=None))
        after = m.read_fields(full_json(label="", roomId=0))
        _, benign, unexpected = m.classify_drift(before, after)
        self.assertEqual(set(benign), {"label", "roomId"})
        self.assertEqual(unexpected, {})

    def test_a_cleared_boolean_is_unexpected_drift(self):
        """The device-326 failure, as the script would now report it."""
        before = m.read_fields(full_json(retryEnabled=True, homeKitEnabled=True))
        after = m.read_fields(full_json(retryEnabled=False, homeKitEnabled=False))
        _, _, unexpected = m.classify_drift(before, after)
        self.assertEqual(set(unexpected), {"retryEnabled", "homeKitEnabled"})
        self.assertEqual(unexpected["homeKitEnabled"], {"before": True, "after": False})

    def test_normalization_in_the_other_direction_is_not_benign(self):
        before = m.read_fields(full_json(roomId=0))
        after = m.read_fields(full_json(roomId=None))
        _, benign, unexpected = m.classify_drift(before, after)
        self.assertEqual(benign, {})
        self.assertIn("roomId", unexpected)


class TestClassifyDriftWithChanges(unittest.TestCase):
    def test_a_landed_change_is_applied_not_drift(self):
        before = m.read_fields(full_json(label="Old"))
        after = m.read_fields(full_json(label="New"))
        applied, _, unexpected = m.classify_drift(before, after, {"label": "New"})
        self.assertEqual(applied["label"], {"before": "Old", "after": "New"})
        self.assertEqual(unexpected, {})

    def test_requested_value_compares_across_types(self):
        """The caller types roomId=8; the hub reads it back as the integer 8."""
        before = m.read_fields(full_json(roomId=7))
        after = m.read_fields(full_json(roomId=8))
        applied, _, unexpected = m.classify_drift(before, after, {"roomId": "8"})
        self.assertIn("roomId", applied)
        self.assertEqual(unexpected, {})

    def test_a_landed_checkbox_change_is_applied(self):
        before = m.read_fields(full_json(homeKitEnabled=False))
        after = m.read_fields(full_json(homeKitEnabled=True))
        applied, _, unexpected = m.classify_drift(before, after, {"homeKitEnabled": True})
        self.assertIn("homeKitEnabled", applied)
        self.assertEqual(unexpected, {})

    def test_a_change_the_hub_ignored_is_unexpected_drift(self):
        """Silently not honouring the write is the failure this bucket must still catch."""
        before = m.read_fields(full_json(label="Old"))
        after = m.read_fields(full_json(label="Old"))
        _, _, unexpected = m.classify_drift(before, after, {"label": "New"})
        self.assertEqual(unexpected["label"]["requested"], "New")

    def test_collateral_drift_on_an_untouched_field_still_caught(self):
        before = m.read_fields(full_json(label="Old", meshEnabled=True))
        after = m.read_fields(full_json(label="New", meshEnabled=False))
        applied, _, unexpected = m.classify_drift(before, after, {"label": "New"})
        self.assertIn("label", applied)
        self.assertIn("meshEnabled", unexpected)


class TestParseSet(unittest.TestCase):
    def test_plain_assignment(self):
        self.assertEqual(m.parse_set(["label=Kitchen Leak"]), {"label": "Kitchen Leak"})

    def test_checkbox_assignment_is_coerced_to_bool(self):
        self.assertEqual(m.parse_set(["homeKitEnabled=true"]), {"homeKitEnabled": True})
        self.assertEqual(m.parse_set(["meshEnabled=false"]), {"meshEnabled": False})

    def test_value_containing_equals_is_kept_whole(self):
        self.assertEqual(m.parse_set(["notes=a=b"]), {"notes": "a=b"})

    def test_setting_id_is_rejected(self):
        """--set id=<other> would POST to a different device than the one verification re-reads."""
        with self.assertRaises(HubError) as ctx:
            m.parse_set(["id=999"])
        self.assertIn("--device", str(ctx.exception))

    def test_setting_version_is_rejected(self):
        """The stamp is read fresh immediately before the POST; overriding it defeats the check."""
        with self.assertRaises(HubError):
            m.parse_set(["version=1"])

    def test_unknown_field_is_rejected_before_any_hub_call(self):
        with self.assertRaises(HubError) as ctx:
            m.parse_set(["notAField=x"])
        self.assertIn("notAField", str(ctx.exception))

    def test_missing_equals_raises(self):
        with self.assertRaises(HubError):
            m.parse_set(["label"])

    def test_none_is_empty(self):
        self.assertEqual(m.parse_set(None), {})


class TestFetchAndPost(unittest.TestCase):
    def test_fetch_returns_parsed_json(self):
        t = FakeTransport([(200, {}, json.dumps(full_json()))])
        self.assertEqual(m.fetch_full_json("http://h:8080", 953, t)["device"]["id"], 953)

    def test_fetch_non_200_raises_actionable_message(self):
        t = FakeTransport([(404, {}, "")])
        with self.assertRaises(HubError) as ctx:
            m.fetch_full_json("http://h:8080", 953, t)
        self.assertIn("Hub Security", str(ctx.exception))

    def test_fetch_non_json_raises_actionable_message(self):
        t = FakeTransport([(200, {}, "<html>")])
        with self.assertRaises(HubError) as ctx:
            m.fetch_full_json("http://h:8080", 953, t)
        self.assertIn("Hub Security", str(ctx.exception))

    def test_fetch_valid_json_of_the_wrong_shape_raises(self):
        """`null` and `[]` are valid JSON and used to crash at full.get(...)."""
        for body in ("null", "[]", '"a string"', "{}"):
            t = FakeTransport([(200, {}, body)])
            with self.assertRaises(HubError) as ctx:
                m.fetch_full_json("http://h:8080", 953, t)
            self.assertIn("device", str(ctx.exception).lower(), body)

    def test_fetch_rejects_a_non_object_device_member(self):
        t = FakeTransport([(200, {}, json.dumps({"device": []}))])
        with self.assertRaises(HubError):
            m.fetch_full_json("http://h:8080", 953, t)

    def test_post_accepts_200_and_302(self):
        for status in (200, 302):
            t = FakeTransport([(status, {}, "")])
            self.assertEqual(m.post_update("http://h:8080", [("id", "953")], t), status)

    def test_post_sends_form_urlencoded(self):
        t = FakeTransport([(200, {}, "")])
        m.post_update("http://h:8080", [("id", "953"), ("meshEnabled", "on")], t)
        self.assertEqual(t.calls[0]["content_type"], "application/x-www-form-urlencoded")
        self.assertEqual(t.calls[0]["body"], "id=953&meshEnabled=on")

    def test_post_failure_names_the_version_stamp(self):
        t = FakeTransport([(500, {}, "")])
        with self.assertRaises(HubError) as ctx:
            m.post_update("http://h:8080", [], t)
        self.assertIn("version", str(ctx.exception))


class TestVersionAdvanced(unittest.TestCase):
    def test_bumped_stamp_is_an_applied_write(self):
        self.assertTrue(m.version_advanced({"version": 41}, {"version": 42}))

    def test_unchanged_stamp_is_not(self):
        """A stale-stamp rejection answers 200 and changes nothing — success-shaped."""
        self.assertFalse(m.version_advanced({"version": 41}, {"version": 41}))

    def test_backwards_stamp_is_not(self):
        self.assertFalse(m.version_advanced({"version": 42}, {"version": 41}))

    def test_string_stamps_compare_numerically(self):
        self.assertTrue(m.version_advanced({"version": "9"}, {"version": "10"}))

    def test_missing_stamp_is_not_an_applied_write(self):
        self.assertFalse(m.version_advanced({"version": None}, {"version": 42}))
        self.assertFalse(m.version_advanced({}, {}))


class TestMain(unittest.TestCase):
    def test_noop_round_trip_is_clean_and_exits_zero(self):
        body = json.dumps(full_json())
        after = json.dumps(full_json(version=42))
        t = FakeTransport([(200, {}, body), (200, {}, ""), (200, {}, after)])
        rc = m.main(["--ip", "192.0.2.11", "--device", "953", "--noop"], transport=t)
        self.assertEqual(rc, 0)
        self.assertEqual(t.calls[1]["method"], "POST")

    def test_noop_reads_version_fresh_before_posting(self):
        """The GET must precede the POST — a cached version stamp is the concurrency bug."""
        t = FakeTransport([(200, {}, json.dumps(full_json())), (200, {}, ""),
                           (200, {}, json.dumps(full_json(version=42)))])
        m.main(["--ip", "192.0.2.11", "--device", "953", "--noop"], transport=t)
        self.assertEqual(t.calls[0]["method"], "GET")
        self.assertIn("version=41", t.calls[1]["body"])

    def test_unexpected_drift_exits_one(self):
        t = FakeTransport([
            (200, {}, json.dumps(full_json(retryEnabled=True))),
            (200, {}, ""),
            (200, {}, json.dumps(full_json(retryEnabled=False, version=42))),
        ])
        rc = m.main(["--ip", "192.0.2.11", "--device", "953", "--noop"], transport=t)
        self.assertEqual(rc, 1)

    def test_benign_normalization_alone_exits_zero(self):
        t = FakeTransport([
            (200, {}, json.dumps(full_json(label=None, roomId=None))),
            (200, {}, ""),
            (200, {}, json.dumps(full_json(label="", roomId=0, version=42))),
        ])
        rc = m.main(["--ip", "192.0.2.11", "--device", "1694", "--noop"], transport=t)
        self.assertEqual(rc, 0)

    def test_successful_set_update_exits_zero(self):
        """A requested change that lands is success, not drift — exit 0."""
        t = FakeTransport([
            (200, {}, json.dumps(full_json(label="Old"))),
            (200, {}, ""),
            (200, {}, json.dumps(full_json(label="New", version=42))),
        ])
        rc = m.main(["--ip", "192.0.2.11", "--device", "953", "--set", "label=New"], transport=t)
        self.assertEqual(rc, 0)

    def test_set_that_does_not_land_exits_one(self):
        t = FakeTransport([
            (200, {}, json.dumps(full_json(label="Old"))),
            (200, {}, ""),
            (200, {}, json.dumps(full_json(label="Old", version=42))),
        ])
        rc = m.main(["--ip", "192.0.2.11", "--device", "953", "--set", "label=New"], transport=t)
        self.assertEqual(rc, 1)

    def test_setting_a_checkbox_on_sends_on_and_exits_zero(self):
        t = FakeTransport([
            (200, {}, json.dumps(full_json(homeKitEnabled=False))),
            (200, {}, ""),
            (200, {}, json.dumps(full_json(homeKitEnabled=True, version=42))),
        ])
        rc = m.main(["--ip", "192.0.2.11", "--device", "953",
                     "--set", "homeKitEnabled=true"], transport=t)
        self.assertEqual(rc, 0)
        self.assertIn("homeKitEnabled=on", t.calls[1]["body"])

    def test_dry_run_posts_nothing(self):
        t = FakeTransport([(200, {}, json.dumps(full_json()))])
        rc = m.main(["--ip", "192.0.2.11", "--device", "953", "--dry-run",
                     "--set", "label=New"], transport=t)
        self.assertEqual(rc, 0)
        self.assertEqual([c["method"] for c in t.calls], ["GET"])

    def test_no_action_requested_exits_two(self):
        self.assertEqual(m.main(["--ip", "192.0.2.11", "--device", "953"],
                                transport=FakeTransport([])), 2)

    def test_noop_with_dry_run_is_rejected(self):
        """Contradictory modes: --noop posts, --dry-run does not."""
        rc = m.main(["--ip", "192.0.2.11", "--device", "953", "--noop", "--dry-run"],
                    transport=FakeTransport([]))
        self.assertEqual(rc, 2)

    def test_dry_run_with_set_is_allowed(self):
        t = FakeTransport([(200, {}, json.dumps(full_json()))])
        rc = m.main(["--ip", "192.0.2.11", "--device", "953", "--dry-run",
                     "--set", "maxEvents=1000"], transport=t)
        self.assertEqual(rc, 0)

    def test_noop_with_set_is_rejected(self):
        rc = m.main(["--ip", "192.0.2.11", "--device", "953", "--noop", "--set", "label=X"],
                    transport=FakeTransport([]))
        self.assertEqual(rc, 2)

    def test_unbumped_version_is_a_failed_write(self):
        """The hub answered 200 but the stamp never moved: nothing was applied. Reporting this as
        a clean round-trip would certify a write that did not happen."""
        t = FakeTransport([
            (200, {}, json.dumps(full_json(version=41))),
            (200, {}, ""),
            (200, {}, json.dumps(full_json(version=41))),
        ])
        rc = m.main(["--ip", "192.0.2.11", "--device", "953", "--noop"], transport=t)
        self.assertEqual(rc, 1)

    def test_dry_run_emits_the_full_result_schema(self):
        """The output shape does not vary by mode."""
        import contextlib
        import io as _io
        t = FakeTransport([(200, {}, json.dumps(full_json()))])
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            m.main(["--ip", "192.0.2.11", "--device", "953", "--dry-run",
                    "--set", "label=New"], transport=t)
        result = json.loads(buf.getvalue())
        self.assertEqual(set(result), {"hub", "device_id", "mode", "form", "posted",
                                       "applied", "benign_normalization", "unexpected_drift"})

    def test_posted_result_has_the_same_schema(self):
        import contextlib
        import io as _io
        t = FakeTransport([(200, {}, json.dumps(full_json())), (200, {}, ""),
                           (200, {}, json.dumps(full_json(version=42)))])
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            m.main(["--ip", "192.0.2.11", "--device", "953", "--noop"], transport=t)
        result = json.loads(buf.getvalue())
        self.assertEqual(set(result), {"hub", "device_id", "mode", "form", "posted",
                                       "applied", "benign_normalization", "unexpected_drift"})

    def test_setting_id_exits_two_and_posts_nothing(self):
        t = FakeTransport([])
        rc = m.main(["--ip", "192.0.2.11", "--device", "953", "--set", "id=999"], transport=t)
        self.assertEqual(rc, 2)
        self.assertEqual(t.calls, [])

    def test_setting_version_exits_two_and_posts_nothing(self):
        t = FakeTransport([])
        rc = m.main(["--ip", "192.0.2.11", "--device", "953", "--set", "version=1"], transport=t)
        self.assertEqual(rc, 2)
        self.assertEqual(t.calls, [])

    def test_unknown_set_field_exits_two_without_contacting_the_hub(self):
        t = FakeTransport([])
        rc = m.main(["--ip", "192.0.2.11", "--device", "953", "--set", "notAField=x"], transport=t)
        self.assertEqual(rc, 2)
        self.assertEqual(t.calls, [])

    def test_malformed_set_exits_two(self):
        """An argument error is exit 2, not the exit 1 a hub failure gets."""
        rc = m.main(["--ip", "192.0.2.11", "--device", "953", "--set", "label"],
                    transport=FakeTransport([]))
        self.assertEqual(rc, 2)

    def test_unexpected_drift_still_emits_the_json_result(self):
        """Exit 1 from drift prints the result: the buckets are the diagnosis."""
        import contextlib
        import io as _io
        t = FakeTransport([
            (200, {}, json.dumps(full_json(retryEnabled=True))),
            (200, {}, ""),
            (200, {}, json.dumps(full_json(retryEnabled=False, version=42))),
        ])
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = m.main(["--ip", "192.0.2.11", "--device", "953", "--noop"], transport=t)
        self.assertEqual(rc, 1)
        self.assertIn("retryEnabled", json.loads(buf.getvalue())["unexpected_drift"])

    def test_hub_error_emits_no_json(self):
        import contextlib
        import io as _io
        t = FakeTransport([(404, {}, "")])
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = m.main(["--ip", "192.0.2.11", "--device", "953", "--noop"], transport=t)
        self.assertEqual(rc, 1)
        self.assertEqual(buf.getvalue(), "")

    def test_fetch_error_exits_one(self):
        t = FakeTransport([(404, {}, "")])
        rc = m.main(["--ip", "192.0.2.11", "--device", "953", "--noop"], transport=t)
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
