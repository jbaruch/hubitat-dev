#!/usr/bin/env python3
"""Fetch a Hubitat hub's Z-Wave, Zigbee, and hub-mesh detail and flag network problems.

The hub exposes three undocumented JSON endpoints (verified live on 2.5.1.128, C-8 Pro —
see ../_reference/endpoints.md):
    GET /hub/zwaveDetails/json   -> {enabled, healthy, zwaveJS, region, nodes[...]}
    GET /hub/zigbeeDetails/json  -> {networkState, healthy, channel, weakChannel, devices[...]}
    GET /hub2/hubMeshJson        -> {hubList:[{ipAddress, hubId, active, offline, warning, ...}]}
No auth on a local hub with Hub Security off.

Radios are not the only way devices go dead. Hub mesh carries COMMANDS between hubs, and a
mesh peer whose record holds a stale IP drops them silently while every radio metric stays
green — the hub reports such a peer as active:true, offline:false, warning:null (grounded on
a live 2026-07-16 outage; see CHANGELOG). Radio health cannot see that class of fault, so
hub-mesh peer health is analyzed here alongside the two radios.

Grounding (rules/zwave-zigbee-mesh.md carries the citations):
  - PER is a cumulative packet-ERROR COUNT ("accumulation of packet errors for a node"),
    not a percentage. Lower is better; 0 is ideal. Hubitat publishes NO absolute cutoff.
  - RTT Avg is round-trip ms; lower is better.
  - lwrRssi is reported on TWO scales depending on the Z-Wave backend: the zwaveJS backend
    reports absolute dBm (negative; closer to 0 = stronger), the legacy backend reports dB
    above the noise floor (positive = good). "Higher is better" holds on both; a fixed
    numeric cutoff does NOT transfer across backends. Silicon Labs RX sensitivity floor:
    -97 dBm (700-series), -110 dBm (800-series).
  - nodeState FAILED marks an unreachable node. It splits by deviceId: FAILED + a bound deviceId
    is a REAL device currently unreachable (may be transient — recover, don't delete); FAILED with
    NO deviceId is an orphan ghost (a pairing that never bound a device — safe to remove).
  - Zigbee's zigbeeDetails snapshot exposes no per-device LQI/RSSI — only liveness (active,
    lastActivity, messageCount) and network-level state (channel, weakChannel, healthy,
    networkState, powerLevel). Per-device LQI lives in /hub/zigbee/getChildAndRouteInfo
    (neighbor table); per-frame LQI+RSSI in the live zigbeeLogsocket.
  - ROUTE FAN-IN is a repeater's blast radius, the classic-mesh counterpart of a hub-mesh peer's
    shared_device_count: how many nodes route THROUGH it (zwave.route_fan_in). Like that count it
    is context, never a fault — a repeater carrying 12 nodes is a normal mesh — so it is ranked,
    never flagged, and reaches neither summary counter (see analyze()). It changes the reading of
    a repeater that is itself flagged: a never-heard leaf is one silent device; a never-heard
    repeater carrying 12 is 12 nodes pathing through something the hub has no evidence is alive.
    Classic mesh only — LR is a star with no repeaters.
  - lastTime/lastActivity is "when the hub last heard this device". For a device that only
    transmits after being commanded (a shade, an outlet), that is effectively "when a command
    last landed" — which is why staleness, not any radio metric, is what exposes a broken
    command path. Ranked here; the actuator-vs-reporter reading is the skill's job.
  - THE TIMESTAMP TZ TRAP: the legacy backend stamps lastTime with an explicit '+0000' (true
    UTC); the zwaveJS backend emits a NAIVE stamp in the hub's LOCAL zone. Reading a naive
    stamp as UTC makes every zwaveJS node read hours staler than it is (5h for America/Chicago).
    The hub's zone comes from /hub/details/json ('timeZone'). Verified live 2026-07-16: the
    newest zwaveJS lastTime tracked local wall-clock to within 7 seconds, not UTC.

Because Hubitat gives no numeric "bad" thresholds, this script HARD-flags only unambiguous,
grounded signals (FAILED nodes, nonzero PER, dead/incomplete Zigbee joins, an unhealthy
network) and otherwise RANKS nodes worst-first so the agent can judge severity against the
rule. The one signal-quality heuristic (RSSI at/below the radio's sensitivity floor) is
emitted with heuristic:true and a cited basis, never as a hard fact.

The deterministic pieces — parsing, ranking, flagging — are pure functions taking
already-parsed JSON and are unit-tested without a hub. Only fetch() touches the network.

Usage:
    hub_mesh.py --ip 192.0.2.10
    hub_mesh.py --hub main            # resolve via ./hubs.json (hub-config skill)
    hub_mesh.py --ip 192.0.2.10 --radio zwave    # one radio only
    hub_mesh.py --ip 192.0.2.10 --no-probe       # skip the peer reachability probe
Output: a single JSON object on stdout (see analyze()).
Exit is non-zero only when a REQUESTED RADIO endpoint fails — that is the asked-for capability.
The auxiliary endpoints (/hub2/hubMeshJson, /hub/details/json) are undocumented and
version-sensitive, so a hub lacking one still gets its radios analyzed: the affected key degrades
to null and the gap lands in `fetch_warnings` (and on stderr) naming what is now unknown. Never
silently — a null `hub_mesh` means a command-path fault could not be ruled out, so a clean radio
result alongside a fetch_warning is not an all-clear.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

sys.path.insert(0, str(Path(__file__).resolve().parent))
# E402: this import must follow the sys.path insert above so hubclient resolves when run as a script.
from hubclient import HubError, _urllib_transport, base_url, resolve_base_from_args  # noqa: E402

ZWAVE_PATH = "/hub/zwaveDetails/json"
ZIGBEE_PATH = "/hub/zigbeeDetails/json"
HUBMESH_PATH = "/hub2/hubMeshJson"
DETAILS_PATH = "/hub/details/json"

# Silicon Labs published receiver sensitivity floor, by Z-Wave series (dBm). Used only for
# the backend-aware RSSI heuristic on the zwaveJS (absolute-dBm) scale. Source: silabs.com.
# Note the modulation: -97 is 700-series 100 kbps GFSK; -110 is the 800-series LR channel
# (100 kbps O-QPSK). The classic GFSK floor on 800 is a few dB higher, so this is a
# conservative floor for a labeled heuristic, not an exact per-node threshold.
SILABS_SENSITIVITY_DBM = {"700": -97, "800": -110}
# A zwaveJS RSSI within this margin of the floor is flagged "near sensitivity floor". Margin,
# not an absolute cutoff — the floor itself is the grounded number; the margin is judgment.
RSSI_FLOOR_MARGIN_DB = 10
# The hub is always node 1 — the first hop of every route ('01 -> ...').
HUB_NODE_ID = 1


def parse_rssi(raw) -> Optional[float]:
    """'-78db' -> -78.0, '27dB' -> 27.0, '', 'Unknown', None -> None. Sign/scale is the
    caller's concern (backend-dependent); this only extracts the number."""
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s.endswith("db"):  # suffix strip without str.removesuffix (3.9+ floor; see README.md)
        s = s[:-2]
    try:
        return float(s.strip())
    except ValueError:
        return None


def parse_route(raw) -> Optional[list]:
    """'01 -> 07 -> 0A' -> [1, 7, 10]. Hops are HEX. None when absent or unparseable.

    The route is the full path the hub uses to reach the node: the hub first, the node itself
    last, and every repeater in between. The LR star's direct route is the two-hop case
    '01 -> <node>' (rules/zwave-zigbee-mesh.md), and a classic-mesh node reached directly has
    the same two-hop shape."""
    if not raw:
        return None
    hops = []
    for part in str(raw).split("->"):
        part = part.strip()
        if not part:
            return None
        try:
            hops.append(int(part, 16))
        except ValueError:
            return None
    return hops or None


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


def hub_timezone(details: Optional[dict]) -> Optional[str]:
    """Pull the hub's IANA zone name out of /hub/details/json, or None. The field is a bare
    string ('America/Chicago') on 2.5.1.128; tolerate a dict shape ({'ID': ...}) too."""
    tz = (details or {}).get("timeZone")
    if isinstance(tz, dict):
        tz = tz.get("ID") or tz.get("id")
    return tz if isinstance(tz, str) and tz else None


def naive_zone(tz_name: Optional[str]):
    """Resolve an IANA name to a tzinfo, or None if absent/unknown. None means naive stamps
    fall back to UTC — correct for the legacy backend, and the least-wrong default elsewhere."""
    if not tz_name:
        return None
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def parse_ts(raw, naive_tz=None) -> Optional[datetime]:
    """Parse a hub timestamp to an aware UTC datetime, or None. Handles '+0000' (no colon),
    trailing 'Z', and naive stamps.

    A NAIVE stamp is hub-LOCAL, not UTC: the legacy Z-Wave backend stamps lastTime '+0000'
    (true UTC), while the zwaveJS backend emits a naive stamp in the hub's own zone. Pass
    naive_tz (from hub_timezone()) to localize it. Without naive_tz a naive stamp is read as
    UTC, which makes a hub west of Greenwich look hours staler than it is — 5h for
    America/Chicago. Explicitly-offset stamps ignore naive_tz entirely."""
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
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=naive_tz or timezone.utc)
    return dt.astimezone(timezone.utc)


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
    coexist on one hub, so topology is per-node, not per-hub. 233..255 is a reserved gap the spec
    does not assign — classify it 'unknown', never 'mesh', so it gets no mesh-only advice."""
    try:
        n = int(node_id)
    except (TypeError, ValueError):
        return "unknown"
    if n >= 256:
        return "lr"
    if 1 <= n <= 232:
        return "mesh"
    return "unknown"


def normalize_zwave_node(node: dict, now: datetime, naive_tz=None) -> dict:
    """Project a raw hub node to the fields the analysis ranks/flags on. `now` and `naive_tz`
    are injected so age is deterministic under test (never call the clock in here)."""
    last = parse_ts(node.get("lastTime"), naive_tz)
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
        "lastTime": node.get("lastTime"),
        # "when the hub last heard this node" — for a command-only device (shade, outlet) that
        # is "when a command last landed". None = never heard since the stats last reset.
        "age_seconds": _age_seconds(last, now),
    }


def analyze_route_fan_in(nodes: list, backend: str, series: str = "800") -> dict:
    """Pure. Count how many classic-mesh nodes route THROUGH each repeater, and cross that count
    with the repeater's own health.

    Fan-in is a repeater's blast radius, exactly as shared_device_count is a hub-mesh peer's —
    and like that count it is CONTEXT, never a fault. A repeater carrying 12 nodes is a normal,
    healthy mesh; nothing here flags it, and nothing here reaches summary.critical/warnings (see
    analyze()). What the count changes is how a repeater that is ITSELF flagged reads: a
    never-heard leaf is one silent device, while a never-heard repeater carrying 12 is 12 nodes
    whose path runs through something the hub has no evidence is alive. Both were already in
    never_heard[] and nothing distinguished them.

    RANKS, NEVER THRESHOLDS. Hubitat publishes no "too many dependents" cutoff, so this invents
    none: every repeater is listed with its count, and the ones carrying a concern are listed
    again worst-first for the reader to judge (rules/zwave-zigbee-mesh.md).

    CLASSIC MESH ONLY. LR is a star — every LR node talks directly to the hub, so it depends on
    no repeater and can serve as none. Reading an LR node's route here would manufacture a
    repeater the topology forbids.
    """
    by_id = {n["nodeId"]: n for n in nodes}
    dependents: dict = {}
    anomalies = []

    for n in nodes:
        if n["topology"] != "mesh":
            continue  # LR star, or the reserved 233..255 gap: no routing to read
        hops = parse_route(n["route"])
        if hops is None:
            if n["route"]:
                # Present but unreadable. A shape this script does not know, not a mesh fault —
                # surfaced like unparsed_timestamps rather than silently counted as "direct".
                anomalies.append({"nodeId": n["nodeId"], "route": n["route"],
                                  "reason": "route is present but not parseable as hex hops"})
            continue
        if hops[0] != HUB_NODE_ID or hops[-1] != n["nodeId"]:
            # A coherent route starts at the hub and ends at the node it belongs to. Anything
            # else is a stale or malformed record, and guessing which hops are repeaters out of
            # it would invent dependents. Surface it and count nothing.
            anomalies.append({
                "nodeId": n["nodeId"], "route": n["route"],
                "reason": f"route does not run from the hub (node {HUB_NODE_ID}) to node "
                          f"{n['nodeId']} — parsed hops {hops}"})
            continue
        # Every intermediate hop must itself be a classic-mesh node. An LR id (>= 256) or one in
        # the reserved 233..255 gap cannot repeat for anyone — LR is a star, and a star node
        # relays nothing — so a mesh route naming one is incoherent, not a discovery. Validated
        # before any counting: a route rejected halfway would leave its earlier hops credited
        # with a dependent from a path this function just called impossible.
        intermediates = hops[1:-1]
        non_mesh = [h for h in intermediates if node_topology(h) != "mesh"]
        if non_mesh:
            anomalies.append({
                "nodeId": n["nodeId"], "route": n["route"],
                "reason": f"route names non-mesh hop(s) {non_mesh} as repeaters — a Long Range "
                          f"or reserved-range id repeats for nobody, so the route is incoherent"})
            continue
        for hop in intermediates:
            dependents.setdefault(hop, []).append(n["nodeId"])

    repeaters = []
    for node_id, deps in dependents.items():
        node = by_id.get(node_id)
        entry = {"nodeId": node_id, "dependent_count": len(deps), "dependents": sorted(deps)}
        concerns = []
        if node is None:
            # A route names a hop absent from nodes[]. Not a mesh fault — a data gap. The
            # dependents are real either way, so it is reported rather than dropped.
            concerns.append("unknown_node")
        else:
            entry.update({
                "deviceId": node["deviceId"], "deviceName": node["deviceName"],
                "nodeState": node["nodeState"], "lastTime": node["lastTime"],
                "age_seconds": node["age_seconds"], "per": node["per"],
                "rssi": node["rssi"], "rssi_raw": node["rssi_raw"],
            })
            # Each concern mirrors a flag the node already carries elsewhere in the output. The
            # fan-in count is what says how far that flag reaches.
            if not node["lastTime"]:
                concerns.append("never_heard")
            if str(node["nodeState"]).upper() == "FAILED":
                concerns.append("failed")
            if node["per"] and node["per"] > 0:
                concerns.append("packet_errors")
            if rssi_heuristic(node["rssi"], backend, series):
                concerns.append("weak_signal_heuristic")
        entry["concerns"] = concerns
        repeaters.append(entry)

    repeaters.sort(key=lambda r: (r["dependent_count"], r["nodeId"]), reverse=True)
    return {
        "repeater_count": len(repeaters),
        # Every repeater, worst-first by dependent count. Topology, not a fault list.
        "repeaters": repeaters,
        # The signal the issue was filed for: repeaters that are themselves flagged, ranked by
        # how many nodes sit behind them. Empty on a healthy mesh however high the fan-in.
        "load_bearing_concerns": [r for r in repeaters if r["concerns"]],
        "anomalies": anomalies,
    }


def analyze_zwave(details: dict, now: datetime, naive_tz=None) -> dict:
    """Pure. Flag failed/ghost nodes and nonzero PER; rank worst-first by PER, RTT, RSSI, and
    staleness. `now`/`naive_tz` are injected (see normalize_zwave_node)."""
    backend = zwave_backend(details)
    series = "700" if "700" in str(details.get("firmwareVersion", "")) else "800"
    nodes = [normalize_zwave_node(n, now, naive_tz) for n in details.get("nodes") or []]

    # Before the flag lists are built, so a node's blast radius rides along wherever it surfaces.
    # This is the point of the whole section: never_heard[] already listed the never-heard
    # repeater and the never-heard leaf identically, and the difference between them is 12 nodes.
    fan_in = analyze_route_fan_in(nodes, backend, series)
    fan_in_counts = {r["nodeId"]: r["dependent_count"] for r in fan_in["repeaters"]}
    for n in nodes:
        # None, not 0, off the classic mesh: an LR star node repeats for nobody by construction,
        # and "0 dependents" would read as a measurement where the question does not apply.
        n["dependent_count"] = fan_in_counts.get(n["nodeId"], 0) if n["topology"] == "mesh" else None

    failed, packet_errors, weak_signal = [], [], []
    for n in nodes:
        if str(n["nodeState"]).upper() == "FAILED":
            # A FAILED node with a bound deviceId is a REAL device that is currently unreachable
            # (may be transient — power-cycle/recover, do NOT delete). Only a FAILED node with no
            # deviceId is an orphan ghost (a pairing that never bound a device) — safe to remove.
            # Verified live: 8 FAILED nodes all had deviceIds, i.e. real dead/unreachable devices.
            n["failure_kind"] = "unreachable_device" if n["deviceId"] else "orphan_ghost"
            failed.append(n)
        if n["per"] and n["per"] > 0:
            packet_errors.append(n)
        h = rssi_heuristic(n["rssi"], backend, series)
        if h:
            weak_signal.append({**n, **h})

    def worst(key, reverse):
        have = [n for n in nodes if n[key] is not None]
        return sorted(have, key=lambda n: n[key], reverse=reverse)

    # lastTime absent entirely = the hub has never heard this node since its stats last reset.
    # Distinct from FAILED: such a node is reported nodeState:OK and passes every radio check.
    # Keyed on the RAW field, never on age_seconds being None: parse_ts also returns None for a
    # timestamp that is PRESENT but unparseable, and calling that "never heard" would invent a
    # diagnosis out of a malformed string. Those surface separately as a data anomaly.
    never_heard = [n for n in nodes if not n["lastTime"]]
    unparsed_timestamps = [n for n in nodes if n["lastTime"] and n["age_seconds"] is None]

    return {
        "backend": backend,
        "healthy": details.get("healthy"),
        "node_count": len(nodes),
        "failed": failed,                               # each tagged failure_kind (see below)
        "orphan_ghosts": [n for n in failed if n["failure_kind"] == "orphan_ghost"],
        "unreachable_devices": [n for n in failed if n["failure_kind"] == "unreachable_device"],
        "packet_errors": sorted(packet_errors, key=lambda n: n["per"], reverse=True),
        "weak_signal_heuristic": weak_signal,
        "never_heard": never_heard,
        # lastTime present but unparseable — a shape this script does not know, not a mesh
        # fault. Surfaced rather than silently folded into never_heard.
        "unparsed_timestamps": unparsed_timestamps,
        "ranked": {
            "by_per": [n for n in worst("per", True) if n["per"]][:10],
            "by_rtt_ms": worst("rtt_ms", True)[:10],
            # Higher RSSI is better on BOTH scales (absolute dBm and dB-above-noise), so
            # worst-first is always ascending. Backend only changes the floor heuristic, not this.
            "by_rssi": worst("rssi", False)[:10],
        },
        # The Z-Wave counterpart of zigbee.stalest. Staleness is not itself a fault — a
        # command-only device is silent until commanded — so this ranks, never flags. The
        # skill reads it against which devices self-report.
        "stalest": worst("age_seconds", True)[:10],
        # How many nodes depend on each repeater, crossed with that repeater's own health.
        # Ranked, never flagged, and deliberately absent from the summary counters — see
        # analyze_route_fan_in() and analyze().
        "route_fan_in": fan_in,
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


def normalize_peer(peer: dict) -> dict:
    """Project a raw hubMeshJson hubList entry to the fields the analysis judges on."""
    return {
        "name": peer.get("name") or "",
        "hubId": peer.get("hubId"),
        "ipAddress": peer.get("ipAddress"),
        "active": peer.get("active"),
        "offline": peer.get("offline"),
        "warning": peer.get("warning"),
        # Devices THIS hub shares with that peer. This is the blast radius of removing the
        # peer: each id is a link an app can be bound to. Re-adding a 3-device peer is cheap;
        # a 148-device peer unbinds every app using them. The skill quotes it before advising.
        "shared_device_count": len(peer.get("deviceIds") or []),
        "lastActive": peer.get("lastActive"),
    }


def analyze_hub_mesh(mesh: Optional[dict], probes: Optional[dict] = None) -> Optional[dict]:
    """Pure. Flag hub-mesh peers that cannot carry commands.

    `probes` maps ipAddress -> {"reachable": bool, "hubId": str|None} (built by probe_peer);
    None skips the address checks and judges on the hub's self-reported fields alone.

    Why the probe matters: in the grounded outage the hub's OWN fields were unanimous and
    wrong — the peer holding a dead IP read active:true, offline:false, warning:null while
    every command to it was dropped. Only asking who actually answers at that address finds
    it. hubUID in /hub/details/json is the same identifier as hubId here (verified across
    three hubs on 2.5.1.128), so a reachable address can also be checked for IDENTITY — an
    address reassigned to a different hub is as stale as one that answers nothing.
    """
    if mesh is None:
        return None
    peers = [normalize_peer(p) for p in mesh.get("hubList") or []]
    problems = []

    def flag(peer, signal, detail):
        problems.append({"peer": peer["name"], "ipAddress": peer["ipAddress"],
                         "signal": signal, "detail": detail,
                         "shared_device_count": peer["shared_device_count"]})

    for p in peers:
        probe = (probes or {}).get(p["ipAddress"] or "")
        if probe is not None:
            p["reachable"] = probe.get("reachable")
            p["probed_hubId"] = probe.get("hubId")
            p["probe_error"] = probe.get("error")
            if probe.get("reachable") is False:
                flag(p, "peer_unreachable",
                     f"no hub answers at {p['ipAddress']} — the record is stale, and commands "
                     f"to this peer are dropped silently")
            elif probe.get("hubId") and p["hubId"] and probe.get("hubId") != p["hubId"]:
                flag(p, "peer_identity_mismatch",
                     f"{p['ipAddress']} answers as hubId {probe['hubId']}, but the record "
                     f"claims {p['hubId']} — the address was reassigned")
        if p["offline"] is True:
            flag(p, "peer_offline", f"{p['name']} reports offline=true")
        if p["active"] is False:
            flag(p, "peer_inactive", f"{p['name']} reports active=false")
        if p["warning"]:
            flag(p, "peer_warning", f"{p['name']} carries warning: {p['warning']}")

    return {
        "peer_count": len(peers),
        "probed": probes is not None,
        "peers": peers,
        "problems": problems,                            # CRITICAL when non-empty
    }


def probe_peer(ip: str, port: int, transport=None) -> dict:
    """Network. Ask whoever answers at `ip` who they are, for analyze_hub_mesh()'s `probes`.
    Returns {"reachable": bool, "hubId": str|None, "error": str|None} — never raises: an
    unreachable peer is the finding, not an error.

    `reachable` means THE ADDRESS ANSWERS, never that it served usable identity. Reachability
    and identity are probed as separate questions: /hub/details/json is itself undocumented and
    version-sensitive, so a peer that responds without it is reachable-with-unknown-identity.
    Collapsing the two would roll a false peer_unreachable CRITICAL against a healthy peer on a
    firmware that lacks the endpoint. reachable:false is reserved for a connection-level failure
    — nothing answered at all. hubId None means identity was not verified, which
    analyze_hub_mesh treats as no evidence rather than as a mismatch."""
    transport = transport or _urllib_transport
    url = base_url(ip, port) + DETAILS_PATH
    try:
        status, _, text = transport("GET", url, None)
    except HubError as e:
        return {"reachable": False, "hubId": None, "error": str(e)}
    if status != 200:
        return {"reachable": True, "hubId": None,
                "error": f"{DETAILS_PATH} returned HTTP {status} — identity unverified"}
    try:
        hub_id = json.loads(text).get("hubUID")
    except json.JSONDecodeError:
        return {"reachable": True, "hubId": None,
                "error": f"{DETAILS_PATH} did not return JSON — identity unverified"}
    if not hub_id:
        # 200 + JSON, no hubUID: still unverified. Reporting hubId None with error None would
        # read as "identity checked and fine" — the one shape that must never look verified.
        return {"reachable": True, "hubId": None,
                "error": f"{DETAILS_PATH} carried no hubUID — identity unverified"}
    return {"reachable": True, "hubId": hub_id, "error": None}


def analyze(zwave: Optional[dict], zigbee: Optional[dict], now: datetime,
            hub_mesh: Optional[dict] = None, naive_tz=None) -> dict:
    """Combine the two radios plus hub mesh and roll up a summary count. Any may be None (not
    fetched / not enabled)."""
    out: dict = {"zwave": None, "zigbee": None, "hub_mesh": None}
    critical = warnings = 0
    if zwave is not None:
        out["zwave"] = analyze_zwave(zwave, now, naive_tz)
        critical += len(out["zwave"]["failed"])
        # Every non-critical Z-Wave flag has to reach the counters. weak_signal_heuristic and
        # never_heard were computed and then dropped here, so a hub with flagged weak routes
        # still rolled up warnings:0 — a second false all-clear, independent of the hub-mesh
        # one. A heuristic hint is still a warning; it carries heuristic:true for the reader.
        warnings += (len(out["zwave"]["packet_errors"])
                     + len(out["zwave"]["weak_signal_heuristic"])
                     + len(out["zwave"]["never_heard"])
                     + len(out["zwave"]["unparsed_timestamps"]))
        # route_fan_in deliberately reaches NEITHER counter. Fan-in is a topology fact: a
        # repeater carrying 12 nodes is a normal mesh, so counting repeaters would warn about
        # healthy hubs and train the reader to ignore the number. Every repeater fan-in marks as
        # a concern is ALREADY counted through the flag it mirrors — a never-heard repeater in
        # never_heard, a FAILED one in failed — so counting it here would double-count the same
        # node. Fan-in changes what those flags MEAN, not how many there are; that reading is the
        # skill's job (rules/zwave-zigbee-mesh.md), exactly as shared_device_count informs a
        # hub-mesh re-add without being a fault of its own.
    if zigbee is not None:
        out["zigbee"] = analyze_zigbee(zigbee, now)
        critical += len(out["zigbee"]["network_problems"])
        warnings += len(out["zigbee"]["dead_devices"])
    if hub_mesh is not None:
        out["hub_mesh"] = hub_mesh
        critical += len(hub_mesh["problems"])
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


def main(argv=None, transport=None) -> int:
    p = argparse.ArgumentParser(description="Flag Z-Wave/Zigbee mesh problems on a Hubitat hub.")
    p.add_argument("--ip")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--hub", help="named hub from hubs.json (when no --ip)")
    p.add_argument("--hubs", help="path to hubs.json (default ./hubs.json when --hub is given)")
    p.add_argument("--radio", choices=["zwave", "zigbee", "both"], default="both")
    p.add_argument("--no-probe", action="store_true",
                   help="skip the hub-mesh peer reachability/identity probe (one HTTP call per peer)")
    args = p.parse_args(argv)

    try:
        base = resolve_base_from_args(ip=args.ip, port=args.port, hub=args.hub, hubs_path=args.hubs)
    except HubError as e:
        print(str(e), file=sys.stderr)
        return 2

    zwave = zigbee = mesh_raw = None
    tz_name = None
    fetch_warnings: list = []

    # The radio endpoints are what the caller asked for: their failure is fatal.
    try:
        if args.radio in ("zwave", "both"):
            zwave = fetch(base, ZWAVE_PATH, transport)
        if args.radio in ("zigbee", "both"):
            zigbee = fetch(base, ZIGBEE_PATH, transport)
    except HubError as e:
        print(str(e), file=sys.stderr)
        return 1

    def optional_fetch(path: str, consequence: str):
        """Auxiliary endpoints degrade instead of sinking the radio diagnostics the caller
        asked for. Both are undocumented and version-sensitive (../_reference/endpoints.md), so a
        hub that lacks one must still get its radios analyzed — analyze() already models a
        missing hub_mesh, and naive_zone(None) already has a documented fallback.

        Degrades LOUDLY, never silently: the failure lands in the output as a structured
        fetch_warnings entry naming what is now unknown, and on stderr. Catches HubError
        specifically — an unexpected exception still propagates."""
        try:
            return fetch(base, path, transport)
        except HubError as e:
            fetch_warnings.append({"endpoint": path, "error": str(e), "consequence": consequence})
            print(f"warning: {path} unavailable — {e}. {consequence}", file=sys.stderr)
            return None

    # The hub's zone decides how a naive lastTime reads (see parse_ts). Fetched, never assumed
    # from the local machine — the analyzer may run in a different zone than the hub. Only the
    # Z-Wave path needs it: Zigbee's lastActivity carries an explicit offset, so fetching it for
    # --radio zigbee would risk a fetch_warning about Z-Wave ages this run never computes, and
    # the skill reads any fetch_warning as a blind axis blocking an all-clear.
    if args.radio in ("zwave", "both"):
        tz_name = hub_timezone(optional_fetch(
            DETAILS_PATH, "Naive zwaveJS lastTime stamps fall back to UTC, so on a hub outside "
                          "UTC every zwaveJS node age is overstated by the hub's offset."))
    mesh_raw = optional_fetch(
        HUBMESH_PATH, "hub_mesh is null: a peer that cannot carry commands would go undetected, "
                      "so a clean radio result here is not an all-clear.")

    probes = None
    if mesh_raw is not None and not args.no_probe:
        probes = {p["ipAddress"]: probe_peer(p["ipAddress"], args.port, transport)
                  for p in (mesh_raw.get("hubList") or []) if p.get("ipAddress")}

    result = analyze(zwave, zigbee, datetime.now(timezone.utc),
                     hub_mesh=analyze_hub_mesh(mesh_raw, probes), naive_tz=naive_zone(tz_name))
    result["hub"] = base
    result["hub_timezone"] = tz_name
    result["fetch_warnings"] = fetch_warnings
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
