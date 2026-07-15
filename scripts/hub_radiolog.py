#!/usr/bin/env python3
"""Tail a Hubitat hub's live Z-Wave or Zigbee RADIO log socket and read the per-frame traffic.

Distinct from hub_logtail.py: that tails the driver/app log (`/logsocket`). These are the
dedicated radio-protocol log streams (verified live on 2.5.1.128 — see reference/endpoints.md):
    ws://<ip>/zwaveLogsocket    (case-sensitive) — decoded Z-Wave controller/driver frames
    ws://<ip>/zigbeeLogsocket   (case-sensitive) — structured Zigbee frame JSON
No auth on a local hub with Hub Security off. The handshake and frame de-chunking are reused
from hub_logtail (already unit-tested); only the parse/aggregate logic is new here.

Frame shapes (grounded):
  Z-Wave  {sourceLabel, plainTextMessage, deviceId, time}
      sourceLabel ∈ SERIAL | CNTRLR | DRIVER; plainTextMessage is the decoded frame text, e.g.
      "[Node 359] [REQ] [BridgeApplicationCommand] │ RSSI: -83 dBm └[Security2CC...]". The node
      id and per-frame RSSI live IN that text (deviceId is -999 for hub-level lines), so they are
      extracted with fixed-format regexes — everything else is passed through verbatim.
      A frame carrying a TransmitReport ("transmit status: ...") also gets a structured `transmit`
      sub-dict — the richest RF diagnostic: per-direction noise floor + signal (→ hub_snr/dest_snr),
      real latency (took_ms), retransmits, TX power. hub_* is at the controller, dest_* at the
      device; a hub SNR far below the device SNR points at the hub's RF environment, not the device.
      Invalid RSSI sentinels (a positive dBm like +78) are dropped, not reported as real.
  Zigbee  {name, id, deviceId, profileId, clusterId, sourceEndpoint, destinationEndpoint,
           groupId, sequence, lastHopLqi, lastHopRssi, time, type, payload}
      Carries per-frame lastHopLqi (0–255) and lastHopRssi (dBm) — the per-device signal the
      Zigbee Details SNAPSHOT does not expose. These are the signal of the LAST HOP into the hub:
      for a device that routes through a repeater, they reflect the repeater→hub link, not the
      end device's own radio. `sequence` is a per-frame counter shared across the device's traffic,
      so a gap is a soft missed-frame hint, not a hard per-cluster drop count.

No absolute "bad" thresholds are asserted (Hubitat publishes none). Signal weakness is a labeled
heuristic; the value of this tool is the live per-device signal + sequence continuity, surfaced
for the agent to judge against rules/zwave-zigbee-mesh.md.

Usage:
    hub_radiolog.py --ip <addr> --radio zigbee [--name SUBSTR] [--seconds 20]
    hub_radiolog.py --ip <addr> --radio zwave  [--node 359] [--follow]
    hub_radiolog.py --ip <addr> --radio zigbee --summary [--seconds 30]   # per-device rollup
Output is structured JSON by default (per-frame JSON objects); --text switches to human-formatted
lines for watching live by eye; --summary aggregates the window into a JSON per-device rollup.
"""
import argparse
import json
import re
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# E402: imports follow the sys.path insert so the sibling modules resolve when run as a script.
from hub_logtail import build_handshake, iter_frames  # noqa: E402

RADIO_SOCKETS = {"zwave": "/zwaveLogsocket", "zigbee": "/zigbeeLogsocket"}

# Zigbee Cluster Library IDs → human names (the common home-automation clusters, per the ZCL
# spec). Unknown ids are classified in cluster_name(), never guessed. Extend from the ZCL spec.
ZCL_CLUSTERS = {
    "0000": "Basic", "0001": "Power Configuration", "0003": "Identify", "0004": "Groups",
    "0005": "Scenes", "0006": "On/Off", "0008": "Level Control", "0019": "OTA Upgrade",
    "0020": "Poll Control", "0102": "Window Covering", "0201": "Thermostat",
    "0300": "Color Control", "0400": "Illuminance", "0402": "Temperature",
    "0405": "Humidity", "0406": "Occupancy", "0500": "IAS Zone", "0501": "IAS ACE",
    "0702": "Metering", "0b04": "Electrical Measurement", "0b05": "Diagnostics",
}

_ZW_NODE_RE = re.compile(r"\[Node (\d+)\]")
_ZW_RSSI_RE = re.compile(r"RSSI:\s*(-?\d+)\s*dBm", re.IGNORECASE)
# zwaveJS TransmitReport fields — the richest RF diagnostic (per-direction noise floor + signal
# → SNR, real latency, retransmits). `measured noise floor:` is at the controller; the same words
# `... by destination:` are at the device, so the hub regex intentionally requires the colon.
_ZW_TXSTATUS_RE = re.compile(r"transmit status: (\w+)")
_ZW_TOOK_RE = re.compile(r"took (\d+) ms")
_ZW_ROUTES_RE = re.compile(r"routing attempts: (\d+)")
_ZW_TXPOWER_RE = re.compile(r"\bTX power: (-?\d+) dBm")
_ZW_HUBNF_RE = re.compile(r"measured noise floor: (-?\d+) dBm")
_ZW_DESTNF_RE = re.compile(r"measured noise floor by destination: (-?\d+) dBm")
_ZW_ACKRSSI_RE = re.compile(r"\bACK RSSI: (-?\d+) dBm")
_ZW_DESTRSSI_RE = re.compile(r"measured RSSI of ACK from destination: (-?\d+) dBm")


def _valid_dbm(v):
    """A received-signal RSSI on the zwave/zigbee logs is negative dBm (~ -30..-110). Positive or
    implausible values are zwaveJS invalid/sentinel readings (e.g. +78) — return None, not garbage."""
    return v if isinstance(v, (int, float)) and -120 <= v <= 0 else None


def _search_int(rx, text):
    m = rx.search(text)
    return int(m.group(1)) if m else None


def parse_transmit_report(text: str) -> dict:
    """Extract the TransmitReport fields from a decoded Z-Wave frame's text. hub_* is measured at
    the controller, dest_* at the device — the asymmetry (which end has the worse SNR / higher
    noise floor) points at whether a link problem is on the hub side or the device side."""
    status = _ZW_TXSTATUS_RE.search(text)
    hub_rssi = _valid_dbm(_search_int(_ZW_ACKRSSI_RE, text))
    hub_nf = _valid_dbm(_search_int(_ZW_HUBNF_RE, text))
    dest_rssi = _valid_dbm(_search_int(_ZW_DESTRSSI_RE, text))
    dest_nf = _valid_dbm(_search_int(_ZW_DESTNF_RE, text))
    return {
        "status": status.group(1) if status else None,
        "took_ms": _search_int(_ZW_TOOK_RE, text),
        "routing_attempts": _search_int(_ZW_ROUTES_RE, text),
        "tx_power": _search_int(_ZW_TXPOWER_RE, text),
        "hub_noise_floor": hub_nf,          # controller's receive noise floor
        "dest_noise_floor": dest_nf,        # device's receive noise floor
        "hub_ack_rssi": hub_rssi,           # device->hub signal, as heard at the hub
        "dest_rssi": dest_rssi,             # hub->device signal, as heard at the device
        "hub_snr": hub_rssi - hub_nf if hub_rssi is not None and hub_nf is not None else None,
        "dest_snr": dest_rssi - dest_nf if dest_rssi is not None and dest_nf is not None else None,
    }


def cluster_name(cluster_id) -> str:
    """Map a Zigbee clusterId (hex string, e.g. '0500') to a ZCL name, or classify it."""
    if cluster_id is None:
        return ""
    key = str(cluster_id).lower().removeprefix("0x").rjust(4, "0")
    if key in ZCL_CLUSTERS:
        return ZCL_CLUSTERS[key]
    try:
        val = int(key, 16)
    except ValueError:
        return "unknown"
    # ZCL manufacturer-specific range is 0xFC00–0xFFFE (requires a manufacturer code); 0xFFFF is
    # not a usable cluster id. 0xE000–0xEFFF is RESERVED ZCL space that Tuya-family devices (e.g.
    # the presence sensors reporting E002) squat on off-spec — vendor-custom, NOT the ZCL
    # manufacturer range. Keep the two distinct (verified against the ZCL spec, 2026-07-15).
    if 0xFC00 <= val <= 0xFFFE:
        return "manufacturer-specific"
    if 0xE000 <= val <= 0xEFFF:
        return "vendor-custom (reserved range)"
    return "unknown"


def _num(v):
    """Coerce a value to a real int/float, else None — defends the ranking/aggregation against a
    version-changed socket sending a wrong-typed numeric field (a string LQI would crash min())."""
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def parse_zigbee_frame(f: dict) -> dict:
    """Normalize a raw Zigbee log frame to the fields worth reading/aggregating."""
    return {
        "radio": "zigbee",
        "name": f.get("name") or "",
        "id": f.get("id"),
        "deviceId": f.get("deviceId"),
        "clusterId": f.get("clusterId"),
        "cluster": cluster_name(f.get("clusterId")),
        "srcEp": f.get("sourceEndpoint"),
        "dstEp": f.get("destinationEndpoint"),
        "seq": _num(f.get("sequence")),
        "lqi": _num(f.get("lastHopLqi")),
        "rssi": _valid_dbm(_num(f.get("lastHopRssi"))),  # negative dBm; drop invalid sentinels
        "type": f.get("type"),
        "time": f.get("time"),
    }


def parse_zwave_frame(f: dict) -> dict:
    """Normalize a raw Z-Wave log frame. Node id and RSSI come from the decoded text; a frame that
    carries a TransmitReport (`transmit status: ...`) gets a structured `transmit` sub-dict."""
    text = f.get("plainTextMessage") or ""
    node = _ZW_NODE_RE.search(text)
    rssi = _ZW_RSSI_RE.search(text)
    out = {
        "radio": "zwave",
        "sourceLabel": f.get("sourceLabel"),
        "node": int(node.group(1)) if node else None,
        "rssi": _valid_dbm(int(rssi.group(1))) if rssi else None,  # drop +78-style invalid readings
        "text": " ".join(text.split()),  # collapse the multi-line decoded block to one line
        "time": f.get("time"),
    }
    if _ZW_TXSTATUS_RE.search(text):
        out["transmit"] = parse_transmit_report(text)
    return out


def matches(frame: dict, name_substr=None, node=None, device_id=None, cluster=None) -> bool:
    """Pure filter predicate over a normalized frame (either radio)."""
    if name_substr and name_substr.lower() not in (frame.get("name") or "").lower():
        return False
    if node is not None and frame.get("node") != node:
        return False
    if device_id is not None and str(frame.get("deviceId")) != str(device_id):
        return False
    if cluster and cluster.lower() not in (frame.get("cluster") or "").lower() \
            and cluster.lower() != str(frame.get("clusterId") or "").lower():
        return False
    return True


def format_frame(frame: dict, as_json: bool = False) -> str:
    if as_json:
        return json.dumps(frame, sort_keys=True)
    t = frame.get("time", "")
    if frame["radio"] == "zigbee":
        return (f"{t} {frame['name'] or ('id ' + str(frame['id']))}: {frame['cluster']} "
                f"(0x{str(frame['clusterId']).lower()}) ep{frame['srcEp']}->{frame['dstEp']} "
                f"seq={frame['seq']} lqi={frame['lqi']} rssi={frame['rssi']}dBm")
    node = f"Node {frame['node']}" if frame["node"] is not None else "hub"
    rssi = f" rssi={frame['rssi']}dBm" if frame["rssi"] is not None else ""
    return f"{t} [{frame['sourceLabel']}] {node}{rssi}: {frame['text']}"


class SequenceTracker:
    """Per-device Zigbee sequence continuity. A jump > 1 (mod 256) between consecutive frames
    from the same device means intervening frames were not heard by the hub — a soft drop signal,
    not a hard error (other traffic and frame types share the counter)."""

    def __init__(self):
        self.last = {}
        self.gaps = {}

    def observe(self, device_key, seq) -> int:
        """Return the gap size for this frame (0 = contiguous/first), and accumulate per device."""
        if seq is None or device_key is None:
            return 0
        prev = self.last.get(device_key)
        self.last[device_key] = seq
        if prev is None:
            return 0
        delta = (seq - prev) % 256
        gap = delta - 1 if delta >= 1 else 0
        if gap > 0:
            self.gaps[device_key] = self.gaps.get(device_key, 0) + gap
        return gap


def summarize(frames: list) -> dict:
    """Pure. Aggregate a window of normalized frames into a per-device rollup: frame count,
    signal min/avg (LQI + RSSI for Zigbee, RSSI for Z-Wave), and observed sequence gaps."""
    tracker = SequenceTracker()
    devices = {}
    for fr in frames:
        if fr["radio"] == "zigbee":
            key = fr["name"] or f"id:{fr['id']}"
        else:
            key = f"Node {fr['node']}" if fr.get("node") is not None else "hub"
        tracker.observe(key, fr.get("seq"))  # same key as the rollup so gaps attach
        d = devices.setdefault(key, {"frames": 0, "lqi": [], "rssi": [], "clusters": set()})
        d["frames"] += 1
        if fr.get("lqi") is not None:
            d["lqi"].append(fr["lqi"])
        if fr.get("rssi") is not None:
            d["rssi"].append(fr["rssi"])
        if fr.get("cluster"):
            d["clusters"].add(fr["cluster"])

    def stat(xs):
        return None if not xs else {"min": min(xs), "avg": round(sum(xs) / len(xs), 1), "n": len(xs)}

    out = {}
    for key, d in devices.items():
        out[key] = {
            "frames": d["frames"],
            "lqi": stat(d["lqi"]),
            "rssi": stat(d["rssi"]),
            "clusters": sorted(d["clusters"]),
            "sequence_gaps": tracker.gaps.get(key, 0),
        }
    result = {"device_count": len(out), "devices": dict(sorted(out.items(),
              # worst signal first: sort by average RSSI ascending (weakest at the top)
              key=lambda kv: (kv[1]["rssi"]["avg"] if kv[1]["rssi"] else 0)))}

    # Z-Wave TransmitReport rollup — the hub-vs-device asymmetry that localizes a link problem.
    # A hub noise floor worse than the devices', with hub-side SNR below device-side, points at the
    # hub's RF environment (not the device, not distance). Median over the window's reports.
    tx = [fr["transmit"] for fr in frames if fr.get("transmit")]
    if tx:
        def med(vals):
            vals = sorted(v for v in vals if v is not None)
            return None if not vals else vals[len(vals) // 2]
        result["transmit_report"] = {
            "reports": len(tx),
            "noack": sum(1 for t in tx if t["status"] and t["status"] != "OK"),
            "retransmits": sum(1 for t in tx if (t["routing_attempts"] or 0) > 1),
            "took_ms_med": med(t["took_ms"] for t in tx),
            "hub_noise_floor_med": med(t["hub_noise_floor"] for t in tx),
            "dest_noise_floor_med": med(t["dest_noise_floor"] for t in tx),
            "hub_snr_med": med(t["hub_snr"] for t in tx),      # device->hub headroom
            "dest_snr_med": med(t["dest_snr"] for t in tx),    # hub->device headroom
        }
    return result


def parse_frame(raw: dict, radio: str) -> dict:
    return parse_zigbee_frame(raw) if radio == "zigbee" else parse_zwave_frame(raw)


def decode_frame(text: str, radio: str):
    """Decode one raw socket text frame to a normalized dict, or None when it is not a well-formed
    JSON object. The radio sockets are undocumented and version-sensitive, so a malformed frame OR
    a JSON value that is not an object (a bare number, list, or string from a shape change) must
    skip the frame, never crash the tail."""
    try:
        raw = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    return parse_frame(raw, radio)


def _run(ip: str, radio: str, filters: dict, seconds, follow, summary, as_json, out) -> int:
    request, _ = build_handshake(ip, RADIO_SOCKETS[radio])
    try:
        sock = socket.create_connection((ip, 80), timeout=10)
    except OSError as e:
        print(f"cannot connect to {ip}:80 — {e}. Confirm the hub IP is correct and reachable "
              f"(ping it, or check hubs.json), and that Hub Security is off — the radio log "
              f"sockets are unauthenticated local sockets on port 80.", file=sys.stderr)
        return 1
    collected = []
    try:
        sock.sendall(request)
        sock.settimeout(5.0)
        raw = b""
        while b"\r\n\r\n" not in raw and len(raw) < 8192:
            chunk = sock.recv(4096)
            if not chunk:
                break
            raw += chunk
        sep = raw.find(b"\r\n\r\n")
        header = (raw[:sep] if sep >= 0 else raw).decode("latin1", "replace")
        if "101" not in header.split("\r\n", 1)[0]:
            print(f"radio-log handshake failed: {header.splitlines()[0] if header else '(no response)'}. "
                  f"The {RADIO_SOCKETS[radio]} endpoint may not exist on this firmware or Hub Security "
                  f"may be on — verify the path against reference/endpoints.md and that the hub is on a "
                  f"supported platform.", file=sys.stderr)
            return 1
        buf = bytearray(raw[sep + 4:]) if sep >= 0 else bytearray()
        sock.settimeout(1.0)
        deadline = None if follow else time.monotonic() + seconds
        while follow or deadline is None or time.monotonic() < deadline:
            try:
                chunk = sock.recv(8192)
            except socket.timeout:
                continue
            if not chunk:
                break
            buf.extend(chunk)
            for opcode, payload in iter_frames(buf):
                if opcode == 0x8:
                    break
                if opcode not in (0x1, 0x2):
                    continue
                frame = decode_frame(payload.decode("utf-8", "replace"), radio)
                if frame is None or not matches(frame, **filters):
                    continue
                if summary:
                    collected.append(frame)
                else:
                    out.write(format_frame(frame, as_json) + "\n")
                    out.flush()
    except KeyboardInterrupt:
        pass
    except OSError as e:
        print(f"radio-log connection to {ip} failed: {e}. The hub may have restarted or dropped "
              f"the socket — re-run to reconnect.", file=sys.stderr)
        return 1
    finally:
        sock.close()
    if summary:
        out.write(json.dumps(summarize(collected), indent=2, default=str) + "\n")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Tail a Hubitat Z-Wave/Zigbee radio log socket.")
    p.add_argument("--ip", required=True)
    p.add_argument("--radio", required=True, choices=["zwave", "zigbee"])
    p.add_argument("--name", help="Zigbee device-name substring filter")
    p.add_argument("--node", type=int, help="Z-Wave node id filter")
    p.add_argument("--device-id", help="Hubitat device id filter")
    p.add_argument("--cluster", help="Zigbee cluster filter (name or hex id)")
    p.add_argument("--seconds", type=int, default=20)
    p.add_argument("--follow", action="store_true", help="run until interrupted")
    p.add_argument("--summary", action="store_true", help="aggregate the window into a per-device rollup")
    p.add_argument("--text", action="store_true",
                   help="human-formatted lines instead of the default JSON (for watching live by eye)")
    args = p.parse_args(argv)

    filters = {"name_substr": args.name, "node": args.node,
               "device_id": args.device_id, "cluster": args.cluster}
    # Structured JSON is the default (this is a skill-invoked deterministic script); --text opts
    # into human-formatted lines. --summary emits a JSON rollup regardless.
    return _run(args.ip, args.radio, filters, args.seconds, args.follow,
                args.summary, not args.text, sys.stdout)


if __name__ == "__main__":
    sys.exit(main())
