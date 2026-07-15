#!/usr/bin/env python3
"""Tests for scripts/hub_mesh.py — pure parse/rank/flag logic over fixture mesh JSON.

Fixtures mirror the real /hub/zwaveDetails/json and /hub/zigbeeDetails/json shapes verified
on 2.5.1.125 (both Z-Wave backends). `now` is injected so activity-age assertions are
deterministic — no wall-clock reads (testing-standards)."""

import importlib.util
import unittest
from datetime import datetime, timezone
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "hub_mesh.py"
spec = importlib.util.spec_from_file_location("hub_mesh", SCRIPT)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

NOW = datetime(2026, 7, 15, 18, 0, 0, tzinfo=timezone.utc)


def zw_node(**kw):
    base = {"nodeId": 10, "deviceId": 100, "deviceName": "Dev", "nodeState": "OK",
            "per": 0, "averageRtt": "30.0", "lwrRssi": "-50db", "neighbors": 5,
            "routeChanges": 0, "route": "01 -> 0A", "security": "S2_Authenticated"}
    base.update(kw)
    return base


def zwave_details(nodes, zwavejs=True, **kw):
    return {"zwaveJS": zwavejs, "healthy": True, "firmwareVersion": "8.10", "nodes": nodes, **kw}


def zb_device(**kw):
    base = {"id": 1, "name": "Outlet", "type": "Generic Zigbee Outlet", "active": True,
            "ping": True, "messageCount": 50, "lastActivity": "2026-07-15T17:00:00+0000",
            "lastMessage": "2026-07-15T17:00:00+0000"}
    base.update(kw)
    return base


def zigbee_details(devices, **kw):
    base = {"enabled": True, "networkState": "ONLINE", "healthy": True, "channel": 25,
            "weakChannel": False, "powerLevel": 8, "devices": devices}
    base.update(kw)
    return base


class TestParsers(unittest.TestCase):
    def test_rssi_negative_dbm(self):
        self.assertEqual(m.parse_rssi("-78db"), -78.0)

    def test_rssi_positive_above_noise(self):
        self.assertEqual(m.parse_rssi("27dB"), 27.0)

    def test_rssi_unparseable_is_none(self):
        for v in ("", "Unknown", None):
            self.assertIsNone(m.parse_rssi(v))

    def test_num_na_and_empty_are_none(self):
        self.assertIsNone(m.parse_num("N/A"))
        self.assertIsNone(m.parse_num(""))
        self.assertEqual(m.parse_num("59.6"), 59.6)

    def test_ts_offset_without_colon(self):
        dt = m.parse_ts("2026-07-15T17:00:00+0000")
        self.assertEqual(dt, datetime(2026, 7, 15, 17, 0, 0, tzinfo=timezone.utc))

    def test_ts_naive_assumed_utc(self):
        dt = m.parse_ts("2026-07-15T11:32:42.136")
        self.assertEqual(dt.tzinfo, timezone.utc)

    def test_ts_none(self):
        self.assertIsNone(m.parse_ts(None))


class TestBackendAndRssiHeuristic(unittest.TestCase):
    def test_backend_detection(self):
        self.assertEqual(m.zwave_backend({"zwaveJS": True}), "zwavejs")
        self.assertEqual(m.zwave_backend({"zwaveJS": False}), "legacy")

    def test_zwavejs_near_floor_flagged(self):
        # -105 dBm is within 10 dB of the 800-series -110 floor
        h = m.rssi_heuristic(-105.0, "zwavejs", "800")
        self.assertIsNotNone(h)
        self.assertEqual(h["signal"], "rssi_near_floor")

    def test_zwavejs_strong_signal_not_flagged(self):
        self.assertIsNone(m.rssi_heuristic(-45.0, "zwavejs", "800"))

    def test_legacy_at_or_below_noise_flagged(self):
        h = m.rssi_heuristic(-3.0, "legacy")
        self.assertEqual(h["signal"], "rssi_at_or_below_noise")

    def test_legacy_positive_not_flagged(self):
        self.assertIsNone(m.rssi_heuristic(27.0, "legacy"))

    def test_same_number_different_verdict_across_backends(self):
        # The load-bearing gotcha: a value good on one scale is not evaluated on the other's.
        self.assertIsNone(m.rssi_heuristic(-40.0, "zwavejs", "800"))  # strong dBm
        self.assertEqual(m.rssi_heuristic(-40.0, "legacy")["signal"], "rssi_at_or_below_noise")


class TestAnalyzeZwave(unittest.TestCase):
    def test_failed_node_flagged_critical(self):
        d = zwave_details([zw_node(nodeId=1, nodeState="OK"),
                           zw_node(nodeId=2, nodeState="FAILED", deviceName="Ghost")])
        r = m.analyze_zwave(d)
        self.assertEqual([n["nodeId"] for n in r["failed"]], [2])
        self.assertEqual(r["failed"][0]["deviceName"], "Ghost")

    def test_packet_errors_ranked_worst_first(self):
        d = zwave_details([zw_node(nodeId=1, per=5), zw_node(nodeId=2, per=0),
                           zw_node(nodeId=3, per=300)])
        r = m.analyze_zwave(d)
        self.assertEqual([n["nodeId"] for n in r["packet_errors"]], [3, 1])  # 0 excluded

    def test_by_rtt_ranking_slowest_first(self):
        d = zwave_details([zw_node(nodeId=1, averageRtt="30"), zw_node(nodeId=2, averageRtt="150"),
                           zw_node(nodeId=3, averageRtt="")])  # empty RTT dropped from ranking
        r = m.analyze_zwave(d)
        self.assertEqual([n["nodeId"] for n in r["ranked"]["by_rtt_ms"]], [2, 1])

    def test_zwavejs_rssi_ranking_lowest_dbm_worst(self):
        d = zwave_details([zw_node(nodeId=1, lwrRssi="-40db"), zw_node(nodeId=2, lwrRssi="-90db")],
                          zwavejs=True)
        r = m.analyze_zwave(d)
        self.assertEqual(r["backend"], "zwavejs")
        self.assertEqual([n["nodeId"] for n in r["ranked"]["by_rssi"]], [2, 1])  # -90 worst

    def test_legacy_rssi_ranking_lowest_above_noise_worst(self):
        d = zwave_details([zw_node(nodeId=1, lwrRssi="30dB"), zw_node(nodeId=2, lwrRssi="5dB")],
                          zwavejs=False)
        r = m.analyze_zwave(d)
        self.assertEqual(r["backend"], "legacy")
        self.assertEqual([n["nodeId"] for n in r["ranked"]["by_rssi"]], [2, 1])  # 5 (near noise) worst


class TestAnalyzeZigbee(unittest.TestCase):
    def test_weak_channel_is_network_problem(self):
        r = m.analyze_zigbee(zigbee_details([], weakChannel=True, channel=20), NOW)
        self.assertIn("weakChannel on channel 20", r["network_problems"])

    def test_offline_and_unhealthy_and_disabled(self):
        r = m.analyze_zigbee(zigbee_details([], networkState="OFFLINE", healthy=False,
                                            enabled=False), NOW)
        self.assertEqual(len(r["network_problems"]), 3)

    def test_dead_device_and_incomplete_join(self):
        devs = [zb_device(id=1, active=True),
                zb_device(id=2, active=False, name="Device", type="Device",
                          lastActivity=None, lastMessage=None, messageCount=0)]
        r = m.analyze_zigbee(zigbee_details(devs), NOW)
        self.assertEqual([d["id"] for d in r["dead_devices"]], [2])
        self.assertTrue(r["dead_devices"][0]["likely_incomplete_join"])

    def test_activity_age_injected_now(self):
        d = zb_device(id=9, lastActivity="2026-07-15T16:00:00+0000")  # 2h before NOW
        r = m.analyze_zigbee(zigbee_details([d]), NOW)
        self.assertEqual(r["stalest"][0]["age_seconds"], 7200)


class TestAnalyzeRollup(unittest.TestCase):
    def test_summary_counts_across_radios(self):
        zw = zwave_details([zw_node(nodeState="FAILED"), zw_node(per=10)])
        zb = zigbee_details([zb_device(active=False)], weakChannel=True)
        r = m.analyze(zw, zb, NOW)
        # 1 failed + 1 weakChannel = 2 critical; 1 PER + 1 dead device = 2 warnings
        self.assertEqual(r["summary"], {"critical": 2, "warnings": 2})

    def test_missing_radio_is_none(self):
        r = m.analyze(zwave_details([zw_node()]), None, NOW)
        self.assertIsNone(r["zigbee"])
        self.assertIsNotNone(r["zwave"])


if __name__ == "__main__":
    unittest.main()
