#!/usr/bin/env python3
"""Tests for skills/_scripts/hub_mesh.py — pure parse/rank/flag logic over fixture mesh JSON.

Fixtures mirror the real /hub/zwaveDetails/json and /hub/zigbeeDetails/json shapes verified
on 2.5.1.125 (both Z-Wave backends). `now` is injected so activity-age assertions are
deterministic — no wall-clock reads (testing-standards)."""

import contextlib
import importlib.util
import io
import json
import unittest
from datetime import datetime, timezone
from typing import cast
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
            "routeChanges": 0, "security": "S2_Authenticated",
            "listening": True, "beaming": False,
            "lastTime": "2026-07-15T17:00:00+0000"}
    base.update(kw)
    # The default route is this node's own DIRECT route, derived from its id. A fixed literal
    # made every node claim a path terminating at node 0x0A whatever its own id — the same drift
    # PR #94 fixed in the eval fixture, and it now reads as an incoherent route rather than as a
    # direct one. Tests that care about repeaters pass `route=` explicitly.
    base.setdefault("route", f"01 -> {base['nodeId']:02X}")
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
        self.assertEqual([n["nodeId"] for n in r["ranked"]["by_rssi_direct"]], [2, 1])  # -90 worst

    def test_legacy_rssi_ranking_lowest_above_noise_worst(self):
        d = zwave_details([zw_node(nodeId=1, lwrRssi="30dB"), zw_node(nodeId=2, lwrRssi="5dB")],
                          zwavejs=False)
        r = m.analyze_zwave(d, NOW)
        self.assertEqual(r["backend"], "legacy")
        # 5 (near noise) worst
        self.assertEqual([n["nodeId"] for n in r["ranked"]["by_rssi_direct"]], [2, 1])

    def test_listening_and_beaming_are_surfaced_raw(self):
        d = zwave_details([zw_node(listening=False, beaming=True)])
        node = m.analyze_zwave(d, NOW)["ranked"]["by_rssi_direct"][0]
        self.assertFalse(node["listening"])
        self.assertTrue(node["beaming"])


class TestAnalyzeZigbee(unittest.TestCase):
    def test_weak_channel_is_network_problem(self):
        r = m.analyze_zigbee(zigbee_details([], weakChannel=True, channel=20), NOW)
        self.assertIn("weakChannel on channel 20", r["network_problems"])

    def test_offline_and_unhealthy_and_disabled(self):
        r = m.analyze_zigbee(zigbee_details([], networkState="OFFLINE", healthy=False,
                                            enabled=False), NOW)
        self.assertEqual(len(r["network_problems"]), 3)

    def test_generic_device_is_incomplete_join(self):
        devs = [zb_device(id=2, active=False, name="Device", type="Device",
                          lastActivity=None, lastMessage=None, messageCount=0)]
        r = m.analyze_zigbee(zigbee_details(devs), NOW)
        self.assertEqual([d["id"] for d in r["incomplete_joins"]], [2])
        self.assertTrue(r["incomplete_joins"][0]["likely_incomplete_join"])

    def test_active_false_is_surfaced_but_not_called_dead(self):
        devs = [zb_device(id=2, active=False, name="Outlet", type="Generic Zigbee Outlet")]
        r = m.analyze_zigbee(zigbee_details(devs), NOW)
        self.assertEqual([d["id"] for d in r["active_false_devices"]], [2])
        self.assertEqual(r["incomplete_joins"], [])

    def test_active_true_does_not_hide_stale_last_activity(self):
        devs = [
            zb_device(id=1, active=True, lastActivity="2025-01-01T00:00:00+0000"),
            zb_device(id=2, active=True, lastActivity="2026-07-15T17:00:00+0000"),
        ]
        r = m.analyze_zigbee(zigbee_details(devs), NOW)
        self.assertEqual([d["id"] for d in r["stalest"]], [1, 2])
        self.assertTrue(r["stalest"][0]["active"])

    def test_missing_last_activity_is_unknown_even_when_last_message_exists(self):
        devs = [zb_device(id=3, active=True, lastActivity=None,
                          lastMessage="2026-07-15T17:00:00+0000")]
        r = m.analyze_zigbee(zigbee_details(devs), NOW)
        self.assertEqual([d["id"] for d in r["activity_unknown"]], [3])
        self.assertEqual(r["stalest"], [])

    def test_unparseable_last_activity_is_unknown(self):
        devs = [zb_device(id=4, active=True, lastActivity="not-a-timestamp")]
        r = m.analyze_zigbee(zigbee_details(devs), NOW)
        self.assertEqual([d["id"] for d in r["activity_unknown"]], [4])
        self.assertEqual(r["stalest"], [])

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
        ranked = m.analyze_zwave(d, NOW)["ranked"]["by_rssi_direct"]
        nodes = {n["nodeId"]: n["topology"] for n in ranked}
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

    def test_unparseable_timestamp_is_not_never_heard(self):
        # parse_ts returns None for an unparseable stamp as well as an absent one. Keying
        # never_heard on age_seconds would call a malformed string "the hub has never heard
        # this node" — a diagnosis invented from a parse failure.
        d = zwave_details([zw_node(nodeId=1, lastTime="not-a-timestamp"),
                           zw_node(nodeId=2, lastTime=None),
                           zw_node(nodeId=3, lastTime="")])
        r = m.analyze_zwave(d, NOW)
        self.assertEqual([n["nodeId"] for n in r["never_heard"]], [2, 3])
        self.assertEqual([n["nodeId"] for n in r["unparsed_timestamps"]], [1])

    def test_never_heard_node_excluded_from_stalest_ranking(self):
        # No timestamp means unknown age, not infinite age — it must not outrank real staleness.
        d = zwave_details([zw_node(nodeId=27, lastTime=None),
                           zw_node(nodeId=10, lastTime="2026-07-15T04:00:00+0000")])
        self.assertEqual([n["nodeId"] for n in m.analyze_zwave(d, NOW)["stalest"]], [10])


class TestAnalyzeRollup(unittest.TestCase):
    def test_summary_counts_across_radios(self):
        zw = zwave_details([zw_node(nodeState="FAILED"), zw_node(per=10)])
        zb = zigbee_details([
            zb_device(active=False, name="Device", type="Device", lastActivity=None)
        ], weakChannel=True)
        r = m.analyze(zw, zb, NOW)
        # 1 failed + 1 weakChannel = 2 critical; 1 PER + 1 incomplete join = 2 warnings
        self.assertEqual(r["summary"], {"critical": 2, "warnings": 2})

    def test_active_false_alone_does_not_reach_warning_counter(self):
        zb = zigbee_details([zb_device(active=False)])
        r = m.analyze(None, zb, NOW)
        self.assertEqual(r["summary"], {"critical": 0, "warnings": 0})

    def test_missing_radio_is_none(self):
        r = m.analyze(zwave_details([zw_node()]), None, NOW)
        self.assertIsNone(r["zigbee"])
        self.assertIsNotNone(r["zwave"])

    def test_weak_signal_and_never_heard_reach_the_counters(self):
        # Regression: both were flagged and then dropped from the rollup, so a hub with two
        # nodes at/below the noise floor still reported warnings:0 — enough for the old skill
        # to call it healthy and stop. Live on the Devices hub: 2 weak + 6 never_heard, all
        # rolled up as 0.
        # On the legacy scale a healthy node reads POSITIVE dB above noise — the shared
        # fixture's default "-50db" is a zwaveJS-shaped value and would flag every node here.
        zw = zwave_details([zw_node(nodeId=1, lwrRssi="-4dB"),                   # at/below noise
                            zw_node(nodeId=2, lwrRssi="30dB", lastTime=None),    # never heard
                            zw_node(nodeId=3, lwrRssi="30dB", per=7)],           # packet errors
                           zwavejs=False)
        r = m.analyze(zw, None, NOW)
        self.assertEqual(r["summary"], {"critical": 0, "warnings": 3})

    def test_clean_hub_still_rolls_up_zero(self):
        # The counters must stay honest in both directions — no manufactured warnings.
        r = m.analyze(zwave_details([zw_node(lwrRssi="-40db")], zwavejs=True), None, NOW)
        self.assertEqual(r["summary"], {"critical": 0, "warnings": 0})

    def test_hub_mesh_problems_count_as_critical(self):
        mesh = m.analyze_hub_mesh(hub_mesh_json([peer()]), {"192.0.2.10": UNREACHABLE})
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
            "ipAddress": "192.0.2.10", "active": True, "offline": False, "warning": None,
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
                               {"192.0.2.10": {"reachable": True, "hubId": "other-hub-uid",
                                                 "error": None}})
        self.assertEqual([p["signal"] for p in r["problems"]], ["peer_identity_mismatch"])

    def test_healthy_probed_peer_has_no_problems(self):
        r = m.analyze_hub_mesh(hub_mesh_json([peer()]), {"192.0.2.10": REACHABLE})
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


class TestOptionalFetchDegradation(unittest.TestCase):
    """The radio endpoints are the requested capability and stay fatal. /hub2/hubMeshJson and
    /hub/details/json are undocumented and version-sensitive, so a hub lacking them must still
    get its radios analyzed — degraded loudly, never silently."""

    def _run(self, fail_paths, argv=("--ip", "1.2.3.4", "--radio", "zwave", "--no-probe")):
        def transport(_method, url, _body):
            if any(p in url for p in fail_paths):
                raise m.HubError(f"cannot reach {url}: HTTP 404")
            if m.ZWAVE_PATH in url:
                return 200, {}, json.dumps(zwave_details([zw_node()], zwavejs=False))
            if m.DETAILS_PATH in url:
                return 200, {}, '{"timeZone": "America/Chicago"}'
            if m.HUBMESH_PATH in url:
                return 200, {}, json.dumps(hub_mesh_json([peer()]))
            raise AssertionError(f"unexpected fetch: {url}")

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = m.main(list(argv), transport=transport)
        payload = json.loads(out.getvalue()) if out.getvalue().strip() else None
        return rc, payload, err.getvalue()

    def test_missing_hub_mesh_endpoint_still_returns_radio_analysis(self):
        rc, payload, err = self._run([m.HUBMESH_PATH])
        self.assertEqual(rc, 0)                              # radios asked for, radios delivered
        self.assertIsNotNone(payload)
        payload = cast(dict, payload)
        self.assertIsNotNone(payload["zwave"])
        self.assertIsNone(payload["hub_mesh"])
        # Degraded, but never quietly: the gap names what is now unknown, on both channels.
        self.assertEqual([w["endpoint"] for w in payload["fetch_warnings"]], [m.HUBMESH_PATH])
        self.assertIn("not an all-clear", payload["fetch_warnings"][0]["consequence"])
        self.assertIn(m.HUBMESH_PATH, err)

    def test_missing_details_endpoint_degrades_timezone_not_the_run(self):
        rc, payload, err = self._run([m.DETAILS_PATH])
        self.assertEqual(rc, 0)
        self.assertIsNotNone(payload)
        payload = cast(dict, payload)
        self.assertIsNone(payload["hub_timezone"])
        self.assertEqual([w["endpoint"] for w in payload["fetch_warnings"]], [m.DETAILS_PATH])
        self.assertIn("overstated by the hub's offset", payload["fetch_warnings"][0]["consequence"])
        self.assertIn(m.DETAILS_PATH, err)

    def test_both_optional_endpoints_missing_still_succeeds(self):
        rc, payload, _ = self._run([m.HUBMESH_PATH, m.DETAILS_PATH])
        self.assertEqual(rc, 0)
        self.assertIsNotNone(payload)
        payload = cast(dict, payload)
        self.assertEqual(len(payload["fetch_warnings"]), 2)

    def test_requested_radio_endpoint_failure_is_still_fatal(self):
        rc, payload, err = self._run([m.ZWAVE_PATH])
        self.assertEqual(rc, 1)
        self.assertIsNone(payload)
        self.assertIn(m.ZWAVE_PATH, err)

    def test_zigbee_only_run_does_not_warn_about_zwave_timestamps(self):
        # /hub/details/json exists only to date Z-Wave's naive stamps; Zigbee's lastActivity
        # carries its own offset. Fetching it for --radio zigbee would raise a fetch_warning
        # about node ages this run never computes — and the skill reads any fetch_warning as a
        # blind axis blocking an all-clear, so an irrelevant one is a false blind axis.
        def transport(_method, url, _body):
            if m.DETAILS_PATH in url:
                raise AssertionError("details must not be fetched for a zigbee-only run")
            if m.ZIGBEE_PATH in url:
                return 200, {}, json.dumps(zigbee_details([zb_device()]))
            if m.HUBMESH_PATH in url:
                return 200, {}, json.dumps(hub_mesh_json([]))
            raise AssertionError(f"unexpected fetch: {url}")

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = m.main(["--ip", "1.2.3.4", "--radio", "zigbee", "--no-probe"], transport=transport)
        payload = json.loads(out.getvalue())
        self.assertEqual(rc, 0)
        self.assertEqual(payload["fetch_warnings"], [])
        self.assertIsNone(payload["zwave"])

    def test_healthy_hub_emits_no_fetch_warnings(self):
        rc, payload, _ = self._run([])
        self.assertEqual(rc, 0)
        self.assertIsNotNone(payload)
        payload = cast(dict, payload)
        self.assertEqual(payload["fetch_warnings"], [])
        self.assertEqual(payload["hub_timezone"], "America/Chicago")


class TestProbePeer(unittest.TestCase):
    """reachable answers "does the address respond", never "did it serve usable identity".
    /hub/details/json is undocumented and version-sensitive, so conflating the two would roll a
    false peer_unreachable critical against a healthy peer on a firmware that lacks it."""

    def test_unreachable_transport_reports_finding_not_error(self):
        def boom(_method, _url, _body):
            raise m.HubError("cannot reach http://192.168.1.64:8080: timed out")
        r = m.probe_peer("192.168.1.64", 8080, transport=boom)
        self.assertEqual(r["reachable"], False)      # nothing answered at all
        self.assertIsNone(r["hubId"])

    def test_reachable_peer_returns_its_hubUID(self):
        def ok(_method, _url, _body):
            return 200, {}, '{"hubUID": "e6574b36-23fc-4164-acf4-24aed2cc6f72"}'
        r = m.probe_peer("192.0.2.11", 8080, transport=ok)
        self.assertEqual(r, {"reachable": True,
                             "hubId": "e6574b36-23fc-4164-acf4-24aed2cc6f72", "error": None})

    def test_http_response_without_the_endpoint_is_reachable_identity_unknown(self):
        def missing(_method, _url, _body):
            return 404, {}, "Not Found"
        r = m.probe_peer("192.0.2.11", 8080, transport=missing)
        self.assertTrue(r["reachable"])              # something IS there ...
        self.assertIsNone(r["hubId"])                # ... it just did not identify itself
        self.assertIn("identity unverified", r["error"])

    def test_non_json_response_is_reachable_identity_unknown(self):
        def html(_method, _url, _body):
            return 200, {}, "<html>login</html>"     # e.g. Hub Security on
        r = m.probe_peer("192.0.2.11", 8080, transport=html)
        self.assertTrue(r["reachable"])
        self.assertIsNone(r["hubId"])
        self.assertIn("identity unverified", r["error"])

    def test_json_without_hubUID_is_identity_unverified_not_verified(self):
        # 200 + JSON but no hubUID. hubId None with error None would read as "identity checked
        # and fine" — the one shape that must never look verified.
        def no_uid(_method, _url, _body):
            return 200, {}, '{"hubName": "Some Hub"}'
        r = m.probe_peer("192.0.2.11", 8080, transport=no_uid)
        self.assertTrue(r["reachable"])
        self.assertIsNone(r["hubId"])
        self.assertIn("identity unverified", r["error"])

    def test_reachable_but_unidentified_peer_raises_no_critical(self):
        # The regression the split exists for: a healthy peer on a firmware without
        # /hub/details/json must not be reported unreachable, nor as an identity mismatch.
        probe = {"reachable": True, "hubId": None, "error": "identity unverified"}
        r = m.analyze_hub_mesh(hub_mesh_json([peer()]), {"192.0.2.10": probe})
        self.assertEqual(r["problems"], [])
        self.assertEqual(r["peers"][0]["probe_error"], "identity unverified")


# --- route fan-in ---------------------------------------------------------------------------
# A repeater's blast radius: how many classic-mesh nodes route THROUGH it. The grounded case is
# the Devices hub, where 12 nodes routed through a repeater the hub had never heard from.


class TestParseRoute(unittest.TestCase):
    def test_hex_hops_to_ints(self):
        self.assertEqual(m.parse_route("01 -> 07 -> 0A"), [1, 7, 10])

    def test_direct_route_is_two_hops(self):
        self.assertEqual(m.parse_route("01 -> 0A"), [1, 10])

    def test_lowercase_and_tight_spacing(self):
        self.assertEqual(m.parse_route("01->0f->1c"), [1, 15, 28])

    def test_three_digit_lr_id(self):
        self.assertEqual(m.parse_route("01 -> 12C"), [1, 300])

    def test_absent_route_is_none(self):
        for raw in ("", None):
            self.assertIsNone(m.parse_route(raw))

    def test_link_speed_suffix_is_stripped(self):
        # zwaveJS appends the negotiated link speed to the last hop. Grounded 2026-07-29 on
        # 2.5.1.134: every route on the hub carried one, so failing to strip it returned None
        # for all 34 and fan-in reported "no repeaters" instead of "unmeasured".
        self.assertEqual(m.parse_route("01 -> DA 100kbps"), [1, 218])
        self.assertEqual(m.parse_route("01 -> 1B -> 71 40kbps"), [1, 27, 113])
        self.assertEqual(m.parse_route("01 -> 1B -> 81 -> 60 9.6kbps"), [1, 27, 129, 96])

    def test_link_speed_suffix_is_case_insensitive(self):
        self.assertEqual(m.parse_route("01 -> 0A 100Kbps"), [1, 10])

    def test_unparseable_route_is_none(self):
        # 'ZZ' is not hex. None means "no route information", never an empty hop list that would
        # read as a direct route. Trailing junk that is NOT the known link-speed suffix stays
        # unparseable — an unknown shape must surface as an anomaly, not be guessed at.
        for raw in ("01 -> ZZ", "not a route", "01 -> -> 0A", "01 -> 0A 40kb", "01 -> 0A fast",
                    "01 -> 0A100kbps",      # no separator before the speed
                    "01 -> 1B 100kbps -> 71 40kbps"):  # per-hop speeds: half-parsing would lie
            self.assertIsNone(m.parse_route(raw))

    def test_speed_suffix_is_stripped_only_from_something_route_shaped(self):
        # Without the '->' guard this parses to [1] — a one-hop list that reads as a route.
        self.assertIsNone(m.parse_route("01 100kbps"))


class TestRouteHopCount(unittest.TestCase):
    """`hops` is what says whether a node's lwrRssi is its own link or a repeater's."""

    def test_direct_route_is_zero_hops(self):
        self.assertEqual(m.route_hop_count("01 -> 0A", 10), 0)

    def test_each_intermediate_is_one_hop(self):
        self.assertEqual(m.route_hop_count("01 -> 07 -> 0A", 10), 1)
        self.assertEqual(m.route_hop_count("01 -> 07 -> 08 -> 0A", 10), 2)

    def test_link_speed_suffix_does_not_blank_the_count(self):
        self.assertEqual(m.route_hop_count("01 -> 1B -> 71 40kbps", 113), 1)

    def test_lr_star_route_is_direct(self):
        # LR talks straight to the hub by construction, so its lwrRssi is always its own link
        self.assertEqual(m.route_hop_count("01 -> 10C", 268), 0)

    def test_unreadable_route_is_none_never_zero(self):
        # None means "which link this measures is unknown". Zero would assert a direct link the
        # route does not evidence, putting a repeater's RSSI into the direct ranking.
        for raw, node_id in (("", 10), (None, 10), ("01 -> ZZ", 10),
                             ("01 -> 0A", 11),          # ends at a different node — stale record
                             ("01 -> 10C -> 0A", 10)):  # an LR id repeats for nobody
            self.assertIsNone(m.route_hop_count(raw, node_id))

    def test_hops_and_fan_in_read_the_same_route_the_same_way(self):
        # One definition of a coherent route. A node counted direct by one reader while the other
        # says it routes through a repeater is the exact misreading the hop count prevents.
        nodes = [zw_node(nodeId=7, route="01 -> 07"), zw_node(nodeId=10, route="01 -> 07 -> 0A"),
                 zw_node(nodeId=11, route="01 -> 0A")]  # incoherent: ends at node 10
        r = m.analyze_zwave(zwave_details(nodes), NOW)
        hops = {n["nodeId"]: n["hops"] for n in r["ranked"]["by_rssi_direct"]
                + r["ranked"]["by_rssi_routed"] + r["ranked"]["by_rssi_route_unknown"]}
        self.assertEqual(hops, {7: 0, 10: 1, 11: None})
        self.assertEqual(r["route_fan_in"]["repeaters"][0]["dependents"], [10])
        self.assertEqual([a["nodeId"] for a in r["route_fan_in"]["anomalies"]], [11])


class TestRssiSplitByHops(unittest.TestCase):
    """lwrRssi is the last hop INTO the hub, so a routed node reports its final repeater's link.
    Grounded: three battery shades read -48 dBm routed through an extender beside the hub, then
    -88/-80/-70 on direct routes after a rebuild — 40 dB with nothing physically moved."""

    def test_repeated_node_is_never_ranked_against_a_direct_one(self):
        nodes = [zw_node(nodeId=10, deviceName="Shade", lwrRssi="-48db", route="01 -> 07 -> 0A"),
                 zw_node(nodeId=7, deviceName="Extender", lwrRssi="-52db", route="01 -> 07"),
                 zw_node(nodeId=11, deviceName="Sensor", lwrRssi="-88db", route="01 -> 0B")]
        r = m.analyze_zwave(zwave_details(nodes), NOW)["ranked"]
        # In one list the shade's -48 outranks the sensor's -88 and the fleet reads as strong.
        self.assertEqual([n["nodeId"] for n in r["by_rssi_direct"]], [11, 7])
        self.assertEqual([n["nodeId"] for n in r["by_rssi_routed"]], [10])
        self.assertNotIn("by_rssi", r)  # the mixed ranking is gone, not merely supplemented

    def test_route_unknown_nodes_stay_rankable(self):
        # A weak reading with no readable route is still a weak link — unattributed, not dropped.
        nodes = [zw_node(nodeId=10, lwrRssi="-95db", route=""),
                 zw_node(nodeId=11, lwrRssi="-60db", route="")]
        r = m.analyze_zwave(zwave_details(nodes), NOW)["ranked"]
        self.assertEqual([n["nodeId"] for n in r["by_rssi_route_unknown"]], [10, 11])
        self.assertEqual(r["by_rssi_direct"], [])

    def test_hops_rides_on_the_weak_signal_flag(self):
        # The flag is real either way, but on a routed node it describes the REPEATER's link
        nodes = [zw_node(nodeId=10, lwrRssi="-105db", route="01 -> 07 -> 0A")]
        r = m.analyze_zwave(zwave_details(nodes), NOW)
        self.assertEqual(r["weak_signal_heuristic"][0]["hops"], 1)


class TestRouteFanIn(unittest.TestCase):
    def fan_in(self, nodes, **kw):
        details = zwave_details(nodes, **kw)
        return m.analyze_zwave(details, NOW)["route_fan_in"]

    def test_counts_nodes_routing_through_a_repeater(self):
        nodes = [zw_node(nodeId=7, deviceName="Extender", route="01 -> 07"),
                 zw_node(nodeId=10, route="01 -> 07 -> 0A"),
                 zw_node(nodeId=11, route="01 -> 07 -> 0B"),
                 zw_node(nodeId=12, route="01 -> 0C")]  # direct — depends on no repeater
        r = self.fan_in(nodes)
        self.assertEqual(r["repeater_count"], 1)
        self.assertEqual(r["repeaters"][0]["nodeId"], 7)
        self.assertEqual(r["repeaters"][0]["dependent_count"], 2)
        self.assertEqual(r["repeaters"][0]["dependents"], [10, 11])

    def test_direct_routes_produce_no_repeaters(self):
        nodes = [zw_node(nodeId=10, route="01 -> 0A"), zw_node(nodeId=11, route="01 -> 0B")]
        r = self.fan_in(nodes)
        self.assertEqual(r["repeater_count"], 0)
        self.assertEqual(r["load_bearing_concerns"], [])

    def test_zwavejs_link_speed_suffix_does_not_blank_the_fan_in_axis(self):
        # The regression this whole path exists for, asserted where it was REPORTED rather than
        # only at the parser: every zwaveJS route carries a link-speed suffix, and when
        # parse_route() choked on it analyze_route_fan_in() answered "this mesh has no
        # repeaters" (repeater_count 0, every route in anomalies[]) instead of "fan-in was not
        # measured". A parser-only test would stay green if the strip moved and the wiring broke.
        nodes = [zw_node(nodeId=7, deviceName="Extender", route="01 -> 07 100kbps"),
                 zw_node(nodeId=10, route="01 -> 07 -> 0A 40kbps"),
                 zw_node(nodeId=11, route="01 -> 07 -> 0B 9.6kbps"),
                 zw_node(nodeId=12, route="01 -> 0C 40kbps")]
        r = self.fan_in(nodes)
        self.assertEqual(r["anomalies"], [])
        self.assertEqual(r["repeater_count"], 1)
        self.assertEqual(r["repeaters"][0]["nodeId"], 7)
        self.assertEqual(r["repeaters"][0]["dependents"], [10, 11])

    def test_multi_hop_counts_every_intermediate(self):
        # Both 07 and 08 carry node 10; neither the hub nor the node itself is a repeater.
        nodes = [zw_node(nodeId=7, route="01 -> 07"), zw_node(nodeId=8, route="01 -> 07 -> 08"),
                 zw_node(nodeId=10, route="01 -> 07 -> 08 -> 0A")]
        r = self.fan_in(nodes)
        counts = {x["nodeId"]: x["dependent_count"] for x in r["repeaters"]}
        self.assertEqual(counts, {7: 2, 8: 1})
        self.assertNotIn(1, counts)   # the hub is never a repeater
        self.assertNotIn(10, counts)  # a node does not repeat for itself

    def test_ranked_worst_first_by_dependent_count(self):
        nodes = [zw_node(nodeId=7, route="01 -> 07"), zw_node(nodeId=8, route="01 -> 08"),
                 zw_node(nodeId=10, route="01 -> 08 -> 0A"),
                 zw_node(nodeId=11, route="01 -> 07 -> 0B"),
                 zw_node(nodeId=12, route="01 -> 07 -> 0C"),
                 zw_node(nodeId=13, route="01 -> 07 -> 0D")]
        r = self.fan_in(nodes)
        self.assertEqual([x["nodeId"] for x in r["repeaters"]], [7, 8])
        self.assertEqual([x["dependent_count"] for x in r["repeaters"]], [3, 1])

    def test_healthy_repeater_carrying_many_is_not_a_concern(self):
        # The issue's explicit instruction: fan-in is a topology fact, not a fault. 12 nodes
        # behind a healthy repeater is a normal mesh.
        nodes = [zw_node(nodeId=7, deviceName="Extender", route="01 -> 07")]
        nodes += [zw_node(nodeId=i, route=f"01 -> 07 -> {i:02X}") for i in range(10, 22)]
        r = self.fan_in(nodes)
        self.assertEqual(r["repeaters"][0]["dependent_count"], 12)
        self.assertEqual(r["repeaters"][0]["concerns"], [])
        self.assertEqual(r["load_bearing_concerns"], [])

    def test_never_heard_repeater_carrying_twelve_is_the_signal(self):
        # The grounded case this was filed for. lastTime absent => the hub has never heard it,
        # while nodeState stays OK and every radio check passes.
        nodes = [zw_node(nodeId=7, deviceName="Extender", route="01 -> 07", lastTime=None)]
        nodes += [zw_node(nodeId=i, route=f"01 -> 07 -> {i:02X}") for i in range(10, 22)]
        r = self.fan_in(nodes)
        concern = r["load_bearing_concerns"][0]
        self.assertEqual(concern["nodeId"], 7)
        self.assertEqual(concern["dependent_count"], 12)
        self.assertEqual(concern["concerns"], ["never_heard"])
        self.assertEqual(len(concern["dependents"]), 12)

    def test_failed_and_weak_repeaters_are_crossed(self):
        nodes = [zw_node(nodeId=7, route="01 -> 07", nodeState="FAILED"),
                 zw_node(nodeId=8, route="01 -> 08", lwrRssi="-105db"),
                 zw_node(nodeId=9, route="01 -> 09", per=4),
                 zw_node(nodeId=10, route="01 -> 07 -> 0A"),
                 zw_node(nodeId=11, route="01 -> 08 -> 0B"),
                 zw_node(nodeId=12, route="01 -> 09 -> 0C")]
        r = self.fan_in(nodes)
        crossed = {x["nodeId"]: x["concerns"] for x in r["load_bearing_concerns"]}
        self.assertEqual(crossed[7], ["failed"])
        self.assertEqual(crossed[8], ["weak_signal_heuristic"])
        self.assertEqual(crossed[9], ["packet_errors"])

    def test_lr_nodes_are_excluded_entirely(self):
        # LR is a star: no repeaters exist to depend on, and an LR node can serve as none.
        # Reading its route here would manufacture a repeater the topology forbids.
        nodes = [zw_node(nodeId=300, route="01 -> 12C"), zw_node(nodeId=301, route="01 -> 12D")]
        r = self.fan_in(nodes)
        self.assertEqual(r["repeater_count"], 0)
        self.assertEqual(r["anomalies"], [])

    def test_lr_hop_in_a_mesh_route_is_an_anomaly_not_a_repeater(self):
        # An LR node is a star node: it repeats for nobody. A mesh route naming one as an
        # intermediate hop is incoherent, not a discovery — counting it would manufacture the
        # repeater the topology forbids. True whether or not the LR node is in nodes[].
        nodes = [zw_node(nodeId=300, route="01 -> 12C"),
                 zw_node(nodeId=10, route="01 -> 12C -> 0A")]
        r = self.fan_in(nodes)
        self.assertEqual(r["repeater_count"], 0)
        self.assertEqual(r["repeaters"], [])
        self.assertEqual(len(r["anomalies"]), 1)
        self.assertEqual(r["anomalies"][0]["nodeId"], 10)
        self.assertIn("non-mesh hop", r["anomalies"][0]["reason"])

    def test_reserved_range_hop_is_an_anomaly(self):
        # 233..255 is a gap the spec does not assign — classified 'unknown', never 'mesh', so it
        # gets no mesh-only reading (see node_topology).
        nodes = [zw_node(nodeId=10, route="01 -> F0 -> 0A")]
        r = self.fan_in(nodes)
        self.assertEqual(r["repeater_count"], 0)
        self.assertIn("non-mesh hop", r["anomalies"][0]["reason"])

    def test_a_rejected_route_credits_no_hop(self):
        # Validation precedes counting: node 11's route is rejected for its LR hop, so the valid
        # hop 07 in that same route must not collect a dependent from a path called impossible.
        nodes = [zw_node(nodeId=7, route="01 -> 07"),
                 zw_node(nodeId=10, route="01 -> 07 -> 0A"),
                 zw_node(nodeId=11, route="01 -> 07 -> 12C -> 0B")]
        r = self.fan_in(nodes)
        self.assertEqual([x["nodeId"] for x in r["repeaters"]], [7])
        self.assertEqual(r["repeaters"][0]["dependent_count"], 1)   # node 10 only, never 11
        self.assertEqual(r["repeaters"][0]["dependents"], [10])
        self.assertEqual(len(r["anomalies"]), 1)

    def test_unknown_hop_is_reported_not_dropped(self):
        # A route naming a hop absent from nodes[]. The dependents are real either way.
        nodes = [zw_node(nodeId=10, route="01 -> 07 -> 0A")]
        r = self.fan_in(nodes)
        self.assertEqual(r["repeaters"][0]["nodeId"], 7)
        self.assertEqual(r["repeaters"][0]["concerns"], ["unknown_node"])
        self.assertEqual(r["repeaters"][0]["dependent_count"], 1)

    def test_incoherent_route_is_an_anomaly_and_counts_nothing(self):
        # The shape the pre-0.1.10 eval fixture carried: every node claiming route '01 -> 0A'.
        # For node 11 that path terminates at a different node. Guessing which hops are
        # repeaters out of it would invent dependents.
        nodes = [zw_node(nodeId=10, route="01 -> 0A"), zw_node(nodeId=11, route="01 -> 0A")]
        r = self.fan_in(nodes)
        self.assertEqual(r["repeater_count"], 0)
        self.assertEqual(len(r["anomalies"]), 1)
        self.assertEqual(r["anomalies"][0]["nodeId"], 11)
        self.assertIn("does not run from the hub", r["anomalies"][0]["reason"])

    def test_unparseable_route_is_an_anomaly(self):
        nodes = [zw_node(nodeId=10, route="01 -> ZZ -> 0A")]
        r = self.fan_in(nodes)
        self.assertEqual(r["repeater_count"], 0)
        self.assertIn("not parseable", r["anomalies"][0]["reason"])

    def test_absent_route_is_silent_not_an_anomaly(self):
        # No route information is not a malformed route.
        nodes = [zw_node(nodeId=10, route="")]
        r = self.fan_in(nodes)
        self.assertEqual(r["anomalies"], [])
        self.assertEqual(r["repeater_count"], 0)


class TestFanInEnrichment(unittest.TestCase):
    def test_never_heard_entry_carries_its_blast_radius(self):
        # The gap the issue names: never_heard[] listed the load-bearing repeater and a silent
        # leaf identically. dependent_count is what tells them apart, inline where it is read.
        nodes = [zw_node(nodeId=7, deviceName="Extender", route="01 -> 07", lastTime=None),
                 zw_node(nodeId=9, deviceName="Leaf", route="01 -> 09", lastTime=None)]
        nodes += [zw_node(nodeId=i, route=f"01 -> 07 -> {i:02X}") for i in range(10, 22)]
        z = m.analyze_zwave(zwave_details(nodes), NOW)
        by_id = {n["nodeId"]: n for n in z["never_heard"]}
        self.assertEqual(by_id[7]["dependent_count"], 12)
        self.assertEqual(by_id[9]["dependent_count"], 0)

    def test_lr_dependent_count_is_none_not_zero(self):
        # A star node repeats for nobody by construction, so "0 dependents" would read as a
        # measurement where the question does not apply.
        nodes = [zw_node(nodeId=300, route="01 -> 12C"), zw_node(nodeId=10, route="01 -> 0A")]
        z = m.analyze_zwave(zwave_details(nodes), NOW)
        by_id = {n["nodeId"]: n for n in z["stalest"]}
        self.assertIsNone(by_id[300]["dependent_count"])
        self.assertEqual(by_id[10]["dependent_count"], 0)


class TestFanInStaysOutOfTheCounters(unittest.TestCase):
    def test_healthy_high_fan_in_hub_rolls_up_clean(self):
        # A repeater carrying 12 nodes is a normal mesh. If fan-in reached the counters, every
        # healthy hub with a repeater would warn — and the reader would learn to ignore it.
        nodes = [zw_node(nodeId=7, route="01 -> 07")]
        nodes += [zw_node(nodeId=i, route=f"01 -> 07 -> {i:02X}") for i in range(10, 22)]
        out = m.analyze(zwave_details(nodes), None, NOW)
        self.assertEqual(out["zwave"]["route_fan_in"]["repeaters"][0]["dependent_count"], 12)
        self.assertEqual(out["summary"], {"critical": 0, "warnings": 0})

    def test_never_heard_repeater_counts_once_not_twice(self):
        # The never-heard repeater is already counted through never_heard[]. Counting it again
        # for its fan-in would double-count one node.
        nodes = [zw_node(nodeId=7, route="01 -> 07", lastTime=None)]
        nodes += [zw_node(nodeId=i, route=f"01 -> 07 -> {i:02X}") for i in range(10, 22)]
        out = m.analyze(zwave_details(nodes), None, NOW)
        self.assertEqual(len(out["zwave"]["route_fan_in"]["load_bearing_concerns"]), 1)
        self.assertEqual(out["summary"], {"critical": 0, "warnings": 1})


if __name__ == "__main__":
    unittest.main()
