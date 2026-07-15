#!/usr/bin/env python3
"""Fetch a Hubitat hub's Z-Wave and Zigbee mesh detail and flag network problems.

The hub exposes two undocumented JSON endpoints (verified live on 2.5.1.125, C-8 Pro —
see reference/endpoints.md):
    GET /hub/zwaveDetails/json   -> {enabled, healthy, zwaveJS, region, nodes[...]}
    GET /hub/zigbeeDetails/json  -> {networkState, healthy, channel, weakChannel, devices[...]}
No auth on a local hub with Hub Security off.

Grounding (rules/zwave-zigbee-mesh.md carries the citations):
  - PER is a cumulative packet-ERROR COUNT ("accumulation of packet errors for a node"),
    not a percentage. Lower is better; 0 is ideal. Hubitat publishes NO absolute cutoff.
  - RTT Avg is round-trip ms; lower is better.
  - lwrRssi is reported on TWO scales depending on the Z-Wave backend: the zwaveJS backend
    reports absolute dBm (negative; closer to 0 = stronger), the legacy backend reports dB
    above the noise floor (positive = good). "Higher is better" holds on both; a fixed
    numeric cutoff does NOT transfer across backends. Silicon Labs RX sensitivity floor:
    -97 dBm (700-series), -110 dBm (800-series).
  - nodeState FAILED marks a failed/ghost node.
  - Zigbee's zigbeeDetails snapshot exposes no per-device LQI/RSSI — only liveness (active,
    lastActivity, messageCount) and network-level state (channel, weakChannel, healthy,
    networkState, powerLevel). Per-device LQI lives in /hub/zigbee/getChildAndRouteInfo
    (neighbor table); per-frame LQI+RSSI in the live zigbeeLogsocket.

Because Hubitat gives no numeric "bad" thresholds, this script HARD-flags only unambiguous,
grounded signals (FAILED nodes, nonzero PER, dead/incomplete Zigbee joins, an unhealthy
network) and otherwise RANKS nodes worst-first so the agent can judge severity against the
rule. The one signal-quality heuristic (RSSI at/below the radio's sensitivity floor) is
emitted with heuristic:true and a cited basis, never as a hard fact.

The deterministic pieces — parsing, ranking, flagging — are pure functions taking
already-parsed JSON and are unit-tested without a hub. Only fetch() touches the network.

Usage:
    hub_mesh.py --ip 192.168.30.2
    hub_mesh.py --hub main            # resolve via ./hubs.json (hub-config skill)
    hub_mesh.py --ip 192.168.30.2 --radio zwave    # one radio only
Output: a single JSON object on stdout (see analyze()); non-zero exit on a fetch failure.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
# E402: this import must follow the sys.path insert above so hubclient resolves when run as a script.
from hubclient import HubError, _urllib_transport, resolve_base_from_args  # noqa: E402

ZWAVE_PATH = "/hub/zwaveDetails/json"
ZIGBEE_PATH = "/hub/zigbeeDetails/json"

# Silicon Labs published receiver sensitivity floor, by Z-Wave series (dBm). Used only for
# the backend-aware RSSI heuristic on the zwaveJS (absolute-dBm) scale. Source: silabs.com.
# Note the modulation: -97 is 700-series 100 kbps GFSK; -110 is the 800-series LR channel
# (100 kbps O-QPSK). The classic GFSK floor on 800 is a few dB higher, so this is a
# conservative floor for a labeled heuristic, not an exact per-node threshold.
SILABS_SENSITIVITY_DBM = {"700": -97, "800": -110}
# A zwaveJS RSSI within this margin of the floor is flagged "near sensitivity floor". Margin,
# not an absolute cutoff — the floor itself is the grounded number; the margin is judgment.
RSSI_FLOOR_MARGIN_DB = 10


def parse_rssi(raw) -> Optional[float]:
    """'-78db' -> -78.0, '27dB' -> 27.0, '', 'Unknown', None -> None. Sign/scale is the
    caller's concern (backend-dependent); this only extracts the number."""
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s.endswith("db"):  # 3.8-compatible suffix strip (no str.removesuffix)
        s = s[:-2]
    try:
        return float(s.strip())
    except ValueError:
        return None


def parse_num(raw) -> Optional[float]:
    """Parse an int/float-ish value; '', 'N/A', None -> None."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s.upper() == "N/A":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_ts(raw) -> Optional[datetime]:
    """Parse a hub timestamp to an aware UTC datetime, or None. Handles '+0000' (no colon),
    trailing 'Z', and naive stamps (assumed UTC)."""
    if not raw:
        return None
    s = str(raw).strip().replace("Z", "+00:00")
    # '+0000' / '-0500' -> '+00:00' so fromisoformat accepts it
    if len(s) >= 5 and s[-5] in "+-" and s[-3] != ":":
        s = s[:-2] + ":" + s[-2:]
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _age_seconds(ts: Optional[datetime], now: datetime) -> Optional[int]:
    if ts is None:
        return None
    return int((now - ts).total_seconds())


def zwave_backend(details: dict) -> str:
    """'zwavejs' or 'legacy' — decides how lwrRssi is scaled."""
    return "zwavejs" if details.get("zwaveJS") else "legacy"


def rssi_heuristic(rssi: Optional[float], backend: str, series: str = "800"):
    """Backend-aware weak-signal heuristic. Returns a dict or None. zwaveJS scale is absolute
    dBm (near the Silabs floor = weak); legacy scale is dB above noise (<=0 = at/below noise,
    per Hubitat staff). Always marked heuristic with a cited basis — never a hard fact."""
    if rssi is None:
        return None
    if backend == "zwavejs":
        floor = SILABS_SENSITIVITY_DBM.get(series, -97)
        if rssi <= floor + RSSI_FLOOR_MARGIN_DB:
            return {"heuristic": True, "signal": "rssi_near_floor",
                    "basis": f"{rssi:g} dBm within {RSSI_FLOOR_MARGIN_DB} dB of the "
                             f"Silabs {series}-series floor {floor} dBm"}
    else:  # legacy: reported as dB above the noise floor; <= 0 means at/below noise
        if rssi <= 0:
            return {"heuristic": True, "signal": "rssi_at_or_below_noise",
                    "basis": f"{rssi:g} dB above noise <= 0 (at/below noise floor)"}
    return None


def node_topology(node_id) -> str:
    """Z-Wave LR node ids are >= 256; classic mesh is 1..232 (Z-Wave Alliance / Silicon Labs).
    LR is a star (no neighbors, no routes, no repeaters); mesh is where routing applies. The two
    coexist on one hub, so topology is per-node, not per-hub."""
    try:
        return "lr" if int(node_id) >= 256 else "mesh"
    except (TypeError, ValueError):
        return "unknown"


def normalize_zwave_node(node: dict) -> dict:
    """Project a raw hub node to the fields the analysis ranks/flags on."""
    return {
        "nodeId": node.get("nodeId"),
        "topology": node_topology(node.get("nodeId")),  # 'lr' | 'mesh' — remediation differs
        "deviceId": node.get("deviceId"),
        "deviceName": node.get("deviceName") or "",
        "nodeState": node.get("nodeState"),
        "msgCount": node.get("msgCount"),  # traffic volume — weigh PER against it
        "per": int(node["per"]) if isinstance(node.get("per"), (int, float)) else parse_num(node.get("per")),
        "rtt_ms": parse_num(node.get("averageRtt")),
        "rssi": parse_rssi(node.get("lwrRssi")),
        "rssi_raw": node.get("lwrRssi"),
        "neighbors": node.get("neighbors"),
        "routeChanges": parse_num(node.get("routeChanges")),
        "route": node.get("route") or "",
        "security": node.get("security"),
        "battery": node.get("batteryPercent"),
    }


def analyze_zwave(details: dict) -> dict:
    """Pure. Flag failed/ghost nodes and nonzero PER; rank worst-first by PER, RTT, RSSI."""
    backend = zwave_backend(details)
    series = "700" if "700" in str(details.get("firmwareVersion", "")) else "800"
    nodes = [normalize_zwave_node(n) for n in details.get("nodes") or []]

    failed, packet_errors, weak_signal = [], [], []
    for n in nodes:
        if str(n["nodeState"]).upper() == "FAILED":
            failed.append(n)
        if n["per"] and n["per"] > 0:
            packet_errors.append(n)
        h = rssi_heuristic(n["rssi"], backend, series)
        if h:
            weak_signal.append({**n, **h})

    def worst(key, reverse):
        have = [n for n in nodes if n[key] is not None]
        return sorted(have, key=lambda n: n[key], reverse=reverse)

    return {
        "backend": backend,
        "healthy": details.get("healthy"),
        "node_count": len(nodes),
        "failed": failed,                               # CRITICAL: ghost/failed nodes
        "packet_errors": sorted(packet_errors, key=lambda n: n["per"], reverse=True),
        "weak_signal_heuristic": weak_signal,
        "ranked": {
            "by_per": [n for n in worst("per", True) if n["per"]][:10],
            "by_rtt_ms": worst("rtt_ms", True)[:10],
            # Higher RSSI is better on BOTH scales (absolute dBm and dB-above-noise), so
            # worst-first is always ascending. Backend only changes the floor heuristic, not this.
            "by_rssi": worst("rssi", False)[:10],
        },
    }


def analyze_zigbee(details: dict, now: datetime) -> dict:
    """Pure. Flag network-level problems and dead/incomplete device joins; report activity age.
    `now` is injected so age is deterministic under test (never call the clock in here)."""
    net_problems = []
    if not details.get("enabled", True):
        net_problems.append("radio disabled")
    if str(details.get("networkState", "ONLINE")).upper() != "ONLINE":
        net_problems.append(f"networkState={details.get('networkState')}")
    if details.get("healthy") is False:
        net_problems.append("healthy=false")
    if details.get("weakChannel"):
        net_problems.append(f"weakChannel on channel {details.get('channel')}")

    devices = []
    for d in details.get("devices") or []:
        last = parse_ts(d.get("lastActivity") or d.get("lastMessage"))
        devices.append({
            "id": d.get("id"), "name": d.get("name") or "", "type": d.get("type") or "",
            "active": d.get("active"), "messageCount": d.get("messageCount"),
            "lastActivity": d.get("lastActivity"),
            "age_seconds": _age_seconds(last, now),
        })

    # active==false = not communicating; a generic "Device"/"Device" name is an unfinished join.
    dead = [d for d in devices if d["active"] is False]
    for d in dead:
        d["likely_incomplete_join"] = (d["name"] == "Device" and d["type"] == "Device")

    ranked_age = sorted((d for d in devices if d["age_seconds"] is not None),
                        key=lambda d: d["age_seconds"], reverse=True)
    return {
        "enabled": details.get("enabled"),
        "networkState": details.get("networkState"),
        "healthy": details.get("healthy"),
        "channel": details.get("channel"),
        "weakChannel": details.get("weakChannel"),
        "powerLevel": details.get("powerLevel"),
        "network_problems": net_problems,               # CRITICAL when non-empty
        "device_count": len(devices),
        "dead_devices": dead,                            # active==false
        "stalest": ranked_age[:10],
    }


def analyze(zwave: Optional[dict], zigbee: Optional[dict], now: datetime) -> dict:
    """Combine the two radios and roll up a summary count. Either may be None (radio not
    fetched / not enabled)."""
    out: dict = {"zwave": None, "zigbee": None}
    critical = warnings = 0
    if zwave is not None:
        out["zwave"] = analyze_zwave(zwave)
        critical += len(out["zwave"]["failed"])
        warnings += len(out["zwave"]["packet_errors"])
    if zigbee is not None:
        out["zigbee"] = analyze_zigbee(zigbee, now)
        critical += len(out["zigbee"]["network_problems"])
        warnings += len(out["zigbee"]["dead_devices"])
    out["summary"] = {"critical": critical, "warnings": warnings}
    return out


def fetch(base: str, path: str, transport=None) -> dict:
    """GET a hub JSON endpoint. Raises HubError on non-200 or non-JSON (e.g. Hub Security on)."""
    transport = transport or _urllib_transport
    status, _, text = transport("GET", base.rstrip("/") + path, None)
    if status != 200:
        raise HubError(f"{base}{path} returned HTTP {status}")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise HubError(
            f"{base}{path} did not return JSON (got {text[:80]!r}). Check that Hub Security "
            f"is off on this hub and that the endpoint is valid on its firmware.") from e


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Flag Z-Wave/Zigbee mesh problems on a Hubitat hub.")
    p.add_argument("--ip")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--hub", help="named hub from hubs.json (when no --ip)")
    p.add_argument("--hubs", help="path to hubs.json (default ./hubs.json when --hub is given)")
    p.add_argument("--radio", choices=["zwave", "zigbee", "both"], default="both")
    args = p.parse_args(argv)

    try:
        base = resolve_base_from_args(ip=args.ip, port=args.port, hub=args.hub, hubs_path=args.hubs)
    except HubError as e:
        print(str(e), file=sys.stderr)
        return 2

    zwave = zigbee = None
    try:
        if args.radio in ("zwave", "both"):
            zwave = fetch(base, ZWAVE_PATH)
        if args.radio in ("zigbee", "both"):
            zigbee = fetch(base, ZIGBEE_PATH)
    except HubError as e:
        print(str(e), file=sys.stderr)
        return 1

    result = analyze(zwave, zigbee, datetime.now(timezone.utc))
    result["hub"] = base
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
