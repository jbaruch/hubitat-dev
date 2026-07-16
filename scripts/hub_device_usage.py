#!/usr/bin/env python3
"""Report where a Hubitat device is used — the blast radius before removing it.

The hub computes a device's usage itself and exposes it on one undocumented endpoint
(verified live on 2.5.1.128, C-8 Pro, Hub Security off — see reference/endpoints.md):

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
hub-UI + physical action (rules/zwave-zigbee-mesh.md, reference/playwright-ui.md), and the
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
    hub_device_usage.py --ip 192.168.30.17 --device 252
    hub_device_usage.py --hub devices --device 252   # resolve via ./hubs.json (hub-config skill)
Output: a single JSON object on stdout (see analyze_usage()); non-zero exit on a fetch failure.
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


def fetch(base: str, device_id: int, transport=None) -> dict:
    """GET /device/fullJson/<id>. Raises HubError on non-200 or non-JSON (e.g. Hub Security on,
    or an id that is not a device)."""
    transport = transport or _urllib_transport
    url = base.rstrip("/") + DEVICE_PATH + str(device_id)
    status, _, text = transport("GET", url, None)
    if status != 200:
        raise HubError(f"{url} returned HTTP {status} — is {device_id} a valid device id on this hub?")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise HubError(
            f"{url} did not return JSON (got {text[:80]!r}). Check that Hub Security is off on "
            f"this hub and that {device_id} is a device id.") from e


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Report where a Hubitat device is used (blast radius) before removing it.")
    p.add_argument("--device", type=int, required=True, help="device id to inspect")
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
        full = fetch(base, args.device)
    except HubError as e:
        print(str(e), file=sys.stderr)
        return 1

    result = analyze_usage(full)
    result["hub"] = base
    result["device_id"] = args.device
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
