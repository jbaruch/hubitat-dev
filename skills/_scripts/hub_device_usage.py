#!/usr/bin/env python3
"""Report a Hubitat device's reference blast radius and, optionally, its live consumers.

The hub computes a device's usage itself and exposes it on one undocumented endpoint
(verified live through 2.5.1.133, C-8 Pro, Hub Security off — see
../_reference/endpoints.md):

    GET /device/fullJson/<deviceId>  -> {device, appsUsing[], appsUsingCount,
                                         dashboards, parentApp, childDevices, hasChildren, ...}

`appsUsing[]` is the hub's own "in use by N apps" list — each entry
`{id, name, label, trueLabel, disabled}`. The `disabled` flag says only whether the app itself is
switched off. It does NOT prove that an enabled app actively consumes the device: Rule Machine can
retain withdrawn `tDev-N` / `trigDevsW` bookkeeping that still appears in `appsUsing`. Every entry
still belongs in the removal blast radius because deletion strands both live and inert references.
`dashboards` (a list), `parentApp` (the app that created the device, or null), and `childDevices` (a
dict of parentId -> [child device objects]) round out the references a delete would strand.

This is the CAPTURE half of safe removal (rules/device-lifecycle.md): it enumerates the references
so an agent can warn before deleting and, on a replacement, re-wire them onto the new device id.
It does NOT delete anything and does NOT judge whether removal is safe — device deletion is a
hub-UI + physical action (rules/zwave-zigbee-mesh.md, ../_reference/playwright-ui.md).

`--live` adds a second, deliberately separate audit over
`GET /installedapp/statusJson/<appId>`:
  - Rule Machine trigger liveness uses `state.trigDevs` / `appState.trigDevs`, which remains
    authoritative while a Required Expression is false. `eventSubscriptions` can disappear in that
    state.
  - Absence from `trigDevs` is negative only when `tDev*` settings or `trigDevsW` show that the
    device held a trigger role. Rule Machine action and condition references otherwise stay unknown.
  - A matching `eventSubscriptions[].typeId` is positive evidence that another app is subscribed.
  - `appSettings[]` identifies which configured setting contains the device even though the legacy
    top-level `settings` field is null.
  - No matching subscription is NOT proof that an arbitrary app is inert; command-only consumers do
    not subscribe. Such entries stay `unknown`, never false-flagged as not live.

The exact Rule Machine predicate and three-state result (`live`, `not_live`, `unknown`) live in
`analyze_liveness()`. The mode flags enabled Rule Machine trigger references that are absent from
`trigDevs`; this catches stale withdrawn trigger bookkeeping without false-flagging a valid trigger
whose Required Expression currently suppresses its subscription or an action-only consumer that
never belongs in the trigger map.

Grounding notes:
  - appsUsingCount is a STRING on the wire ("2"); parsed to int here.
  - statusJson is NOT the blast-radius enumerator. Its top-level `settings` field is null, while
    `appSettings[]` carries resolved `deviceIdsForDeviceList` / `deviceList` plus setting names.
    fullJson's appsUsing remains the complete hub-computed reference list.
  - The deterministic projections (`analyze_usage`, `analyze_liveness`) are pure functions of
    already-parsed JSON and are unit-tested without a hub. Only fetch functions touch the network.

Usage:
    hub_device_usage.py --ip 192.0.2.11 --device 252
    hub_device_usage.py --ip 192.0.2.11 --device 252 --live
    hub_device_usage.py --ip 192.0.2.11 --name "Alice Office Closet Motion Sensor"
    hub_device_usage.py --hub devices --device 252   # resolve via ./hubs.json (hub-config skill)
Exactly one of --device / --name is required; --name resolves the id from /hub2/devicesList (exact
name match, fails clearly on zero or multiple matches). Output: a single JSON object on stdout (see
analyze_usage()); `--live` adds `live_audit` (see analyze_liveness()). Non-zero exit on a fetch
failure.
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
STATUS_PATH = "/installedapp/statusJson/"

LIVE = "live"
NOT_LIVE = "not_live"
UNKNOWN = "unknown"


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
    """Project a raw appsUsing entry to the fields the blast-radius report reads.

    `disabled` is app switch state only. It is not a liveness verdict.
    """
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
    """Pure. Project /device/fullJson into the device's reference blast radius.

    Apps remain split by enabled/disabled switch state. Both groups are references a delete can
    strand; neither group is a live-consumer verdict.
    """
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
            # App switch state only. Both groups are part of the reference blast radius.
            "apps_enabled": len(enabled),
            "apps_disabled": len(disabled),
            "dashboards": len(dashboards),
            "child_devices": len(children),
            "has_parent_app": parent is not None,
        },
    }


def _device_id(raw) -> Optional[int]:
    """Normalize an id or an RM state key (`1612:Motion`) to a device id."""
    if raw is None:
        return None
    token = str(raw).strip().split(":", 1)[0]
    try:
        return int(token)
    except ValueError:
        return None


def _setting_device_ids(setting: dict) -> set:
    """Return device ids carried by one statusJson appSettings[] entry."""
    values = setting.get("deviceIdsForDeviceList")
    if isinstance(values, (list, tuple, set)):
        raw_ids = list(values)
    elif values is None:
        raw_ids = []
    else:
        raw_ids = [values]

    device_list = setting.get("deviceList")
    if isinstance(device_list, dict):
        raw_ids.extend(device_list.keys())

    return {device_id for raw in raw_ids if (device_id := _device_id(raw)) is not None}


def _configured_settings(status: dict, device_id: int) -> list:
    """Names of appSettings[] entries whose resolved device list contains device_id."""
    names = []
    for setting in status.get("appSettings") or []:
        if not isinstance(setting, dict) or device_id not in _setting_device_ids(setting):
            continue
        name = setting.get("name")
        names.append(str(name) if name is not None else "<unnamed>")
    return sorted(set(names))


def _state_value(status: dict, name: str):
    """Read a named state value from the statusJson shapes observed across built-in apps.

    Returns (found, value). A present empty dict is distinct from an absent state surface.
    """
    for root_name in ("appState", "state"):
        root = status.get(root_name)
        if isinstance(root, dict) and name in root:
            value = root[name]
            if isinstance(value, dict) and set(value) == {"value"}:
                value = value["value"]
            return True, value
        if isinstance(root, list):
            for entry in root:
                if not isinstance(entry, dict):
                    continue
                if entry.get("name") == name or entry.get("key") == name:
                    return True, entry.get("value")
    return False, None


def _state_device_ids(value) -> set:
    """Return device ids from an RM trigDevs/trigDevsW mapping or JSON string."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return set()
    if not isinstance(value, dict):
        return set()
    return {device_id for raw in value if (device_id := _device_id(raw)) is not None}


def _matching_subscription_count(status: dict, device_id: int) -> int:
    """Count eventSubscriptions[] rows whose typeId is the device."""
    subscriptions = status.get("eventSubscriptions") or []
    if isinstance(subscriptions, dict):
        subscriptions = subscriptions.values()
    return sum(
        1 for subscription in subscriptions
        if isinstance(subscription, dict) and _device_id(subscription.get("typeId")) == device_id
    )


def _status_for_app(statuses: dict, app_id):
    if app_id in statuses:
        return statuses[app_id]
    text_id = str(app_id)
    if text_id in statuses:
        return statuses[text_id]
    return {}


def analyze_liveness(device_id: int, apps: list, statuses: dict) -> dict:
    """Pure. Classify each appsUsing reference as live, not_live, or unknown.

    Decision contract:
      - disabled app -> not_live (the app switch is off)
      - Rule Machine trigger present in trigDevs -> live
      - matching eventSubscriptions[].typeId -> live
      - Rule Machine reference absent from trigDevs -> not_live only when tDev* or trigDevsW proves
        that the reference held a trigger role
      - every other enabled reference -> unknown

    Only enabled Rule Machine trigger references with authoritative negative evidence are flagged in
    enabled_configured_not_live. A missing subscription or trigger-map entry cannot prove an
    action/condition consumer is inert.
    """
    audited = []
    for app in apps:
        entry = dict(app)
        entry.update({
            "status": UNKNOWN,
            "method": "no_authoritative_liveness_surface",
            "configured_settings": [],
            "matching_subscription_count": 0,
            "trigger_in_trig_devs": None,
            "trigger_in_trig_devs_w": None,
            "rule_machine_trigger_settings": [],
            "configured_not_live": False,
        })

        if app["disabled"]:
            entry.update({
                "status": NOT_LIVE,
                "method": "app_disabled",
                "configured_not_live": True,
            })
            audited.append(entry)
            continue

        status = _status_for_app(statuses, app.get("id"))
        entry["configured_settings"] = _configured_settings(status, device_id)
        entry["matching_subscription_count"] = _matching_subscription_count(status, device_id)
        entry["rule_machine_trigger_settings"] = [
            setting for setting in entry["configured_settings"] if setting.startswith("tDev")
        ]

        trig_found, trig_devs = _state_value(status, "trigDevs")
        withdrawn_found, withdrawn = _state_value(status, "trigDevsW")
        name = " ".join(str(app.get(field) or "") for field in ("name", "label")).lower()
        is_rule_machine = trig_found or withdrawn_found or "rule machine" in name

        if trig_found:
            in_triggers = device_id in _state_device_ids(trig_devs)
            in_withdrawn = (
                device_id in _state_device_ids(withdrawn) if withdrawn_found else False
            )
            entry.update({
                "trigger_in_trig_devs": in_triggers,
                "trigger_in_trig_devs_w": in_withdrawn,
            })

            if in_triggers:
                entry.update({
                    "status": LIVE,
                    "method": "rule_machine_trig_devs",
                })
            elif entry["matching_subscription_count"]:
                entry.update({
                    "status": LIVE,
                    "method": "event_subscription",
                })
            elif in_withdrawn or entry["rule_machine_trigger_settings"]:
                entry.update({
                    "status": NOT_LIVE,
                    "method": "rule_machine_trig_devs",
                    "configured_not_live": True,
                })
            else:
                entry["method"] = "rule_machine_non_trigger_reference"
        elif entry["matching_subscription_count"]:
            entry.update({
                "status": LIVE,
                "method": "event_subscription",
            })
        elif is_rule_machine:
            entry["method"] = "rule_machine_state_unavailable"

        audited.append(entry)

    counts = {state: sum(a["status"] == state for a in audited)
              for state in (LIVE, NOT_LIVE, UNKNOWN)}
    enabled_configured_not_live = [
        a for a in audited if not a["disabled"] and a["configured_not_live"]
    ]
    return {
        "apps": audited,
        "summary": {
            **counts,
            "enabled_configured_not_live": len(enabled_configured_not_live),
        },
        "enabled_configured_not_live": enabled_configured_not_live,
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


def fetch_app_status(base: str, app_id: int, transport=None) -> dict:
    """GET /installedapp/statusJson/<id>. Raises HubError on non-200 or non-JSON."""
    transport = transport or _urllib_transport
    url = base.rstrip("/") + STATUS_PATH + str(app_id)
    status, _, text = transport("GET", url, None)
    if status != 200:
        raise HubError(
            f"{url} returned HTTP {status} — live-consumer audit is incomplete. Check that app "
            f"{app_id} still exists and that Hub Security is off.")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        raise HubError(
            f"{url} did not return JSON (got {text[:80]!r}) — live-consumer audit is incomplete. "
            f"Check that Hub Security is off.") from e
    if not isinstance(payload, dict):
        raise HubError(
            f"{url} returned {type(payload).__name__}, expected a JSON object — live-consumer "
            f"audit is incomplete.")
    return payload


def fetch_app_statuses(base: str, apps: list, transport=None) -> dict:
    """Fetch statusJson for enabled apps. Disabled apps need no live-surface request."""
    statuses = {}
    for app in apps:
        if app["disabled"]:
            continue
        app_id = app.get("id")
        if _device_id(app_id) is None:
            raise HubError(
                f"appsUsing entry {app.get('label')!r} has no numeric app id — live-consumer "
                f"audit cannot inspect it.")
        statuses[app_id] = fetch_app_status(base, int(app_id), transport)
    return statuses


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
    p.add_argument(
        "--live", action="store_true",
        help="also audit authoritative live-consumer surfaces (three-state: live/not_live/unknown)")
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
        apps = [normalize_app(app) for app in full.get("appsUsing") or []]
        statuses = fetch_app_statuses(base, apps, transport) if args.live else {}
    except HubError as e:
        print(str(e), file=sys.stderr)
        return 1

    result = analyze_usage(full)
    if args.live:
        result["live_audit"] = analyze_liveness(device_id, apps, statuses)
    result["hub"] = base
    result["device_id"] = device_id
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
