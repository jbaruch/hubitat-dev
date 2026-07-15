#!/usr/bin/env python3
"""Tests for scripts/hub_radiolog.py — pure frame parsing, filtering, cluster naming, sequence
gap tracking, and window summarization over fixture frames (no network)."""

import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "hub_radiolog.py"
spec = importlib.util.spec_from_file_location("hub_radiolog", SCRIPT)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

# Real shapes captured from the live sockets on 2.5.1.128.
ZB_RAW = {"name": "Stairs Top Motion Sensor", "id": 26851, "deviceId": 501, "profileId": "0104",
          "clusterId": "0500", "sourceEndpoint": "01", "destinationEndpoint": "01",
          "groupId": "0000", "sequence": 85, "lastHopLqi": 204, "lastHopRssi": -49,
          "time": "2026-07-15 12:11:32.769", "type": "zigbeeRx", "payload": "00"}
ZW_RAW = {"sourceLabel": "DRIVER", "deviceId": -999,
          "plainTextMessage": "« [Node 359] [REQ] [BridgeApplicationCommand]\n     │ RSSI: -83 dBm\n     └[Security2CCMessageEncapsulation]",
          "time": "2026-07-15 12:10:43.446"}


class TestClusterName(unittest.TestCase):
    def test_known_cluster(self):
        self.assertEqual(m.cluster_name("0500"), "IAS Zone")
        self.assertEqual(m.cluster_name("0006"), "On/Off")

    def test_hex_prefix_and_padding(self):
        self.assertEqual(m.cluster_name("0x6"), "On/Off")

    def test_manufacturer_specific_range(self):
        # ZCL manufacturer-specific is 0xFC00–0xFFFE
        self.assertEqual(m.cluster_name("FC01"), "manufacturer-specific")
        self.assertEqual(m.cluster_name("FFFE"), "manufacturer-specific")

    def test_vendor_custom_reserved_range_not_manufacturer(self):
        # 0xE000–0xEFFF is reserved space Tuya-family devices squat on — NOT manufacturer-specific
        self.assertEqual(m.cluster_name("E002"), "vendor-custom (reserved range)")
        self.assertEqual(m.cluster_name("E000"), "vendor-custom (reserved range)")

    def test_unknown(self):
        self.assertEqual(m.cluster_name("0999"), "unknown")
        self.assertEqual(m.cluster_name("FFFF"), "unknown")  # 0xFFFF is not a usable cluster id


class TestParseZigbee(unittest.TestCase):
    def test_fields_and_cluster(self):
        f = m.parse_zigbee_frame(ZB_RAW)
        self.assertEqual(f["radio"], "zigbee")
        self.assertEqual(f["cluster"], "IAS Zone")
        self.assertEqual((f["lqi"], f["rssi"], f["seq"]), (204, -49, 85))
        self.assertEqual(f["name"], "Stairs Top Motion Sensor")


class TestDecodeFrame(unittest.TestCase):
    def test_valid_object_frame_decodes(self):
        import json
        f = m.decode_frame(json.dumps(ZB_RAW), "zigbee")
        self.assertEqual(f["cluster"], "IAS Zone")

    def test_malformed_json_is_none(self):
        self.assertIsNone(m.decode_frame("{not json", "zigbee"))

    def test_json_valid_but_not_an_object_is_none(self):
        # the version-sensitive socket could emit a bare number/list/string — must skip, not crash
        for text in ("123", "[1, 2, 3]", '"a string"', "null"):
            self.assertIsNone(m.decode_frame(text, "zigbee"))
            self.assertIsNone(m.decode_frame(text, "zwave"))

    def test_wrong_typed_numeric_field_does_not_crash_aggregation(self):
        import json
        bad = {**ZB_RAW, "lastHopLqi": "oops", "lastHopRssi": None, "sequence": "x"}
        f = m.decode_frame(json.dumps(bad), "zigbee")
        self.assertIsNone(f["lqi"])
        self.assertIsNone(f["rssi"])
        m.summarize([f])  # must not raise on the wrong-typed frame


class TestParseZwave(unittest.TestCase):
    def test_node_and_rssi_extracted_from_text(self):
        f = m.parse_zwave_frame(ZW_RAW)
        self.assertEqual(f["radio"], "zwave")
        self.assertEqual(f["node"], 359)
        self.assertEqual(f["rssi"], -83)
        self.assertEqual(f["sourceLabel"], "DRIVER")
        self.assertNotIn("\n", f["text"])  # multi-line block collapsed

    def test_hub_line_without_node(self):
        f = m.parse_zwave_frame({"sourceLabel": "SERIAL", "deviceId": -999,
                                 "plainTextMessage": "» [ACK] (0x06)", "time": "t"})
        self.assertIsNone(f["node"])
        self.assertIsNone(f["rssi"])


TXREPORT = ("« [REQ] [SendDataBridge] callback id: 244 transmit status: OK, took 40 ms "
            "routing attempts: 2 protocol & route speed: Z-Wave Long Range, 100 kbit/s "
            "routing scheme: Resort to Direct ACK RSSI: -91 dBm ACK channel no.: 3 TX channel no.: 3 "
            "TX power: 14 dBm measured noise floor: -92 dBm ACK TX power by destination: 14 dBm "
            "measured RSSI of ACK from destination: -85 dBm measured noise floor by destination: -96 dBm")


class TestTransmitReport(unittest.TestCase):
    def test_fields_and_per_direction_snr(self):
        t = m.parse_transmit_report(TXREPORT)
        self.assertEqual((t["status"], t["took_ms"], t["routing_attempts"], t["tx_power"]), ("OK", 40, 2, 14))
        # hub side hears the device at -91 vs a -92 floor => 1 dB; device hears hub at -85 vs -96 => 11 dB
        self.assertEqual((t["hub_noise_floor"], t["dest_noise_floor"]), (-92, -96))
        self.assertEqual((t["hub_ack_rssi"], t["dest_rssi"]), (-91, -85))
        self.assertEqual((t["hub_snr"], t["dest_snr"]), (1, 11))

    def test_hub_noise_floor_regex_not_confused_by_destination(self):
        # "measured noise floor:" (hub) must not match "measured noise floor by destination:"
        t = m.parse_transmit_report(TXREPORT)
        self.assertNotEqual(t["hub_noise_floor"], t["dest_noise_floor"])

    def test_frame_gets_transmit_subdict(self):
        f = m.parse_zwave_frame({"sourceLabel": "DRIVER", "plainTextMessage": TXREPORT, "time": "t"})
        self.assertEqual(f["transmit"]["hub_snr"], 1)

    def test_non_report_frame_has_no_transmit(self):
        f = m.parse_zwave_frame({"sourceLabel": "SERIAL", "plainTextMessage": "» [ACK] (0x06)", "time": "t"})
        self.assertNotIn("transmit", f)


class TestRssiSentinel(unittest.TestCase):
    def test_positive_dbm_dropped_zwave(self):
        f = m.parse_zwave_frame({"sourceLabel": "DRIVER",
                                 "plainTextMessage": "« [Node 260] RSSI: 78 dBm", "time": "t"})
        self.assertIsNone(f["rssi"])  # +78 dBm is an invalid sentinel, not a real reading

    def test_real_negative_dbm_kept(self):
        f = m.parse_zwave_frame({"sourceLabel": "DRIVER",
                                 "plainTextMessage": "« [Node 260] RSSI: -85 dBm", "time": "t"})
        self.assertEqual(f["rssi"], -85)

    def test_zigbee_positive_rssi_dropped(self):
        f = m.parse_zigbee_frame({**ZB_RAW, "lastHopRssi": 78})
        self.assertIsNone(f["rssi"])


class TestMatches(unittest.TestCase):
    def test_zigbee_name_filter(self):
        f = m.parse_zigbee_frame(ZB_RAW)
        self.assertTrue(m.matches(f, name_substr="stairs"))
        self.assertFalse(m.matches(f, name_substr="kitchen"))

    def test_zwave_node_filter(self):
        f = m.parse_zwave_frame(ZW_RAW)
        self.assertTrue(m.matches(f, node=359))
        self.assertFalse(m.matches(f, node=360))

    def test_cluster_filter_by_name_or_id(self):
        f = m.parse_zigbee_frame(ZB_RAW)
        self.assertTrue(m.matches(f, cluster="IAS Zone"))
        self.assertTrue(m.matches(f, cluster="0500"))
        self.assertFalse(m.matches(f, cluster="On/Off"))


class TestSequenceTracker(unittest.TestCase):
    def test_contiguous_no_gap(self):
        t = m.SequenceTracker()
        self.assertEqual(t.observe("dev", 10), 0)  # first
        self.assertEqual(t.observe("dev", 11), 0)  # contiguous
        self.assertEqual(t.gaps.get("dev", 0), 0)

    def test_gap_detected_and_accumulated(self):
        t = m.SequenceTracker()
        t.observe("dev", 10)
        self.assertEqual(t.observe("dev", 14), 3)  # 3 missing between 10 and 14
        self.assertEqual(t.gaps["dev"], 3)

    def test_wraparound(self):
        t = m.SequenceTracker()
        t.observe("dev", 255)
        self.assertEqual(t.observe("dev", 0), 0)  # 255 -> 0 is contiguous mod 256

    def test_independent_devices(self):
        t = m.SequenceTracker()
        t.observe("a", 5); t.observe("b", 100)
        self.assertEqual(t.observe("a", 6), 0)
        self.assertEqual(t.observe("b", 105), 4)
        self.assertEqual(t.gaps.get("a", 0), 0)


class TestSummarize(unittest.TestCase):
    def _zb(self, name, seq, lqi, rssi):
        return m.parse_zigbee_frame({**ZB_RAW, "name": name, "sequence": seq,
                                     "lastHopLqi": lqi, "lastHopRssi": rssi})

    def test_per_device_rollup_and_worst_first(self):
        frames = [self._zb("Strong", 1, 200, -40), self._zb("Strong", 2, 210, -42),
                  self._zb("Weak", 10, 90, -85)]
        s = m.summarize(frames)
        self.assertEqual(s["device_count"], 2)
        self.assertEqual(list(s["devices"])[0], "Weak")  # weakest avg RSSI first
        self.assertEqual(s["devices"]["Strong"]["frames"], 2)
        self.assertEqual(s["devices"]["Strong"]["rssi"], {"min": -42, "avg": -41.0, "n": 2})

    def test_sequence_gaps_attach_to_device(self):
        frames = [self._zb("Flaky", 1, 100, -70), self._zb("Flaky", 5, 100, -70)]
        s = m.summarize(frames)
        self.assertEqual(s["devices"]["Flaky"]["sequence_gaps"], 3)

    def test_zwave_rollup_by_node(self):
        frames = [m.parse_zwave_frame(ZW_RAW), m.parse_zwave_frame(ZW_RAW)]
        s = m.summarize(frames)
        self.assertIn("Node 359", s["devices"])
        self.assertEqual(s["devices"]["Node 359"]["rssi"], {"min": -83, "avg": -83.0, "n": 2})

    def test_transmit_report_rollup(self):
        frames = [m.parse_zwave_frame({"sourceLabel": "DRIVER", "plainTextMessage": TXREPORT, "time": "t"})]
        s = m.summarize(frames)
        tr = s["transmit_report"]
        self.assertEqual(tr["reports"], 1)
        self.assertEqual((tr["hub_snr_med"], tr["dest_snr_med"]), (1, 11))  # the hub-side bottleneck
        self.assertEqual(tr["hub_noise_floor_med"], -92)
        self.assertEqual(tr["retransmits"], 1)

    def test_no_transmit_report_key_when_absent(self):
        s = m.summarize([m.parse_zwave_frame(ZW_RAW)])
        self.assertNotIn("transmit_report", s)


if __name__ == "__main__":
    unittest.main()
