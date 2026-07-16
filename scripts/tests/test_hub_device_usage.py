#!/usr/bin/env python3
"""Tests for scripts/hub_device_usage.py — pure projection of /device/fullJson into a device's
blast radius, plus the fetch error paths. No live hub.

The fixtures mirror the real /device/fullJson/<id> shape verified live on 2.5.1.128 (C-8 Pro):
appsUsing entries carry {id, name, label, trueLabel, disabled}; appsUsingCount is a STRING;
childDevices is a dict {parentId: [child objects]}."""

import importlib.util
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


class FakeTransport:
    """Callable transport: returns a fixed (status, headers, text) for any call."""

    def __init__(self, status, text):
        self.status, self.text = status, text

    def __call__(self, method, url, body):
        return self.status, {}, self.text


class TestFetch(unittest.TestCase):
    def test_ok(self):
        body = json.dumps(full_json(appsUsingCount="2"))
        out = m.fetch("http://h:8080", 252, transport=FakeTransport(200, body))
        self.assertEqual(out["appsUsingCount"], "2")

    def test_non_200_raises(self):
        with self.assertRaises(m.HubError):
            m.fetch("http://h:8080", 999, transport=FakeTransport(404, "Not Found"))

    def test_non_json_raises(self):
        with self.assertRaises(m.HubError):
            m.fetch("http://h:8080", 252, transport=FakeTransport(200, "<html>login</html>"))


class TestMain(unittest.TestCase):
    def test_missing_hub_target_exits_2(self):
        self.assertEqual(m.main(["--device", "252"]), 2)


if __name__ == "__main__":
    unittest.main()
