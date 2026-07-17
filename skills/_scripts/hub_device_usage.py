#!/usr/bin/env python3
"""Report where a Hubitat device is used — the blast radius before removing it.

The hub computes a device's usage itself and exposes it on one undocumented endpoint
(verified live on 2.5.1.128, C-8 Pro, Hub Security off — see ../_reference/endpoints.md):

    GET /device/fullJson/<deviceId>  -> {device, appsUsing[], appsUsingCount,
                                         dashboards, parentApp, childDevices, hasChildren, ...}

`appsUsing[]` is the hub's own "in use by N apps" list — each entry
`{id, name, label, trueLabel, disabled}`. The `disabled` flag is the load-bearing vs inert
distinction the removal decision turns on: an enabled app is a live automation that breaks on
delete; a disabled one is inert. `dashboards` (a list), `parentApp` (the app that created the
device, or null), and `childDevices` (a dict of parentId -> [child device objects]) round out the
references a delete would strand.

This is the CAPTURE half of safe removal (rules/device-lifecycle.md): it enumerates the references
so an agent can warn before deleting and, on a replacement, re-wire them onto the new device id.
It does NOT delete anything and does NOT judge whether removal is safe — device deletion is a
hub-UI + physical action (rules/zwave-zigbee-mesh.md, ../_reference/playwright-ui.md), and the
load-bearing-vs-inert judgment is the skill's, not the script's.

Grounding notes:
  - appsUsingCount is a STRING on the wire ("2"); parsed to int here.
  - statusJson is deliberately NOT used for enumeration: /installedapp/statusJson/<id> reports
    device-input settings as None even when set (verify device inputs via
    /installedapp/configure/json/<id>/<page>). fullJson's appsUsing is the hub's computed list and
    does not have that blind spot.
  - The deterministic projection (analyze_usage) is a pure function of already-parsed JSON and is
    unit-tested without a hub. Only fetch() touches the network.

Usage:
    hub_device_usage.py --ip 192.0.2.11 --device 252
    hub_device_usage.py --ip 192.0.2.11 --name "Alice Office Closet Motion Sensor"
    hub_device_usage.py --hub devices --device 252   # resolve via ./hubs.json (hub-config skill)
Exactly one of --device / --name is required; --name resolves the id from /hub2/devicesList (exact
name match, fails clearly on zero or multiple matches). Output: a single JSON object on stdout (see
analyze_usage()); non-zero exit on a fetch failure.
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
DEVICES_LIST_PATH = "/hub2/devicesList"


def parse_count(raw) -> Optional[int]:
    """appsUsingCount arrives as a string ('2'); '', None -> None."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def normalize_app(app: dict) -> dict:
    """Project a raw appsUsing entry to the fields the removal decision reads. `disabled` is the
    load-bearing (enabled) vs inert (disabled) split the warning turns on."""
    return {
        "id": app.get("id"),
        "name": app.get("name"),
        "label": app.get("label") or app.get("trueLabel") or app.get("name"),
        "disabled": bool(app.get("disabled")),
    }


def normalize_children(child_devices) -> list:
    """childDevices is a dict {parentId: [child objects]}. Flatten to the child devices a delete
    of the parent would take down. A non-dict (or empty) yields []."""
    out = []
    if isinstance(child_devices, dict):
        for group in child_devices.values():
            for c in group or []:
                if isinstance(c, dict):
                    out.append({
                        "id": c.get("id"),
                        "displayName": c.get("displayName") or c.get("name"),
                        "disabled": bool(c.get("disabled")),
                    })
    return out


def analyze_usage(full: dict) -> dict:
    """Pure. Project /device/fullJson into the device's blast radius: apps split enabled/disabled,
    dashboards, parent app, child devices, and rollup counts."""
    device = full.get("device") or {}
    apps = [normalize_app(a) for a in full.get("appsUsing") or []]
    enabled = [a for a in apps if not a["disabled"]]
    disabled = [a for a in apps if a["disabled"]]
    dashboards = list(full.get("dashboards") or [])
    children = normalize_children(full.get("childDevices"))
    parent = full.get("parentApp")

    return {
        "device_name": device.get("displayName") or device.get("label") or device.get("name"),
        "driver": device.get("name"),
        "apps_using_count": parse_count(full.get("appsUsingCount")),
        "apps": {"enabled": enabled, "disabled": disabled},
        "dashboards": dashboards,
        "parent_app": parent,
        "child_devices": children,
        "blast_radius": {
            # Enabled apps are live automations a delete breaks; disabled ones are inert.
            "apps_enabled": len(enabled),
            "apps_disabled": len(disabled),
            "dashboards": len(dashboards),
            "child_devices": len(children),
            "has_parent_app": parent is not None,
        },
    }


def _walk_devices(entries) -> list:
    """Pure. Flatten a /hub2/devicesList `devices` forest to every entry, at any depth.

    The body is a TREE, not a flat list: a child device appears ONLY nested in its parent's
    `children[]`, never at the top level (`../_reference/parent-child-devices.md`, verified on
    2.5.1.128 — 151 top-level entries, 5 children reachable only by recursing). Iterating
    `devices[]` alone silently misses every child device.
    """
    out = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        out.append(entry)
        out.extend(_walk_devices(entry.get("children")))
    return out


def resolve_device_id(devices_list: dict, name: str) -> int:
    """Pure. Match a device by display name against a /hub2/devicesList body (entries are
    `{data: {id, name, ...}}`; `data.name` is the friendly name). Case-insensitive exact match —
    raises HubError on zero matches, or on more than one (listing the colliding ids) so the caller
    never silently picks the wrong device. Name-to-id resolution is deterministic hub polling, so it
    lives here, not in the skill (script-delegation).

    Searches the whole tree via _walk_devices, so a child device resolves by name like any other.
    """
    target = name.strip().lower()
    matches = []
    for entry in _walk_devices(devices_list.get("devices")):
        data = entry.get("data") or {}
        dev_name = data.get("name")
        if dev_name is not None and str(dev_name).strip().lower() == target and data.get("id") is not None:
            matches.append((int(data["id"]), str(dev_name)))
    if not matches:
        raise HubError(f"no device named {name!r} on this hub — check the exact display name "
                       f"(the hub's Devices page), or pass --device <id>.")
    if len(matches) > 1:
        listed = ", ".join(f"{n!r} (id {i})" for i, n in matches)
        raise HubError(f"{len(matches)} devices match name {name!r}: {listed}. Pass --device <id> "
                       f"to disambiguate.")
    return matches[0][0]


def fetch_devices(base: str, transport=None) -> dict:
    """GET /hub2/devicesList. Raises HubError on non-200 or non-JSON."""
    transport = transport or _urllib_transport
    url = base.rstrip("/") + DEVICES_LIST_PATH
    status, _, text = transport("GET", url, None)
    if status != 200:
        raise HubError(f"{url} returned HTTP {status} — check that Hub Security is off on this hub "
                       f"(an authed hub returns a redirect/401 to the login page).")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise HubError(f"{url} did not return JSON (got {text[:80]!r}). Check that Hub Security is "
                       f"off on this hub.") from e


def fetch(base: str, device_id: int, transport=None) -> dict:
    """GET /device/fullJson/<id>. Raises HubError on non-200 or non-JSON."""
    transport = transport or _urllib_transport
    url = base.rstrip("/") + DEVICE_PATH + str(device_id)
    status, _, text = transport("GET", url, None)
    if status != 200:
        raise HubError(f"{url} returned HTTP {status} — check that {device_id} is a valid device id "
                       f"and that Hub Security is off on this hub (an authed hub returns a "
                       f"redirect/401 to the login page).")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise HubError(
            f"{url} did not return JSON (got {text[:80]!r}). Check that Hub Security is off on "
            f"this hub and that {device_id} is a device id.") from e


def main(argv=None, transport=None) -> int:
    p = argparse.ArgumentParser(
        description="Report where a Hubitat device is used (blast radius) before removing it.")
    sel = p.add_mutually_exclusive_group(required=True)
    sel.add_argument("--device", type=int, help="device id to inspect")
    sel.add_argument("--name", help="device display name to resolve to an id (exact match)")
    p.add_argument("--ip")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--hub", help="named hub from hubs.json (when no --ip)")
    p.add_argument("--hubs", help="path to hubs.json (default ./hubs.json when --hub is given)")
    args = p.parse_args(argv)

    try:
        base = resolve_base_from_args(ip=args.ip, port=args.port, hub=args.hub, hubs_path=args.hubs)
    except HubError as e:
        print(str(e), file=sys.stderr)
        return 2

    try:
        device_id = args.device if args.device is not None else resolve_device_id(
            fetch_devices(base, transport), args.name)
        full = fetch(base, device_id, transport)
    except HubError as e:
        print(str(e), file=sys.stderr)
        return 1

    result = analyze_usage(full)
    result["hub"] = base
    result["device_id"] = device_id
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
