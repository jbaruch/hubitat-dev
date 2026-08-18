#!/usr/bin/env python3
"""Tests for skills/_scripts/hub_fw_update.py — the RSSI-floor gate and its link readout.

The gate is the guardrail that keeps a hang-prone flash from taking the whole Z-Wave
controller down (rules/firmware-update.md), so its decision is a pure function tested
without a hub: `rssi_gate` takes an already-read (rssi, hops) pair and returns a verdict.
`node_link` is exercised against fixture /hub/zwaveDetails/json payloads with `_get`
patched — no network, no clock (testing-standards)."""

import importlib.util
import unittest
from pathlib import Path
from typing import Any

SCRIPT = Path(__file__).resolve().parent.parent / "hub_fw_update.py"
spec = importlib.util.spec_from_file_location("hub_fw_update", SCRIPT)
assert spec and spec.loader
_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_mod)
# Loaded off a file path, so its attributes are opaque to the type checker; the tests
# read and rebind them (patching `_get`) through Any rather than a per-line ignore.
m: Any = _mod

FLOOR = -95


class TestRssiGateDirect(unittest.TestCase):
    """hops == 0 — lwrRssi IS the node's own radio, so the floor applies as it always has."""

    def test_strong_direct_node_flashes(self):
        self.assertEqual(m.rssi_gate(-61, 0, FLOOR, False, False)[0], "flash")

    def test_direct_node_at_the_floor_is_skipped_weak(self):
        verdict, reason = m.rssi_gate(FLOOR, 0, FLOOR, False, False)
        self.assertEqual(verdict, "skipped_weak")
        self.assertIn("--flash-weak", reason)

    def test_direct_node_below_the_floor_is_skipped_weak(self):
        self.assertEqual(m.rssi_gate(-96, 0, FLOOR, False, False)[0], "skipped_weak")

    def test_flash_weak_forces_a_weak_direct_node(self):
        self.assertEqual(m.rssi_gate(-96, 0, FLOOR, True, False)[0], "flash")

    def test_direct_node_with_unreadable_rssi_is_skipped_unknown(self):
        """A direct link with no readable reading is still an UNMEASURED own-link, and the
        rule leaves unmeasured links on their current firmware. The pre-hop-accounting gate
        flashed these on a vacuous "no reading"."""
        verdict, reason = m.rssi_gate(None, 0, FLOOR, False, False)
        self.assertEqual(verdict, "skipped_unknown")
        self.assertIn("no readable lwrRssi", reason)
        self.assertIn("--flash-unmeasured", reason)

    def test_flash_unmeasured_forces_a_direct_node_with_no_reading(self):
        self.assertEqual(m.rssi_gate(None, 0, FLOOR, False, True)[0], "flash")

    def test_flash_weak_does_not_lift_the_unmeasured_skip_on_a_direct_node(self):
        """--flash-weak speaks to the floor, which needs a reading to apply at all."""
        self.assertEqual(m.rssi_gate(None, 0, FLOOR, True, False)[0], "skipped_unknown")


class TestRssiGateRouted(unittest.TestCase):
    """hops >= 1 — lwrRssi is the final repeater's link into the hub, never this node's."""

    def test_routed_node_reading_strong_is_still_skipped(self):
        """The false-PASS this gate exists to close: measured in #112, three shades read
        -48 dBm routed through an extender beside the hub and -88/-80/-70 once direct."""
        verdict, reason = m.rssi_gate(-48, 2, FLOOR, False, False)
        self.assertEqual(verdict, "skipped_unknown")
        self.assertIn("--flash-unmeasured", reason)
        self.assertIn("2 repeater(s)", reason)

    def test_routed_node_reading_weak_is_skipped_as_unknown_not_weak(self):
        """A weak routed reading is not evidence about the device, so it is not skipped_weak."""
        self.assertEqual(m.rssi_gate(-99, 1, FLOOR, False, False)[0], "skipped_unknown")

    def test_flash_unmeasured_forces_a_routed_node(self):
        self.assertEqual(m.rssi_gate(-48, 2, FLOOR, False, True)[0], "flash")

    def test_flash_weak_alone_does_not_lift_the_routed_skip(self):
        """The two overrides are independent; --flash-weak speaks only to the floor on a
        direct measured link."""
        self.assertEqual(m.rssi_gate(-48, 2, FLOOR, True, False)[0], "skipped_unknown")

    def test_routed_node_with_unreadable_rssi_is_skipped(self):
        verdict, reason = m.rssi_gate(None, 3, FLOOR, False, False)
        self.assertEqual(verdict, "skipped_unknown")
        self.assertIn("the only reading is", reason)


class TestRssiGateUnknownRoute(unittest.TestCase):
    """hops is None — no readable route, so direct-vs-routed itself is unknown."""

    def test_unknown_route_is_skipped(self):
        verdict, reason = m.rssi_gate(-70, None, FLOOR, False, False)
        self.assertEqual(verdict, "skipped_unknown")
        self.assertIn("no readable route", reason)

    def test_unknown_route_is_never_treated_as_direct(self):
        """A strong reading must not buy a flash when the route does not evidence a direct link."""
        self.assertEqual(m.rssi_gate(-40, None, FLOOR, False, False)[0], "skipped_unknown")

    def test_flash_unmeasured_forces_an_unknown_route(self):
        self.assertEqual(m.rssi_gate(-70, None, FLOOR, False, True)[0], "flash")


class TestLinkNote(unittest.TestCase):
    def test_direct(self):
        self.assertEqual(m.link_note(-61, 0), " rssi -61dBm direct")

    def test_routed(self):
        self.assertEqual(m.link_note(-48, 2), " rssi -48dBm via 2 repeater(s)")

    def test_unknown_route_with_a_reading(self):
        self.assertEqual(m.link_note(-70, None), " rssi -70dBm route unknown")

    def test_known_hops_without_a_reading(self):
        self.assertEqual(m.link_note(None, 0), " link direct")

    def test_nothing_known(self):
        self.assertEqual(m.link_note(None, None), "")


class TestNodeLink(unittest.TestCase):
    """node_link reads one /hub/zwaveDetails/json payload; _get is patched, never called out."""

    def setUp(self):
        self._real_get = m._get
        self.addCleanup(lambda: setattr(m, "_get", self._real_get))

    def _serve(self, nodes):
        m._get = lambda base, path: {"nodes": nodes}

    def test_direct_node(self):
        self._serve([{"nodeId": 44, "lwrRssi": "-61db", "route": "01 -> 2C"}])
        self.assertEqual(m.node_link("http://h", 44), (-61, 0))

    def test_routed_node_reports_its_hop_count(self):
        self._serve([{"nodeId": 113, "lwrRssi": "-48db", "route": "01 -> 1B -> 71 40kbps"}])
        rssi, hops = m.node_link("http://h", 113)
        self.assertEqual(rssi, -48)
        self.assertEqual(hops, 1)

    def test_legacy_positive_scale_reads_as_unknown_rssi(self):
        """The legacy backend reports dB above the noise floor; this gate reads only the
        zwaveJS absolute-dBm scale, so a positive number is not interpreted."""
        self._serve([{"nodeId": 44, "lwrRssi": "27dB", "route": "01 -> 2C"}])
        self.assertEqual(m.node_link("http://h", 44), (None, 0))

    def test_missing_rssi_reads_as_unknown(self):
        self._serve([{"nodeId": 44, "route": "01 -> 2C"}])
        self.assertEqual(m.node_link("http://h", 44), (None, 0))

    def test_unparseable_route_reads_as_unknown_hops(self):
        self._serve([{"nodeId": 44, "lwrRssi": "-61db", "route": ""}])
        self.assertEqual(m.node_link("http://h", 44), (-61, None))

    def test_node_absent_from_the_payload_knows_nothing(self):
        self._serve([{"nodeId": 9, "lwrRssi": "-61db", "route": "01 -> 09"}])
        self.assertEqual(m.node_link("http://h", 44), (None, None))

    def test_absent_node_gates_as_unknown(self):
        """The end-to-end shape of the closed hole: a node the details payload does not
        carry used to flash on a vacuous 'no reading', and now skips."""
        self._serve([])
        rssi, hops = m.node_link("http://h", 44)
        self.assertEqual(m.rssi_gate(rssi, hops, FLOOR, False, False)[0], "skipped_unknown")


if __name__ == "__main__":
    unittest.main()
