#!/usr/bin/env python3
"""Tests for skills/_scripts/hub_device_usage.py — pure projection of /device/fullJson into a device's
blast radius, plus the fetch error paths. No live hub.

The fixtures mirror the real /device/fullJson/<id> shape verified live on 2.5.1.128 (C-8 Pro):
appsUsing entries carry {id, name, label, trueLabel, disabled}; appsUsingCount is a STRING;
childDevices is a dict {parentId: [child objects]}."""

import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "hub_device_usage.py"
spec = importlib.util.spec_from_file_location("hub_device_usage", SCRIPT)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def app(**kw):
    base = {"id": 584, "name": "Notifier", "label": "Notifier", "trueLabel": None, "disabled": False}
    base.update(kw)
    return base


def full_json(**kw):
    """A /device/fullJson body mirroring the live shape."""
    base = {
        "device": {"id": 252, "name": "Generic Zigbee Motion Sensor",
                   "displayName": "Alice Office Closet Motion Sensor",
                   "label": "Alice Office Closet Motion Sensor"},
        "appsUsing": [],
        "appsUsingCount": "0",
        "dashboards": [],
        "parentApp": None,
        "childDevices": {},
        "hasChildren": False,
    }
    base.update(kw)
    return base


class TestParseCount(unittest.TestCase):
    def test_string_count(self):
        self.assertEqual(m.parse_count("2"), 2)

    def test_empty_and_none(self):
        self.assertIsNone(m.parse_count(""))
        self.assertIsNone(m.parse_count(None))

    def test_nonnumeric(self):
        self.assertIsNone(m.parse_count("many"))


class TestNormalizeApp(unittest.TestCase):
    def test_label_falls_back_to_truelabel_then_name(self):
        a = m.normalize_app({"id": 3, "name": "Hub mesh", "label": None, "trueLabel": "Hub mesh"})
        self.assertEqual(a["label"], "Hub mesh")
        a2 = m.normalize_app({"id": 3, "name": "OnlyName"})
        self.assertEqual(a2["label"], "OnlyName")

    def test_disabled_coerced_to_bool(self):
        self.assertIs(m.normalize_app(app(disabled=True))["disabled"], True)
        self.assertIs(m.normalize_app(app())["disabled"], False)


class TestNormalizeChildren(unittest.TestCase):
    def test_flattens_dict_of_lists(self):
        children = {"74": [
            {"id": 75, "displayName": "CH1", "disabled": False},
            {"id": 76, "name": "CH2", "disabled": True},
        ]}
        out = m.normalize_children(children)
        self.assertEqual([c["id"] for c in out], [75, 76])
        self.assertEqual(out[1]["displayName"], "CH2")  # falls back to name
        self.assertIs(out[1]["disabled"], True)

    def test_empty_and_non_dict(self):
        self.assertEqual(m.normalize_children({}), [])
        self.assertEqual(m.normalize_children(None), [])
        self.assertEqual(m.normalize_children([]), [])


class TestAnalyzeUsage(unittest.TestCase):
    def test_splits_enabled_and_disabled_apps(self):
        f = full_json(
            appsUsing=[app(id=1, disabled=False), app(id=2, disabled=True), app(id=3, disabled=False)],
            appsUsingCount="3")
        r = m.analyze_usage(f)
        self.assertEqual([a["id"] for a in r["apps"]["enabled"]], [1, 3])
        self.assertEqual([a["id"] for a in r["apps"]["disabled"]], [2])
        self.assertEqual(r["blast_radius"]["apps_enabled"], 2)
        self.assertEqual(r["blast_radius"]["apps_disabled"], 1)
        self.assertEqual(r["apps_using_count"], 3)

    def test_device_name_and_driver(self):
        r = m.analyze_usage(full_json())
        self.assertEqual(r["device_name"], "Alice Office Closet Motion Sensor")
        self.assertEqual(r["driver"], "Generic Zigbee Motion Sensor")

    def test_parent_app_and_children_counted(self):
        f = full_json(parentApp={"id": 9, "label": "CoCoHue"},
                      childDevices={"74": [{"id": 75, "displayName": "CH1"}]})
        r = m.analyze_usage(f)
        self.assertIs(r["blast_radius"]["has_parent_app"], True)
        self.assertEqual(r["blast_radius"]["child_devices"], 1)
        self.assertEqual(r["parent_app"]["label"], "CoCoHue")

    def test_no_usage_is_clean_blast_radius(self):
        r = m.analyze_usage(full_json())
        self.assertEqual(r["blast_radius"],
                         {"apps_enabled": 0, "apps_disabled": 0, "dashboards": 0,
                          "child_devices": 0, "has_parent_app": False})

    def test_dashboards_passed_through(self):
        f = full_json(dashboards=[{"id": 1, "name": "Main"}])
        self.assertEqual(m.analyze_usage(f)["blast_radius"]["dashboards"], 1)


def status_json(**kw):
    base = {
        "settings": None,
        "appSettings": [],
        "eventSubscriptions": [],
        "state": {},
    }
    base.update(kw)
    return base


class TestAnalyzeLiveness(unittest.TestCase):
    def test_rule_machine_withdrawn_reference_is_configured_not_live(self):
        apps = [m.normalize_app(app(
            id=788, name="Rule Machine", label="Stairs rule", disabled=False))]
        statuses = {
            788: status_json(
                appSettings=[{
                    "name": "tDev-11",
                    "value": None,
                    "deviceIdsForDeviceList": [1612],
                    "deviceList": {"1612": "mZone-Stairs"},
                }],
                eventSubscriptions=[{"typeId": 220, "handler": "allHandlerX"}],
                state={
                    "trigDevs": {"220:Motion": ["1"]},
                    "trigDevsW": {"1612:Motion": []},
                }),
        }

        result = m.analyze_liveness(1612, apps, statuses)

        audited = result["apps"][0]
        self.assertEqual(audited["status"], "not_live")
        self.assertEqual(audited["method"], "rule_machine_trig_devs")
        self.assertFalse(audited["trigger_in_trig_devs"])
        self.assertTrue(audited["trigger_in_trig_devs_w"])
        self.assertEqual(audited["configured_settings"], ["tDev-11"])
        self.assertEqual(audited["rule_machine_trigger_settings"], ["tDev-11"])
        self.assertEqual(result["summary"]["enabled_configured_not_live"], 1)
        self.assertEqual([a["id"] for a in result["enabled_configured_not_live"]], [788])

    def test_required_expression_false_uses_trig_devs_not_subscriptions(self):
        apps = [m.normalize_app(app(
            id=819, name="Rule Machine", label="Motion at Night", disabled=False))]
        statuses = {
            "819": status_json(
                eventSubscriptions=[],
                appState=[{"name": "trigDevs", "value": '{"1580:Motion":["1"]}'}]),
        }

        result = m.analyze_liveness(1580, apps, statuses)

        audited = result["apps"][0]
        self.assertEqual(audited["status"], "live")
        self.assertTrue(audited["trigger_in_trig_devs"])
        self.assertEqual(audited["matching_subscription_count"], 0)
        self.assertEqual(result["summary"]["enabled_configured_not_live"], 0)

    def test_rule_machine_action_reference_absent_from_trig_devs_stays_unknown(self):
        apps = [m.normalize_app(app(
            id=820, name="Rule Machine", label="Control fan", disabled=False))]
        statuses = {
            820: status_json(
                appSettings=[{
                    "name": "actionDevice",
                    "deviceIdsForDeviceList": [1612],
                    "deviceList": {"1612": "Office Fan"},
                }],
                state={"trigDevs": {"220:Motion": ["1"]}}),
        }

        audited = m.analyze_liveness(1612, apps, statuses)["apps"][0]

        self.assertEqual(audited["status"], "unknown")
        self.assertEqual(audited["method"], "rule_machine_non_trigger_reference")
        self.assertFalse(audited["trigger_in_trig_devs"])
        self.assertEqual(audited["rule_machine_trigger_settings"], [])
        self.assertFalse(audited["configured_not_live"])

    def test_rule_machine_non_trigger_subscription_is_live_evidence(self):
        apps = [m.normalize_app(app(
            id=821, name="Rule Machine", label="Watch illuminance", disabled=False))]
        statuses = {
            821: status_json(
                appSettings=[{
                    "name": "conditionDevice",
                    "deviceIdsForDeviceList": [1612],
                    "deviceList": {"1612": "Office Lux"},
                }],
                eventSubscriptions=[{"typeId": 1612}],
                state={"trigDevs": {"220:Motion": ["1"]}}),
        }

        audited = m.analyze_liveness(1612, apps, statuses)["apps"][0]

        self.assertEqual(audited["status"], "live")
        self.assertEqual(audited["method"], "event_subscription")
        self.assertFalse(audited["trigger_in_trig_devs"])

    def test_rule_machine_tdev_setting_is_negative_trigger_evidence(self):
        apps = [m.normalize_app(app(
            id=822, name="Rule Machine", label="Old trigger", disabled=False))]
        statuses = {
            822: status_json(
                appSettings=[{
                    "name": "tDev-4",
                    "deviceIdsForDeviceList": [1612],
                    "deviceList": {"1612": "Old Motion"},
                }],
                state={"trigDevs": {"220:Motion": ["1"]}}),
        }

        audited = m.analyze_liveness(1612, apps, statuses)["apps"][0]

        self.assertEqual(audited["status"], "not_live")
        self.assertEqual(audited["method"], "rule_machine_trig_devs")
        self.assertEqual(audited["rule_machine_trigger_settings"], ["tDev-4"])
        self.assertTrue(audited["configured_not_live"])

    def test_matching_event_subscription_is_positive_live_evidence(self):
        apps = [m.normalize_app(app(id=12, name="Motion Lighting", disabled=False))]
        statuses = {
            12: status_json(
                appSettings=[{
                    "name": "motions",
                    "deviceIdsForDeviceList": [1611],
                    "deviceList": {"1611": "mZone-Study"},
                }],
                eventSubscriptions=[{"typeId": "1611"}, {"typeId": 44}]),
        }

        audited = m.analyze_liveness(1611, apps, statuses)["apps"][0]

        self.assertEqual(audited["status"], "live")
        self.assertEqual(audited["method"], "event_subscription")
        self.assertEqual(audited["matching_subscription_count"], 1)
        self.assertEqual(audited["configured_settings"], ["motions"])

    def test_no_subscription_stays_unknown_for_command_only_consumer(self):
        apps = [m.normalize_app(app(id=13, name="Room Lighting", disabled=False))]
        statuses = {
            13: status_json(appSettings=[{
                "name": "roomDevsL",
                "deviceIdsForDeviceList": [99],
                "deviceList": {"99": "Kitchen Light"},
            }]),
        }

        audited = m.analyze_liveness(99, apps, statuses)["apps"][0]

        self.assertEqual(audited["status"], "unknown")
        self.assertEqual(audited["method"], "no_authoritative_liveness_surface")
        self.assertFalse(audited["configured_not_live"])

    def test_disabled_app_is_not_live_without_status_json(self):
        apps = [m.normalize_app(app(id=14, disabled=True))]

        result = m.analyze_liveness(99, apps, {})

        audited = result["apps"][0]
        self.assertEqual(audited["status"], "not_live")
        self.assertEqual(audited["method"], "app_disabled")
        self.assertEqual(result["summary"],
                         {"live": 0, "not_live": 1, "unknown": 0,
                          "enabled_configured_not_live": 0})

    def test_rule_machine_without_state_stays_unknown(self):
        apps = [m.normalize_app(app(id=15, name="Rule Machine", disabled=False))]

        audited = m.analyze_liveness(99, apps, {15: status_json()})["apps"][0]

        self.assertEqual(audited["status"], "unknown")
        self.assertEqual(audited["method"], "rule_machine_state_unavailable")


def devices_list(*names_ids):
    """Build a /hub2/devicesList body from (name, id) pairs."""
    return {"devices": [{"data": {"id": i, "name": n}} for n, i in names_ids]}


class TestResolveDeviceId(unittest.TestCase):
    def test_exact_match_case_insensitive(self):
        dl = devices_list(("Alice Office Closet Motion Sensor", 252), ("Kitchen Light", 40))
        self.assertEqual(m.resolve_device_id(dl, "alice office closet MOTION sensor"), 252)

    def test_no_match_raises(self):
        with self.assertRaises(m.HubError):
            m.resolve_device_id(devices_list(("Kitchen Light", 40)), "Bedroom Fan")

    def test_ambiguous_match_raises_and_lists(self):
        dl = devices_list(("Lamp", 10), ("Lamp", 11))
        with self.assertRaises(m.HubError) as ctx:
            m.resolve_device_id(dl, "Lamp")
        self.assertIn("10", str(ctx.exception))
        self.assertIn("11", str(ctx.exception))

    def test_empty_list_raises(self):
        with self.assertRaises(m.HubError):
            m.resolve_device_id({"devices": []}, "Anything")

    def test_resolves_a_nested_child_device(self):
        """A child appears ONLY under its parent's children[] — never at the top level. Resolving
        by name has to walk the tree or a child is unreachable by --name (grounded 2.5.1.128)."""
        dl = {"devices": [
            {"data": {"id": 300, "name": "Console Power Strip"},
             "parent": True, "child": False,
             "children": [
                 {"data": {"id": 301, "name": "Power Strip - CH1"}, "parent": False, "child": True},
                 {"data": {"id": 302, "name": "Power Strip - CH2"}, "parent": False, "child": True},
             ]},
        ]}
        self.assertEqual(m.resolve_device_id(dl, "Power Strip - CH2"), 302)
        self.assertEqual(m.resolve_device_id(dl, "Console Power Strip"), 300)

    def test_child_name_colliding_with_top_level_is_ambiguous(self):
        """Walking the tree must not silently prefer the top-level hit."""
        dl = {"devices": [
            {"data": {"id": 10, "name": "Lamp"}},
            {"data": {"id": 20, "name": "Strip"},
             "children": [{"data": {"id": 21, "name": "Lamp"}}]},
        ]}
        with self.assertRaises(m.HubError) as ctx:
            m.resolve_device_id(dl, "Lamp")
        self.assertIn("10", str(ctx.exception))
        self.assertIn("21", str(ctx.exception))

    def test_grandchildren_are_reached(self):
        dl = {"devices": [
            {"data": {"id": 1, "name": "A"},
             "children": [{"data": {"id": 2, "name": "B"},
                           "children": [{"data": {"id": 3, "name": "C"}}]}]},
        ]}
        self.assertEqual(m.resolve_device_id(dl, "C"), 3)

    def test_missing_or_malformed_children_do_not_break_the_walk(self):
        dl = {"devices": [
            {"data": {"id": 1, "name": "A"}, "children": None},
            {"data": {"id": 2, "name": "B"}},
            {"data": {"id": 3, "name": "C"}, "children": ["junk"]},
        ]}
        self.assertEqual(m.resolve_device_id(dl, "C"), 3)


class FakeTransport:
    """Callable transport: returns a fixed (status, headers, text) for any call."""

    def __init__(self, status, text):
        self.status, self.text = status, text

    def __call__(self, method, url, body):
        return self.status, {}, self.text


class RouteTransport:
    """Callable transport routing by path suffix -> (status, text)."""

    def __init__(self, routes):
        self.routes = routes

    def __call__(self, method, url, body):
        path = "/" + url.split("://", 1)[1].split("/", 1)[1]
        for suffix, (status, text) in self.routes.items():
            if path.startswith(suffix):
                return status, {}, text
        raise AssertionError(f"no route for {path}")


class TestFetch(unittest.TestCase):
    def test_ok(self):
        body = json.dumps(full_json(appsUsingCount="2"))
        out = m.fetch("http://h:8080", 252, transport=FakeTransport(200, body))
        self.assertEqual(out["appsUsingCount"], "2")

    def test_non_200_raises(self):
        with self.assertRaises(m.HubError):
            m.fetch("http://h:8080", 999, transport=FakeTransport(404, "Not Found"))

    def test_non_200_message_mentions_hub_security(self):
        with self.assertRaises(m.HubError) as ctx:
            m.fetch("http://h:8080", 999, transport=FakeTransport(302, ""))
        self.assertIn("Hub Security", str(ctx.exception))

    def test_non_json_raises(self):
        with self.assertRaises(m.HubError):
            m.fetch("http://h:8080", 252, transport=FakeTransport(200, "<html>login</html>"))


class TestFetchDevices(unittest.TestCase):
    def test_ok(self):
        body = json.dumps(devices_list(("Kitchen Light", 40)))
        out = m.fetch_devices("http://h:8080", transport=FakeTransport(200, body))
        self.assertEqual(out["devices"][0]["data"]["id"], 40)

    def test_non_200_raises(self):
        with self.assertRaises(m.HubError):
            m.fetch_devices("http://h:8080", transport=FakeTransport(401, "login"))


class TestFetchAppStatus(unittest.TestCase):
    def test_ok(self):
        body = json.dumps(status_json(eventSubscriptions=[{"typeId": 40}]))
        out = m.fetch_app_status("http://h:8080", 12, transport=FakeTransport(200, body))
        self.assertEqual(out["eventSubscriptions"][0]["typeId"], 40)

    def test_non_200_raises(self):
        with self.assertRaises(m.HubError):
            m.fetch_app_status("http://h:8080", 12, transport=FakeTransport(404, "missing"))

    def test_non_object_raises(self):
        with self.assertRaises(m.HubError):
            m.fetch_app_status("http://h:8080", 12, transport=FakeTransport(200, "[]"))

    def test_fetch_statuses_skips_disabled_apps(self):
        calls = []

        def transport(method, url, body):
            calls.append((method, url, body))
            return 200, {}, json.dumps(status_json())

        apps = [
            m.normalize_app(app(id=12, disabled=False)),
            m.normalize_app(app(id=13, disabled=True)),
        ]
        statuses = m.fetch_app_statuses("http://h:8080", apps, transport=transport)

        self.assertEqual(list(statuses), [12])
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][1].endswith("/installedapp/statusJson/12"))


class TestMain(unittest.TestCase):
    def test_missing_hub_target_exits_2(self):
        self.assertEqual(m.main(["--device", "252"]), 2)

    def test_device_and_name_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            m.main(["--ip", "1.2.3.4", "--device", "1", "--name", "x"])

    def test_name_resolution_end_to_end(self):
        """--name resolves via /hub2/devicesList, then fetches /device/fullJson for that id."""
        routes = {
            "/hub2/devicesList": (200, json.dumps(devices_list(("Kitchen Light", 40)))),
            "/device/fullJson/": (200, json.dumps(full_json(appsUsingCount="1"))),
        }
        self.assertEqual(
            m.main(["--ip", "1.2.3.4", "--name", "kitchen light"], transport=RouteTransport(routes)),
            0)

    def test_live_mode_adds_audit(self):
        body = full_json(
            appsUsing=[app(id=788, name="Rule Machine", disabled=False)],
            appsUsingCount="1")
        routes = {
            "/device/fullJson/": (200, json.dumps(body)),
            "/installedapp/statusJson/": (
                200,
                json.dumps(status_json(
                    appSettings=[{
                        "name": "tDev-11",
                        "deviceIdsForDeviceList": [1612],
                        "deviceList": {"1612": "mZone-Stairs"},
                    }],
                    state={"trigDevs": {"220:Motion": ["1"]}}))),
        }
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            rc = m.main(
                ["--ip", "1.2.3.4", "--device", "1612", "--live"],
                transport=RouteTransport(routes))

        self.assertEqual(rc, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["live_audit"]["apps"][0]["status"], "not_live")
        self.assertEqual(payload["live_audit"]["summary"]["enabled_configured_not_live"], 1)


if __name__ == "__main__":
    unittest.main()
