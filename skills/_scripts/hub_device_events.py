"""Census a device's retained event log per attribute, and name the channels that are silent.

Grounded live on 2.5.1.x (C-8 Pro, Hub Security off — see ../_reference/endpoints.md):

    GET /device/fullJson/<id>     -> {device:{currentStates:{...}}, ...}
    GET /device/eventsJson/<id>   -> [{date, name, value, type, isStateChange, ...}, ...]

**Per-attribute freshness is not a device-level property.** A multi-sensor can have one attribute
channel dead while every other channel reports normally, and that shape defeats both liveness
heuristics at once: a monotonic sibling attribute still advances and `lastActivityTime` stays
current (the device passes), while the frozen channel emits no events because an unchanged value
emits none (the value passes). Grounded 2026-07-27: a contact/temperature/battery multi-sensor
reported `contact = closed` with the window physically open, and `eventsJson` carried **zero**
contact events out of 33 retained — the radio, battery and temperature channels worked, the reed
switch reporting did not.

So the discriminator is whether an attribute appears **at all** in the retained window. This script
reports that: every attribute the device declares in `currentStates`, its event count, and the ones
at zero. `command-*` rows are events too (`type: "command"`) and are excluded from attribute
counts, since a command being issued is not the attribute moving.

**A zero count is a prompt to investigate, never proof on its own.** The retained window is bounded
by `maxEvents` per attribute (default 11 changes, `rules/data-collection.md`), so a genuinely
steady channel on a short window and a dead one look identical here. Widen `maxEvents` or compare
against the device's physical state before concluding (`rules/state-vs-attributes.md`).

The deterministic pieces (grouping, counting, silent-channel detection) are pure functions unit
tested without a hub; only the fetches touch the network via an injectable `transport`.

Usage:
    hub_device_events.py --ip 192.0.2.11 --device 119
    hub_device_events.py --hub main --device 119 --expect-attribute contact
Output: one JSON object on stdout ({hub, device_id, retained_events, by_attribute,
silent_attributes, expected}). Exit 2 on a config or argument error, 1 on a hub/fetch error or a
silent --expect-attribute, 0 otherwise.
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
# E402: this import must follow the sys.path insert above so hubclient resolves when run as a script.
from hubclient import HubError, _urllib_transport, resolve_base_from_args  # noqa: E402

DEVICE_PATH = "/device/fullJson/"
EVENTS_PATH = "/device/eventsJson/"


def declared_attributes(full: dict) -> list:
    """Pure. The attribute names the device currently publishes, from `device.currentStates`.

    The hub renders `currentStates` as a dict keyed by attribute and as a list of rows; both appear
    across builds and both are read here.
    """
    device = full.get("device")
    states = device.get("currentStates") if isinstance(device, dict) else None
    if isinstance(states, dict):
        return sorted(str(k) for k in states)
    if isinstance(states, list):
        return sorted({str(r.get("name")) for r in states
                       if isinstance(r, dict) and r.get("name") is not None})
    return []


def attribute_events(events: list) -> list:
    """Pure. The attribute-change rows, excluding `command-*` / `type: "command"` rows.

    A command being issued is visible in this log separately from the attribute moving; counting it
    as attribute traffic would make a dead channel look alive.
    """
    out = []
    for row in events:
        if not isinstance(row, dict):
            continue
        if str(row.get("type") or "").lower() == "command":
            continue
        name = str(row.get("name") or "")
        if not name or name.startswith("command-"):
            continue
        out.append(row)
    return out


def count_by_attribute(events: list) -> dict:
    """Pure. `{attribute: {count, newest, oldest}}` over the attribute rows.

    `newest`/`oldest` are the raw `date` strings as the hub rendered them; this makes no attempt to
    parse them, so no timezone assumption is baked in here.
    """
    out: dict = {}
    for row in attribute_events(events):
        name = str(row.get("name"))
        dates = [d for d in (row.get("date"),) if d]
        slot = out.setdefault(name, {"count": 0, "dates": []})
        slot["count"] += 1
        slot["dates"].extend(str(d) for d in dates)
    result = {}
    for name, slot in sorted(out.items()):
        dates = sorted(slot["dates"])
        result[name] = {"count": slot["count"],
                        "newest": dates[-1] if dates else None,
                        "oldest": dates[0] if dates else None}
    return result


def silent_attributes(declared: list, counts: dict) -> list:
    """Pure. Declared attributes with zero events in the retained window.

    This is the discriminator a dead channel fails and a healthy device passes: a device-level
    liveness signal cannot see it, because the siblings are still reporting.
    """
    return sorted(a for a in declared if counts.get(a, {}).get("count", 0) == 0)


def check_expected(silent: list, declared: list, expected: Optional[str]) -> Optional[dict]:
    """Pure. Whether the named attribute carried events. None when nothing was asserted."""
    if not expected:
        return None
    return {"attribute": expected,
            "declared": expected in declared,
            "silent": expected in silent}


def _get_json(base: str, path: str, device_id, transport, what: str):
    status, _, body = transport("GET", f"{base}{path}{device_id}", None)
    if status != 200:
        raise HubError(f"GET {path}{device_id} returned HTTP {status} — confirm the device id and "
                       f"that Hub Security is off for this client")
    try:
        return json.loads(body)
    except ValueError as e:
        raise HubError(
            f"GET {path}{device_id} did not return JSON ({e}) — the hub serves an HTML login page "
            f"when Hub Security is on for this client. Confirm the device id, confirm Hub Security "
            f"is off, then re-verify the route against skills/_reference/endpoints.md ({what})") from e


def fetch_full_json(base: str, device_id, transport) -> dict:
    full = _get_json(base, DEVICE_PATH, device_id, transport, "device record")
    if not isinstance(full, dict) or not isinstance(full.get("device"), dict):
        raise HubError(f"GET {DEVICE_PATH}{device_id} returned JSON without a `device` object — "
                       f"confirm the device id exists")
    return full


def fetch_events(base: str, device_id, transport) -> list:
    events = _get_json(base, EVENTS_PATH, device_id, transport, "event log")
    if not isinstance(events, list):
        raise HubError(f"GET {EVENTS_PATH}{device_id} returned {type(events).__name__}, not a list "
                       f"— re-verify the route against skills/_reference/endpoints.md")
    return events


def main(argv=None, transport=None) -> int:
    parser = argparse.ArgumentParser(
        description="Census a Hubitat device's retained events per attribute.")
    parser.add_argument("--ip")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--hub")
    parser.add_argument("--hubs", help="path to hubs.json (default ./hubs.json when --hub is given)")
    parser.add_argument("--device", type=int, required=True)
    parser.add_argument("--expect-attribute", dest="expect_attribute",
                        help="assert this attribute carried events; exit 1 when it is silent")
    args = parser.parse_args(argv)
    transport = transport or _urllib_transport

    try:
        base = resolve_base_from_args(args.ip, args.port, args.hub, args.hubs)
    except (HubError, OSError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 2

    try:
        declared = declared_attributes(fetch_full_json(base, args.device, transport))
        events = fetch_events(base, args.device, transport)
    except HubError as e:
        print(str(e), file=sys.stderr)
        return 1

    counts = count_by_attribute(events)
    silent = silent_attributes(declared, counts)
    expected = check_expected(silent, declared, args.expect_attribute)

    result = {
        "hub": base,
        "device_id": args.device,
        "declared_attributes": declared,
        "retained_events": len(attribute_events(events)),
        "by_attribute": counts,
        "silent_attributes": silent,
        "expected": expected,
    }
    print(json.dumps(result, indent=2, default=str))
    if expected and expected["silent"]:
        print(f"attribute '{args.expect_attribute}' carried zero events on device {args.device} "
              f"while {len(counts)} other channel(s) reported — a dead attribute channel looks "
              f"exactly like a steady one here, so compare the value against the device's physical "
              f"state before trusting it (rules/state-vs-attributes.md)", file=sys.stderr)
        return 1
    if expected and not expected["declared"]:
        print(f"attribute '{args.expect_attribute}' is not published by device {args.device} — "
              f"declared: {', '.join(declared) or '(none)'}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
