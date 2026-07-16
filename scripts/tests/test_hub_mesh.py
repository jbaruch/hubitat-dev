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
            "routeChanges": 0, "route": "01 -> 0A", "security": "S2_Authenticated",
            "lastTime": "2026-07-15T17:00:00+0000"}
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
        r = m.analyze_zwave(d, NOW)
        self.assertEqual([n["nodeId"] for n in r["failed"]], [2])
        self.assertEqual(r["failed"][0]["deviceName"], "Ghost")

    def test_failed_splits_orphan_ghost_vs_unreachable_device(self):
        # FAILED + deviceId = real device (unreachable, don't delete); FAILED + no deviceId = ghost
        d = zwave_details([zw_node(nodeId=1, nodeState="FAILED", deviceId=300, deviceName="Real Light"),
                           zw_node(nodeId=2, nodeState="FAILED", deviceId=None, deviceName="Device")])
        r = m.analyze_zwave(d, NOW)
        self.assertEqual([n["nodeId"] for n in r["unreachable_devices"]], [1])
        self.assertEqual([n["nodeId"] for n in r["orphan_ghosts"]], [2])
        kinds = {n["nodeId"]: n["failure_kind"] for n in r["failed"]}
        self.assertEqual(kinds, {1: "unreachable_device", 2: "orphan_ghost"})

    def test_packet_errors_ranked_worst_first(self):
        d = zwave_details([zw_node(nodeId=1, per=5), zw_node(nodeId=2, per=0),
                           zw_node(nodeId=3, per=300)])
        r = m.analyze_zwave(d, NOW)
        self.assertEqual([n["nodeId"] for n in r["packet_errors"]], [3, 1])  # 0 excluded

    def test_by_rtt_ranking_slowest_first(self):
        d = zwave_details([zw_node(nodeId=1, averageRtt="30"), zw_node(nodeId=2, averageRtt="150"),
                           zw_node(nodeId=3, averageRtt="")])  # empty RTT dropped from ranking
        r = m.analyze_zwave(d, NOW)
        self.assertEqual([n["nodeId"] for n in r["ranked"]["by_rtt_ms"]], [2, 1])

    def test_zwavejs_rssi_ranking_lowest_dbm_worst(self):
        d = zwave_details([zw_node(nodeId=1, lwrRssi="-40db"), zw_node(nodeId=2, lwrRssi="-90db")],
                          zwavejs=True)
        r = m.analyze_zwave(d, NOW)
        self.assertEqual(r["backend"], "zwavejs")
        self.assertEqual([n["nodeId"] for n in r["ranked"]["by_rssi"]], [2, 1])  # -90 worst

    def test_legacy_rssi_ranking_lowest_above_noise_worst(self):
        d = zwave_details([zw_node(nodeId=1, lwrRssi="30dB"), zw_node(nodeId=2, lwrRssi="5dB")],
                          zwavejs=False)
        r = m.analyze_zwave(d, NOW)
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


class TestTopology(unittest.TestCase):
    def test_lr_vs_mesh_by_node_id(self):
        # LR node ids are >= 256; classic mesh is 1..232 (Z-Wave Alliance / Silicon Labs)
        self.assertEqual(m.node_topology(268), "lr")
        self.assertEqual(m.node_topology(256), "lr")
        self.assertEqual(m.node_topology(232), "mesh")
        self.assertEqual(m.node_topology(1), "mesh")

    def test_reserved_gap_is_unknown_not_mesh(self):
        # 233..255 is a reserved gap the spec does not assign — must not fall through to "mesh"
        self.assertEqual(m.node_topology(233), "unknown")
        self.assertEqual(m.node_topology(255), "unknown")
        self.assertEqual(m.node_topology(0), "unknown")

    def test_unknown_when_missing(self):
        self.assertEqual(m.node_topology(None), "unknown")
        self.assertEqual(m.node_topology("x"), "unknown")

    def test_topology_surfaced_on_analyzed_nodes(self):
        d = zwave_details([zw_node(nodeId=268), zw_node(nodeId=100)])
        nodes = {n["nodeId"]: n["topology"] for n in m.analyze_zwave(d, NOW)["ranked"]["by_rssi"]}
        self.assertEqual(nodes[268], "lr")
        self.assertEqual(nodes[100], "mesh")


class TestTimestampBackendSplit(unittest.TestCase):
    """The legacy backend stamps lastTime '+0000' (true UTC); zwaveJS emits a NAIVE stamp in
    the hub's local zone. Verified live 2026-07-16 on 2.5.1.128."""

    def test_naive_stamp_localized_to_hub_zone(self):
        # 13:00 naive in America/Chicago (CDT, UTC-5) is 18:00 UTC — NOT 13:00 UTC.
        got = m.parse_ts("2026-07-15T13:00:00.081", m.naive_zone("America/Chicago"))
        self.assertEqual(got, datetime(2026, 7, 15, 18, 0, 0, 81000, tzinfo=timezone.utc))

    def test_naive_stamp_without_zone_falls_back_to_utc(self):
        got = m.parse_ts("2026-07-15T13:00:00.081", None)
        self.assertEqual(got, datetime(2026, 7, 15, 13, 0, 0, 81000, tzinfo=timezone.utc))

    def test_explicit_offset_ignores_hub_zone(self):
        # A legacy '+0000' stamp is already absolute; the hub zone must not shift it again.
        got = m.parse_ts("2026-07-15T18:00:00+0000", m.naive_zone("America/Chicago"))
        self.assertEqual(got, datetime(2026, 7, 15, 18, 0, 0, tzinfo=timezone.utc))

    def test_zwavejs_node_age_not_inflated_by_five_hours(self):
        # Regression: reading the naive zwaveJS stamp as UTC made every node read 5h staler
        # than reality, which would have buried real staleness under a false one.
        d = zwave_details([zw_node(lastTime="2026-07-15T12:30:00.000")], zwavejs=True)
        r = m.analyze_zwave(d, NOW, m.naive_zone("America/Chicago"))
        self.assertEqual(r["stalest"][0]["age_seconds"], 1800)  # 17:30 UTC -> 18:00 NOW

    def test_hub_timezone_reads_string_dict_and_absent(self):
        self.assertEqual(m.hub_timezone({"timeZone": "America/Chicago"}), "America/Chicago")
        self.assertEqual(m.hub_timezone({"timeZone": {"ID": "Europe/Riga"}}), "Europe/Riga")
        self.assertIsNone(m.hub_timezone({}))
        self.assertIsNone(m.hub_timezone(None))

    def test_unknown_zone_degrades_to_none_not_crash(self):
        self.assertIsNone(m.naive_zone("Mars/Olympus_Mons"))
        self.assertIsNone(m.naive_zone(None))


class TestZwaveStaleness(unittest.TestCase):
    def test_age_seconds_surfaced_per_node(self):
        d = zwave_details([zw_node(lastTime="2026-07-15T17:00:00+0000")])
        self.assertEqual(m.analyze_zwave(d, NOW)["stalest"][0]["age_seconds"], 3600)

    def test_stalest_ranks_oldest_first(self):
        d = zwave_details([
            zw_node(nodeId=1, lastTime="2026-07-15T17:59:00+0000"),   # 1 min
            zw_node(nodeId=2, lastTime="2026-07-15T04:00:00+0000"),   # 14 h
            zw_node(nodeId=3, lastTime="2026-07-15T17:00:00+0000"),   # 1 h
        ])
        self.assertEqual([n["nodeId"] for n in m.analyze_zwave(d, NOW)["stalest"]], [2, 3, 1])

    def test_never_heard_node_flagged_though_state_is_ok(self):
        # nodeState OK + no lastTime: passes every radio check, yet the hub has never heard it.
        d = zwave_details([zw_node(nodeId=27, nodeState="OK", lastTime=None, msgCount=0),
                           zw_node(nodeId=10)])
        r = m.analyze_zwave(d, NOW)
        self.assertEqual([n["nodeId"] for n in r["never_heard"]], [27])
        self.assertEqual(r["failed"], [])                    # and it is NOT a FAILED node

    def test_never_heard_node_excluded_from_stalest_ranking(self):
        # No timestamp means unknown age, not infinite age — it must not outrank real staleness.
        d = zwave_details([zw_node(nodeId=27, lastTime=None),
                           zw_node(nodeId=10, lastTime="2026-07-15T04:00:00+0000")])
        self.assertEqual([n["nodeId"] for n in m.analyze_zwave(d, NOW)["stalest"]], [10])


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

    def test_hub_mesh_problems_count_as_critical(self):
        mesh = m.analyze_hub_mesh(hub_mesh_json([peer()]), {"192.168.30.2": UNREACHABLE})
        r = m.analyze(zwave_details([zw_node()]), None, NOW, hub_mesh=mesh)
        # A radio-clean hub is NOT all-clear when a mesh peer cannot carry commands.
        self.assertEqual(r["summary"]["critical"], 1)

    def test_hub_mesh_absent_is_none_and_adds_nothing(self):
        r = m.analyze(zwave_details([zw_node()]), None, NOW, hub_mesh=None)
        self.assertIsNone(r["hub_mesh"])
        self.assertEqual(r["summary"]["critical"], 0)


# --- hub mesh -----------------------------------------------------------------------------
# Shapes mirror /hub2/hubMeshJson verified live on 2.5.1.128 across three hubs (2026-07-16).

def peer(**kw):
    """A peer the hub reports as perfectly healthy — which is exactly what the hub said about
    the dead peer in the grounded outage."""
    base = {"name": "Apps", "hubId": "1ec9f270-bda8-465a-b240-f4ea79d85e4a",
            "ipAddress": "192.168.30.2", "active": True, "offline": False, "warning": None,
            "deviceIds": [6, 57, 471], "lastActive": 1784212155639}
    base.update(kw)
    return base


def hub_mesh_json(peers):
    return {"hubList": peers, "modeHubId": None, "sharedDevices": []}


REACHABLE = {"reachable": True, "hubId": "1ec9f270-bda8-465a-b240-f4ea79d85e4a", "error": None}
UNREACHABLE = {"reachable": False, "hubId": None, "error": "cannot reach"}


class TestAnalyzeHubMesh(unittest.TestCase):
    def test_unreachable_peer_flagged_though_hub_reports_it_healthy(self):
        # The grounded 2026-07-16 outage: peer record held a stale IP from the old subnet and
        # dropped every command, while the hub reported active/offline/warning all clean.
        # Nothing but probing the address finds this — the regression test for the whole PR.
        r = m.analyze_hub_mesh(hub_mesh_json([peer(ipAddress="192.168.1.64")]),
                               {"192.168.1.64": UNREACHABLE})
        self.assertEqual([p["signal"] for p in r["problems"]], ["peer_unreachable"])
        self.assertTrue(r["peers"][0]["active"])       # hub's own fields stayed green ...
        self.assertFalse(r["peers"][0]["offline"])
        self.assertIsNone(r["peers"][0]["warning"])
        self.assertFalse(r["peers"][0]["reachable"])   # ... and the probe is what disagreed

    def test_identity_mismatch_flagged_when_address_reassigned(self):
        r = m.analyze_hub_mesh(hub_mesh_json([peer()]),
                               {"192.168.30.2": {"reachable": True, "hubId": "other-hub-uid",
                                                 "error": None}})
        self.assertEqual([p["signal"] for p in r["problems"]], ["peer_identity_mismatch"])

    def test_healthy_probed_peer_has_no_problems(self):
        r = m.analyze_hub_mesh(hub_mesh_json([peer()]), {"192.168.30.2": REACHABLE})
        self.assertEqual(r["problems"], [])
        self.assertTrue(r["probed"])

    def test_self_reported_faults_flagged_without_probe(self):
        r = m.analyze_hub_mesh(hub_mesh_json([
            peer(name="A", offline=True), peer(name="B", active=False),
            peer(name="C", warning="hub not responding")]), None)
        self.assertEqual([p["signal"] for p in r["problems"]],
                         ["peer_offline", "peer_inactive", "peer_warning"])
        self.assertFalse(r["probed"])

    def test_shared_device_count_surfaced_as_blast_radius(self):
        r = m.analyze_hub_mesh(hub_mesh_json([peer(deviceIds=list(range(148)))]), None)
        self.assertEqual(r["peers"][0]["shared_device_count"], 148)

    def test_no_mesh_json_is_none(self):
        self.assertIsNone(m.analyze_hub_mesh(None, None))


class TestProbePeer(unittest.TestCase):
    def test_unreachable_transport_reports_finding_not_error(self):
        def boom(_method, _url, _body):
            raise m.HubError("cannot reach http://192.168.1.64:8080: timed out")
        r = m.probe_peer("192.168.1.64", 8080, transport=boom)
        self.assertEqual(r["reachable"], False)
        self.assertIsNone(r["hubId"])

    def test_reachable_peer_returns_its_hubUID(self):
        def ok(_method, _url, _body):
            return 200, {}, '{"hubUID": "e6574b36-23fc-4164-acf4-24aed2cc6f72"}'
        r = m.probe_peer("192.168.30.17", 8080, transport=ok)
        self.assertEqual(r, {"reachable": True,
                             "hubId": "e6574b36-23fc-4164-acf4-24aed2cc6f72", "error": None})


if __name__ == "__main__":
    unittest.main()
