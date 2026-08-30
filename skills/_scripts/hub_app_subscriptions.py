#!/usr/bin/env python3
"""Report an installed app's LIVE event subscriptions, and optionally assert that a device is
among them.

Grounded live on 2.5.1.169 (C-8 Pro, Hub Security off — see ../_reference/endpoints.md):

    GET /installedapp/statusJson/<appId>  -> {label, eventSubscriptions:[...], appSettings:[...], ...}

An installed app's configured-looking settings are not what it watches. A built-in app that
enumerates per-device subscriptions freezes them at its last **Done**, so a device added afterwards
is absent from `eventSubscriptions` and its handler never fires — while `appSettings` still reads
like blanket coverage. Measured on Hubitat Safety Monitor: `useAllWater = 'true'` and
`hasWater = "All leak and water detectors"` alongside 13 frozen per-device `water.wet`
subscriptions that did not include a newly added sensor (rules/app-lifecycle.md).

`eventSubscriptions[].typeId` is positive live evidence for a **subscription-driven** consumer. It
is NOT a complete account of what an app does: a command-only consumer legitimately holds no
subscription, and Rule Machine keeps a trigger in `state.trigDevs` while a false Required
Expression suspends the subscription (rules/device-lifecycle.md). Absence is conclusive only for an
app type known to consume by subscription — this script reports, and never infers inertness from a
missing row.

`--expect-device` asserts presence, which is the check to run after a Done: the device must be
there. It deliberately does NOT assert a count delta — one Done can legitimately add several
previously missing subscriptions, and one device can need several attribute subscriptions.

The deterministic pieces (projection, filtering, presence check) are pure functions unit-tested
without a hub; only the fetch touches the network via an injectable `transport`.

Usage:
    hub_app_subscriptions.py --ip 192.0.2.11 --app 61
    hub_app_subscriptions.py --ip 192.0.2.11 --app 61 --attribute water.wet
    hub_app_subscriptions.py --hub main --app 61 --attribute water.wet --expect-device 953
Output: one JSON object on stdout ({hub, app_id, app_label, count, subscriptions, device_ids,
by_attribute, expected}). Exit 2 on a config or argument error, 1 on a hub/fetch error or a
missing --expect-device, 0 otherwise.
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
# E402: this import must follow the sys.path insert above so hubclient resolves when run as a script.
from hubclient import HubError, _urllib_transport, resolve_base_from_args  # noqa: E402

STATUS_PATH = "/installedapp/statusJson/"


def subscriptions_of(status: dict) -> list:
    """Pure. The app's `eventSubscriptions[]` rows, each a dict."""
    rows = status.get("eventSubscriptions")
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def filter_by_attribute(subs: list, attribute: Optional[str]) -> list:
    """Pure. Rows whose `name` matches the attribute, or every row when attribute is None.

    The hub writes `name` as the subscribed event, which may be a bare attribute (`water`) or an
    attribute.value pair (`water.wet`). An exact match on either form is the caller's choice; a
    bare attribute also matches its dotted forms.
    """
    if not attribute:
        return list(subs)
    out = []
    for row in subs:
        name = str(row.get("name") or "")
        if name == attribute or name.startswith(attribute + "."):
            out.append(row)
    return out


def device_ids(subs: list) -> list:
    """Pure. The sorted distinct `typeId` values of DEVICE-type rows.

    Only `type == "DEVICE"` rows carry a device id; a LOCATION row's `typeId` is not a device.
    """
    ids = {r.get("typeId") for r in subs
           if str(r.get("type") or "").upper() == "DEVICE" and r.get("typeId") is not None}
    return sorted(ids, key=lambda v: (str(type(v)), v))


def check_expected(subs: list, expected: Optional[int]) -> Optional[dict]:
    """Pure. Whether `expected` appears among the DEVICE rows. None when nothing was asserted.

    Presence is the assertion. A count delta is not: one Done can add several previously missing
    subscriptions, and one device can need several attribute subscriptions.
    """
    if expected is None:
        return None
    present = expected in device_ids(subs)
    return {"device_id": expected, "present": present}


def group_by_attribute(subs: list) -> dict:
    """Pure. `{event name: [device ids]}` for the DEVICE rows, for reading coverage at a glance.

    Drops rows with no `typeId` and de-duplicates, matching `device_ids` — `null` is not a device
    id, and emitting one would put a value in the output that no caller can act on.
    """
    out: dict = {}
    for row in subs:
        if str(row.get("type") or "").upper() != "DEVICE":
            continue
        if row.get("typeId") is None:
            continue
        out.setdefault(str(row.get("name") or ""), set()).add(row.get("typeId"))
    return {k: sorted(v, key=lambda x: (str(type(x)), x)) for k, v in sorted(out.items())}


def fetch_status(base: str, app_id, transport) -> dict:
    """Read the installed app's status. Answers a bare `{}` for an id that does not exist."""
    status, _, body = transport("GET", f"{base}{STATUS_PATH}{app_id}", None)
    if status != 200:
        raise HubError(f"GET {STATUS_PATH}{app_id} returned HTTP {status} — confirm the app id and "
                       f"that Hub Security is off for this client")
    try:
        parsed = json.loads(body)
    except ValueError as e:
        raise HubError(
            f"GET {STATUS_PATH}{app_id} did not return JSON ({e}) — the hub serves an HTML login "
            f"page when Hub Security is on for this client. Confirm the app id, confirm Hub "
            f"Security is off, then re-verify the route against skills/_reference/endpoints.md") from e
    if not isinstance(parsed, dict):
        raise HubError(f"GET {STATUS_PATH}{app_id} returned {type(parsed).__name__}, not an object "
                       f"— re-verify the route against skills/_reference/endpoints.md")
    if not parsed:
        raise HubError(f"app {app_id} returned an empty object — the hub answers `{{}}` for an app "
                       f"id that does not exist. Confirm the id against GET /hub2/appsList, walking "
                       f"`children[]` recursively")
    return parsed


def main(argv=None, transport=None) -> int:
    parser = argparse.ArgumentParser(
        description="Report an installed Hubitat app's live event subscriptions.")
    parser.add_argument("--ip")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--hub")
    parser.add_argument("--hubs", help="path to hubs.json (default ./hubs.json when --hub is given)")
    parser.add_argument("--app", type=int, required=True)
    parser.add_argument("--attribute", help="filter to one event name, e.g. water.wet")
    parser.add_argument("--expect-device", type=int, dest="expect_device",
                        help="assert this device id is subscribed; exit 1 when it is not")
    args = parser.parse_args(argv)
    transport = transport or _urllib_transport

    try:
        base = resolve_base_from_args(args.ip, args.port, args.hub, args.hubs)
    except (HubError, OSError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 2

    try:
        status = fetch_status(base, args.app, transport)
        subs = filter_by_attribute(subscriptions_of(status), args.attribute)
        expected = check_expected(subs, args.expect_device)
    except HubError as e:
        print(str(e), file=sys.stderr)
        return 1

    result = {
        "hub": base,
        "app_id": args.app,
        "app_label": status.get("label") or status.get("name"),
        "attribute": args.attribute,
        "count": len(subs),
        "subscriptions": subs,
        "device_ids": device_ids(subs),
        "by_attribute": group_by_attribute(subs),
        "expected": expected,
    }
    print(json.dumps(result, indent=2, default=str))
    if expected and not expected["present"]:
        print(f"device {args.expect_device} is NOT subscribed on app {args.app}"
              + (f" for {args.attribute}" if args.attribute else "")
              + " — the app's config was not committed, or it was committed without this device. "
                "Re-open the app's config page and press Done (rules/ui-automation.md)",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
