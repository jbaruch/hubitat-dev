#!/usr/bin/env python3
"""Regenerate this scenario's mesh-snapshot fixture.

Run me when `hub_mesh.analyze()`'s output shape changes (`plugin-evals` Fixture Hygiene).
Not part of the published plugin (`.tesslignore`) and not a `skills/_scripts/` module: this is
authoring tooling for one fixture, co-located with what it generates.

    python3 evals/mesh-health-command-path-not-radio/generate.py          # rewrite the fixture
    python3 evals/mesh-health-command-path-not-radio/generate.py --check  # verify, exit 1 on drift

WHY A GENERATOR AND NOT HAND-WRITTEN JSON

The fixture is synthetic RAW hub JSON pushed through the REAL `hub_mesh.analyze()` with an
injected clock. That is the whole point: the fixture is then a shape the shipped script
actually emits, and its `summary {critical:0, warnings:0}` is genuinely COMPUTED by the
analyzer rather than asserted by an author. A false all-clear is the entire premise of this
scenario, so a hand-asserted summary would beg the question it exists to pose.

Hand-patching the JSON forfeits that property. The previous generator lived in a scratch
directory and was lost; the fixture it left behind had drifted from the script in three ways
that only a real round-trip would have caught (see the CHANGELOG entry for PR #27):
  - `hub_mesh` carried a hand-written `peers[].probe.{reachable,identity_match}` that the real
    `analyze_hub_mesh()` never emits. `analyze()` takes hub_mesh already-analyzed and passes it
    through untouched, so nothing validated it.
  - `routeChanges` was `0` on a `zwaveJS` backend, where the hub reports `N/A`
    (`rules/zwave-zigbee-mesh.md` The backend split).
  - Every node carried the same `route` `'01 -> 0A'` regardless of its own id, which claims
    most nodes' paths terminate at a different node.

WHAT THE FIXTURE ENCODES (the scenario's ground truth — `criteria.json` grades against it)

Every axis is measured and every axis is green: no FAILED nodes, no packet errors, no
weak-signal flags, no never-heard nodes, empty `fetch_warnings`, and hub-mesh peers probing
reachable with matching identity. The ONLY evidence is the staleness pattern:

  - Every command-only device (shades, lamps, outlets, switches, dimmers, relay) froze inside a
    47-second window ending 2026-07-15 19:33:03 local -> age ~13.7 h.
  - Every self-reporting device (locks, motion, temperature) is fresh (~3-12 min), which is what
    proves the radio is alive.
  - The Zigbee outlet froze in that SAME window while the other Zigbee devices stayed fresh. A
    fault spanning both radios at once cannot be a radio fault.

TOPOLOGY IS DELIBERATELY DECORRELATED FROM THE FREEZE

Routes are coherent (a repeater actually relays for the nodes whose route names it), and every
repeater carries at least one FRESH device alongside frozen ones — see REPEATERS below. That is
load-bearing, not decoration: a repeater carrying only frozen nodes would hand the reader a
plausible radio explanation and undercut the scenario's premise. Fresh devices behind the same
repeater as frozen ones rule that repeater out on the evidence.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "_scripts"))
# E402: this import must follow the sys.path insert above so hub_mesh resolves from skills/_scripts/.
from hub_mesh import analyze, analyze_hub_mesh  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "inputs" / "mesh-snapshot-2026-07-16.json"

# The analyzer's injected clock. 09:14:30 America/Chicago, as task.md tells the agent.
# Fixed, never the wall clock: `testing-standards` Determinism, and the ages below are
# differences against it.
NOW_UTC = datetime(2026, 7, 16, 14, 14, 30, tzinfo=timezone.utc)
HUB_TZ = "America/Chicago"
HUB_BASE = "http://10.0.4.10:8080"

# Repeaters. Each relays for a mix of frozen and fresh nodes (see the module docstring).
#   node 07 Extender Hall    -> 10, 11, 12, 21, 22 frozen + 60 FRESH
#   node 08 Extender Cellar  -> 16, 17, 18 frozen    + 64 FRESH
#   node 09 Extender Gallery -> 25, 26, 27 frozen    + 66 FRESH
#
# Columns: node id, name, lastTime (naive hub-LOCAL — the zwaveJS backend stamps it that way,
# `rules/zwave-zigbee-mesh.md`), avg RTT ms, lwrRssi (absolute dBm on this backend), msgCount,
# neighbors, route. deviceId is 400 + node id.
#
# Ranking guard: the analyzer emits only the worst 10 by age / RTT / RSSI, so the extenders and
# the LR sensors stay out of every ranked list by being fresh, fast (RTT < 73) and strong
# (RSSI > -72 dBm). Weakening one would silently push a visible node out of a ranked list and
# invalidate criteria.json's ground truth.
ZWAVE_NODES = [
    # --- infrastructure: repeaters. Fresh; an extender is neither actuator nor reporter. ---
    (7, "Extender Hall", "2026-07-16 09:13:44", 30, -48, 902, 9, "01 -> 07"),
    (8, "Extender Cellar", "2026-07-16 09:13:38", 35, -52, 884, 8, "01 -> 08"),
    (9, "Extender Gallery", "2026-07-16 09:13:51", 32, -50, 913, 7, "01 -> 09"),
    # --- command-only (actuators): frozen in the 19:32:16 - 19:33:03 window ---
    (10, "Shade Study West", "2026-07-15 19:32:16", 45, -62, 1270, 5, "01 -> 07 -> 0A"),
    (11, "Shade Study East", "2026-07-15 19:32:21", 54, -64, 1277, 5, "01 -> 07 -> 0B"),
    (12, "Shade Landing", "2026-07-15 19:32:27", 63, -66, 1284, 4, "01 -> 07 -> 0C"),
    (13, "Shade Loft North", "2026-07-15 19:32:33", 72, -68, 1291, 4, "01 -> 0D"),
    (14, "Shade Loft South", "2026-07-15 19:32:39", 81, -70, 1298, 3, "01 -> 0E"),
    (15, "Shade Gallery", "2026-07-15 19:32:44", 90, -72, 1305, 3, "01 -> 0F"),
    (16, "Outlet Bench Lamp", "2026-07-15 19:32:48", 99, -74, 1312, 4, "01 -> 08 -> 10"),
    (17, "Outlet Server Nook", "2026-07-15 19:32:51", 45, -76, 1319, 4, "01 -> 08 -> 11"),
    (18, "Outlet Kiln", "2026-07-15 19:32:54", 54, -78, 1326, 3, "01 -> 08 -> 12"),
    (21, "Lamp Porch Left", "2026-07-15 19:33:02", 81, -66, 1347, 5, "01 -> 07 -> 15"),
    (22, "Lamp Porch Right", "2026-07-15 19:33:03", 90, -68, 1354, 5, "01 -> 07 -> 16"),
    (23, "Switch Pantry", "2026-07-15 19:32:31", 99, -70, 1361, 4, "01 -> 17"),
    (24, "Switch Mudroom", "2026-07-15 19:32:36", 45, -72, 1368, 4, "01 -> 18"),
    (25, "Switch Cellar Stair", "2026-07-15 19:32:41", 54, -74, 1375, 3, "01 -> 09 -> 19"),
    (26, "Dimmer Studio Track", "2026-07-15 19:32:46", 63, -76, 1382, 4, "01 -> 09 -> 1A"),
    (27, "Dimmer Alcove", "2026-07-15 19:32:53", 72, -78, 1389, 3, "01 -> 09 -> 1B"),
    (28, "Relay Garage Bay", "2026-07-15 19:32:59", 81, -62, 1396, 4, "01 -> 1C"),
    # --- self-reporting (reporters): fresh. Each of 60/64/66 sits behind a repeater that also
    # --- carries frozen nodes, which is what rules that repeater out as the cause.
    (60, "Lock Front Entry", "2026-07-16 09:11:16", 88, -71, 1620, 5, "01 -> 07 -> 3C"),
    (61, "Lock Side Entry", "2026-07-16 09:07:16", 95, -74, 1627, 4, "01 -> 3D"),
    (64, "Motion Studio", "2026-07-16 09:10:16", 73, -77, 1648, 4, "01 -> 08 -> 40"),
    (66, "Sensor Attic Temp", "2026-07-16 09:02:16", 67, -79, 1662, 3, "01 -> 09 -> 42"),
]

# Z-Wave Long Range: id >= 256, a star — no repeaters, no routing, neighbors 0
# (`rules/zwave-zigbee-mesh.md` Classic mesh vs Long Range). Present so the fixture proves
# route fan-in ignores LR rather than merely not encountering it. Fresh reporters.
ZWAVE_LR_NODES = [
    (300, "Sensor Barn Temp", "2026-07-16 09:12:02", 40, -60, 744, "01 -> 12C"),
    (301, "Sensor Well Level", "2026-07-16 09:09:31", 38, -58, 731, "01 -> 12D"),
]

ZIGBEE_DEVICES = [
    # The outlet is the cross-radio evidence: frozen inside the SAME window as the Z-Wave
    # actuators, while every other Zigbee device is fresh. lastActivity carries an explicit
    # +0000 on both backends, unlike the Z-Wave naive stamps above.
    ("0C41", "Plug Media Shelf", "Zigbee Outlet", "2026-07-16 00:32:29+0000", 4106),
    ("0E55", "Motion Sunroom", "Zigbee Motion", "2026-07-16 14:02:45+0000", 3390),
    ("0D77", "Button Kitchen", "Zigbee Button", "2026-07-16 14:07:59+0000", 812),
    ("0E02", "Contact Back Door", "Zigbee Contact", "2026-07-16 14:12:22+0000", 2551),
]

# Two peers, both healthy and both probing clean, so the hub-mesh link is ruled out on evidence
# rather than by omission. shared_device_count is derived from len(deviceIds) by the analyzer.
HUB_MESH_PEERS = [
    ("Hub Annex", "hub-b7", "10.0.4.11", 14, "2026-07-16 09:14:12"),
    ("Hub Workshop", "hub-c3", "10.0.4.12", 9, "2026-07-16 09:14:07"),
]


def zwave_raw() -> dict:
    """Synthetic /hub/zwaveDetails/json. Field names and shapes mirror the live 2.5.1.128
    C-8 Pro capture in skills/_reference/endpoints.md."""
    nodes = []
    for node_id, name, last, rtt, rssi, msgs, neighbors, route in ZWAVE_NODES:
        nodes.append({
            "nodeId": node_id,
            "deviceId": str(400 + node_id),
            "deviceName": name,
            "nodeState": "OK",
            "msgCount": msgs,
            "per": 0,
            "averageRtt": str(rtt),
            "lwrRssi": f"{rssi}db",
            "neighbors": neighbors,
            # The zwaveJS backend does not report route changes — the hub emits the literal
            # 'N/A' (rules/zwave-zigbee-mesh.md), which parse_num maps to None.
            "routeChanges": "N/A",
            "route": route,
            "security": "S2",
            "lastTime": last,
        })
    for node_id, name, last, rtt, rssi, msgs, route in ZWAVE_LR_NODES:
        nodes.append({
            "nodeId": node_id,
            "deviceId": str(400 + node_id),
            "deviceName": name,
            "nodeState": "OK",
            "msgCount": msgs,
            "per": 0,
            "averageRtt": str(rtt),
            "lwrRssi": f"{rssi}db",
            "neighbors": 0,          # inherent to a star, not a fault
            "routeChanges": "N/A",
            "route": route,
            "security": "S2",        # LR inclusion is S2-mandatory
            "lastTime": last,
        })
    return {
        "enabled": True,
        "healthy": True,
        "zwaveJS": True,             # -> backend 'zwavejs': absolute-dBm RSSI, naive lastTime
        "region": "US",
        "firmwareVersion": "1.8.0",  # no '700' -> the 800-series sensitivity floor
        "nodes": nodes,
    }


def zigbee_raw() -> dict:
    """Synthetic /hub/zigbeeDetails/json."""
    return {
        "enabled": True,
        "networkState": "ONLINE",
        "healthy": True,
        "channel": 20,
        "weakChannel": False,
        "powerLevel": 8,
        "devices": [
            {"id": dev_id, "name": name, "type": dev_type,
             "active": True, "messageCount": msgs, "lastActivity": last}
            for dev_id, name, dev_type, last, msgs in ZIGBEE_DEVICES
        ],
    }


def hub_mesh_raw() -> dict:
    """Synthetic /hub2/hubMeshJson."""
    return {
        "hubList": [
            {"name": name, "hubId": hub_id, "ipAddress": ip,
             "active": True, "offline": False, "warning": None,
             "deviceIds": [str(9000 + hub_index * 100 + i) for i in range(shared)],
             "lastActive": last_active}
            for hub_index, (name, hub_id, ip, shared, last_active) in enumerate(HUB_MESH_PEERS)
        ],
    }


def probes() -> dict:
    """What probe_peer() returns for each peer: every address answers, and each answers with the
    hubId the record claims. The shape is probe_peer()'s contract — see its docstring."""
    return {ip: {"reachable": True, "hubId": hub_id, "error": None}
            for _, hub_id, ip, _, _ in HUB_MESH_PEERS}


def build() -> dict:
    """Push the synthetic raw JSON through the real analyzer and assemble main()'s output
    shape. Nothing here asserts a finding — every counter is computed by analyze()."""
    result = analyze(
        zwave_raw(),
        zigbee_raw(),
        NOW_UTC,
        hub_mesh=analyze_hub_mesh(hub_mesh_raw(), probes()),
        naive_tz=ZoneInfo(HUB_TZ),
    )
    # main() stamps these three onto the analyzer's output before printing.
    result["hub"] = HUB_BASE
    result["hub_timezone"] = HUB_TZ
    result["fetch_warnings"] = []
    return result


def assert_ground_truth(result: dict) -> None:
    """Fail loudly if the generated fixture stops encoding the scenario criteria.json grades.

    These are checks on a COMPUTED result, not assertions substituting for one: the premise is a
    false all-clear, so a fixture that quietly grew a real fault would still 'look fine' while
    silently gutting the measurement. Each failure names what to do about it."""
    zwave, zigbee, mesh = result["zwave"], result["zigbee"], result["hub_mesh"]
    problems = []

    if result["summary"] != {"critical": 0, "warnings": 0}:
        problems.append(
            f"summary is {result['summary']}, must be {{critical:0, warnings:0}} — the false "
            f"all-clear IS the scenario. A node grew a real flag; check per/nodeState/RSSI above.")
    for bucket in ("failed", "packet_errors", "weak_signal_heuristic", "never_heard",
                   "unparsed_timestamps"):
        if zwave[bucket]:
            problems.append(f"zwave.{bucket} is non-empty ({len(zwave[bucket])}) — every axis "
                            f"must measure green so staleness is the only evidence.")
    if zwave["healthy"] is not True or zigbee["network_problems"] or zigbee["dead_devices"]:
        problems.append("zwave.healthy must be true and Zigbee must report no network problems "
                        "or dead devices.")
    if mesh["problems"]:
        problems.append(f"hub_mesh.problems is non-empty ({len(mesh['problems'])}) — the peer "
                        f"link must be ruled out on evidence (both peers probe clean).")
    if not mesh["probed"]:
        problems.append("hub_mesh.probed must be true — an unprobed peer table is not evidence.")

    # The staleness pattern criteria.json quotes verbatim.
    by_name = {n["deviceName"]: n for n in zwave["stalest"]}
    frozen = [n for n in by_name.values() if n["age_seconds"] > 49000]
    if len(frozen) != 10:
        problems.append(f"expected the 10 stalest Z-Wave nodes to be the frozen actuators, got "
                        f"{len(frozen)} above 49000s.")
    plug = next((d for d in zigbee["stalest"] if d["name"] == "Plug Media Shelf"), None)
    if plug is None or plug["age_seconds"] != 49321:
        problems.append("the Zigbee outlet 'Plug Media Shelf' must sit at age 49321 — it is the "
                        "cross-radio evidence criteria.json grades.")

    if problems:
        raise SystemExit("fixture ground truth broken:\n  - " + "\n  - ".join(problems))


def _regen_hint() -> Path:
    """The path to print in the drift message: repo-relative when the cwd is an ancestor,
    absolute otherwise. Never raises — this builds the text of an error message, and a
    message that dies computing itself takes the drift report down with it. `relative_to`
    raises ValueError whenever the cwd is not an ancestor (CI invoking the script by
    absolute path, a run from anywhere but the repo root), which is exactly the moment
    --check has something to say."""
    try:
        return Path(__file__).resolve().relative_to(Path.cwd().resolve())
    except ValueError:
        return Path(__file__).resolve()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate this scenario's mesh-snapshot fixture.")
    parser.add_argument("--check", action="store_true",
                        help="verify the committed fixture matches this generator; exit 1 on drift")
    args = parser.parse_args(argv)

    result = build()
    assert_ground_truth(result)
    rendered = json.dumps(result, indent=2, default=str) + "\n"

    if args.check:
        if not FIXTURE.exists():
            print(f"{FIXTURE} does not exist — run this script without --check to write it.",
                  file=sys.stderr)
            return 1
        if FIXTURE.read_text() != rendered:
            print(f"{FIXTURE} is stale — regenerate it:\n"
                  f"    python3 {_regen_hint()}", file=sys.stderr)
            return 1
        print(f"{FIXTURE.name} is up to date.")
        return 0

    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(rendered)
    print(f"wrote {FIXTURE} ({len(rendered)} bytes, "
          f"{result['zwave']['node_count']} Z-Wave nodes, "
          f"{result['zigbee']['device_count']} Zigbee devices)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
