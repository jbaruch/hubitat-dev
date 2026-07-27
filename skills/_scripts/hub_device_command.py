#!/usr/bin/env python3
"""Run a command on a Hubitat device and confirm it landed — enumerate the real command
surface, execute over the undocumented runmethod endpoint, and verify by observation.

Grounded live on 2.5.1.134–135 (C-8 Pro, Hub Security off — see ../_reference/endpoints.md):

    GET  /device/fullJson/<id>   -> {device:{currentStates[...]}, commands:[...], ...}
    POST /device/runmethod       {"id":<id>,"method":"<cmd>","args":[...]}  -> {"success":<bool>,"message":<str|null>}

The command surface is `commands[]` from fullJson — each `{name, parameters:[{type, defaultValue}],
arguments, relatedAttribute, capability:<bool>}`. It is authoritative and includes driver custom
commands (`capability:false`) that inferring from the declared capability would miss. This script
validates the requested command against that list (rejecting an unknown name with the valid set) and
coerces each argument to its declared `parameters[].type` before sending — `setZoneWaterTime` takes a
number, and a string would fail oddly.

**A runmethod return code is not evidence the command executed.** The hub answers `{"success":true}`
when the Groovy method is *dispatched*, not when the device moved — a method that throws, or a command
to the state the device is already in, returns the identical payload. In the already-in-state case the
platform's change filter suppresses the event too (rules/state-vs-attributes.md), so "no event" does
not distinguish worked from failed. Verification here re-reads the command's `relatedAttribute` from
`currentStates` and reports whether its value changed — an unchanged attribute is reported as
`unchanged`, never as failure, because change-to-current-state is a legitimate no-op.

The deterministic pieces (command lookup, argument coercion, state diff, response parsing) are pure
functions unit-tested without a hub; only the fetch/run functions touch the network via an injectable
`transport`.

Usage:
    hub_device_command.py --ip 192.0.2.11 --device 1639 --command refresh
    hub_device_command.py --ip 192.0.2.11 --device 1639 --command setLevel --arg 40 --arg 5
    hub_device_command.py --ip 192.0.2.11 --name "Zone 8 Soil" --command on
    hub_device_command.py --ip 192.0.2.11 --device 1639 --list          # print the command surface, run nothing
    hub_device_command.py --hub main --device 1639 --command on          # resolve via ./hubs.json
Exactly one of --device / --name is required. --command is required unless --list. Output: one JSON
object on stdout ({hub, device_id, command, args, dispatched, response, verification}). Exit 2 on a
config error, 1 on a hub/fetch error or a dispatch that returned success:false, 0 otherwise.
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
RUNMETHOD_PATH = "/device/runmethod"

# fullJson parameter types map onto these JSON arg kinds. ENUM constraints render as string in the
# UI, so an ENUM value is sent as its literal string (rules/ui-automation.md, playwright-ui gotcha 34).
_NUMBER_TYPES = {"NUMBER", "INTEGER"}
_DECIMAL_TYPES = {"DECIMAL", "FLOAT", "NUMBER_DECIMAL"}


def commands_of(full: dict) -> list:
    """Pure. The device's command surface: fullJson `commands[]`, each a dict."""
    return [c for c in (full.get("commands") or []) if isinstance(c, dict)]


def command_names(commands: list) -> list:
    """Pure. The declared command names, in order."""
    return [str(c.get("name")) for c in commands if c.get("name") is not None]


def find_command(commands: list, name: str) -> Optional[dict]:
    """Pure. Exact-name match (command/method names are case-sensitive). None if absent."""
    for c in commands:
        if c.get("name") == name:
            return c
    return None


def validate_command(commands: list, name: str) -> dict:
    """Pure. Return the command entry, or raise HubError naming the valid commands. Rejecting an
    unknown name against the real surface beats sending a method the driver does not implement."""
    found = find_command(commands, name)
    if found is None:
        valid = ", ".join(command_names(commands)) or "(none declared)"
        raise HubError(f"device has no command {name!r}. Valid commands: {valid}")
    return found


def coerce_arg(value: str, param_type: Optional[str]):
    """Pure. Coerce one CLI string to its declared parameter type. NUMBER -> int (or float when the
    literal is fractional); DECIMAL -> float; everything else (STRING, ENUM, unknown) -> the literal
    string. Raises HubError on a numeric type that will not parse."""
    kind = (param_type or "").upper()
    if kind in _NUMBER_TYPES:
        try:
            return int(value)
        except ValueError:
            try:
                return float(value)
            except ValueError as e:
                raise HubError(f"argument {value!r} is not a number (parameter type {param_type})") from e
    if kind in _DECIMAL_TYPES:
        try:
            return float(value)
        except ValueError as e:
            raise HubError(f"argument {value!r} is not a decimal (parameter type {param_type})") from e
    return value


def parameter_types(command: dict) -> list:
    """Pure. The declared parameter types of a command, in order ([] when none)."""
    out = []
    for p in command.get("parameters") or []:
        if isinstance(p, dict):
            out.append(p.get("type"))
    return out


def coerce_args(command: dict, raw_args: list) -> list:
    """Pure. Map positional CLI args onto the command's parameter types and coerce each. More args
    than declared parameters is an error (the command does not take them); an arg past the declared
    list with no type would be an ambiguous send."""
    types = parameter_types(command)
    if len(raw_args) > len(types):
        name = command.get("name")
        raise HubError(
            f"command {name!r} takes {len(types)} argument(s), got {len(raw_args)}: {raw_args!r}")
    return [coerce_arg(value, types[i] if i < len(types) else None) for i, value in enumerate(raw_args)]


def current_states(full: dict) -> dict:
    """Pure. device.currentStates as {attribute_name: entry_dict}. {} when absent."""
    device = full.get("device") or {}
    states = device.get("currentStates")
    out = {}
    if isinstance(states, dict):
        for name, entry in states.items():
            if isinstance(entry, dict):
                out[str(name)] = entry
    elif isinstance(states, list):
        for entry in states:
            if isinstance(entry, dict) and entry.get("name") is not None:
                out[str(entry["name"])] = entry
    return out


def related_attribute(command: dict) -> Optional[str]:
    """Pure. The attribute a command is expected to move, per fullJson. None when unspecified."""
    attr = command.get("relatedAttribute")
    return str(attr) if attr else None


def _state_value(entry: dict):
    return entry.get("value") if isinstance(entry, dict) else None


def _state_date(entry: dict):
    return entry.get("date") if isinstance(entry, dict) else None


def verify_outcome(before: dict, after: dict, attr: Optional[str]) -> dict:
    """Pure. Compare an attribute's value across a before/after currentStates snapshot.

    Result `changed` is True only when the value moved. An unchanged value is reported with
    `note: "unchanged — command may have been a no-op (already in state), which the change filter
    hides"`, never as a failure. A command with no relatedAttribute, or an attribute absent from the
    surface, yields `changed: null` — the caller cannot confirm by observation and must fall back to
    the event log (rules/state-vs-attributes.md)."""
    if not attr:
        return {"attribute": None, "changed": None,
                "note": "command declares no relatedAttribute — confirm via /device/eventsJson"}
    if attr not in before and attr not in after:
        return {"attribute": attr, "changed": None,
                "note": f"attribute {attr!r} not in currentStates — confirm via /device/eventsJson"}
    before_val = _state_value(before.get(attr, {}))
    after_val = _state_value(after.get(attr, {}))
    changed = before_val != after_val
    result = {
        "attribute": attr,
        "before": before_val,
        "after": after_val,
        "before_date": _state_date(before.get(attr, {})),
        "after_date": _state_date(after.get(attr, {})),
        "changed": changed,
    }
    if not changed:
        result["note"] = ("unchanged — command may have been a no-op (already in state), which the "
                          "change filter hides; confirm intent via /device/eventsJson if needed")
    return result


def parse_runmethod_response(text: str) -> dict:
    """Pure. Parse the runmethod body `{"success":<bool>,"message":<str|null>}`. A non-JSON body
    (an authed hub's HTML, a changed endpoint) raises HubError."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        raise HubError(
            f"/device/runmethod did not return JSON (got {text[:80]!r}). Check that Hub Security is "
            f"off on this hub and that the endpoint is valid on its firmware.") from e
    if not isinstance(payload, dict):
        raise HubError(f"/device/runmethod returned {type(payload).__name__}, expected a JSON object")
    return {"success": bool(payload.get("success")), "message": payload.get("message")}


def fetch_full_json(base: str, device_id: int, transport=None) -> dict:
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
        raise HubError(f"{url} did not return JSON (got {text[:80]!r}). Check that Hub Security is "
                       f"off on this hub and that {device_id} is a device id.") from e


def fetch_devices(base: str, transport=None) -> dict:
    """GET /hub2/devicesList. Raises HubError on non-200 or non-JSON."""
    transport = transport or _urllib_transport
    url = base.rstrip("/") + DEVICES_LIST_PATH
    status, _, text = transport("GET", url, None)
    if status != 200:
        raise HubError(f"{url} returned HTTP {status} — check that Hub Security is off on this hub.")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise HubError(f"{url} did not return JSON (got {text[:80]!r}). Check that Hub Security is "
                       f"off on this hub.") from e


def _walk_devices(entries) -> list:
    """Pure. Flatten a /hub2/devicesList `devices` forest — a child appears only nested in its
    parent's `children[]` (../_reference/parent-child-devices.md)."""
    out = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        out.append(entry)
        out.extend(_walk_devices(entry.get("children")))
    return out


def resolve_device_id(devices_list: dict, name: str) -> int:
    """Pure. Case-insensitive exact match of a device display name against /hub2/devicesList. Raises
    HubError on zero or multiple matches so the caller never commands the wrong device."""
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
        raise HubError(f"{len(matches)} devices match name {name!r}: {listed}. Pass --device <id>.")
    return matches[0][0]


def run_method(base: str, device_id: int, method: str, args: list, transport=None) -> dict:
    """POST /device/runmethod as JSON. Returns the parsed {success, message}. Raises HubError on a
    non-200 or a non-JSON body."""
    transport = transport or _urllib_transport
    url = base.rstrip("/") + RUNMETHOD_PATH
    body = json.dumps({"id": device_id, "method": method, "args": args})
    status, _, text = transport("POST", url, body, "application/json")
    if status != 200:
        raise HubError(f"{url} returned HTTP {status} for {method!r} on device {device_id} — check "
                       f"that Hub Security is off on this hub.")
    return parse_runmethod_response(text)


def describe_surface(commands: list) -> list:
    """Pure. Project commands[] into a compact surface listing for --list."""
    out = []
    for c in commands:
        out.append({
            "name": c.get("name"),
            "parameters": parameter_types(c),
            "relatedAttribute": related_attribute(c),
            "capability": bool(c.get("capability")),
        })
    return out


def main(argv=None, transport=None) -> int:
    p = argparse.ArgumentParser(
        description="Run a command on a Hubitat device and confirm it landed by observation.")
    sel = p.add_mutually_exclusive_group(required=True)
    sel.add_argument("--device", type=int, help="device id to command")
    sel.add_argument("--name", help="device display name to resolve to an id (exact match)")
    p.add_argument("--command", help="command/method name (must exist in the device's command surface)")
    p.add_argument("--arg", action="append", default=[], dest="args",
                   help="positional command argument (repeatable, in order)")
    p.add_argument("--list", action="store_true", help="print the device's command surface and exit")
    p.add_argument("--no-verify", action="store_true",
                   help="skip the post-command relatedAttribute re-read")
    p.add_argument("--ip")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--hub", help="named hub from hubs.json (when no --ip)")
    p.add_argument("--hubs", help="path to hubs.json (default ./hubs.json when --hub is given)")
    args = p.parse_args(argv)

    if not args.list and not args.command:
        print("--command is required unless --list is given", file=sys.stderr)
        return 2

    try:
        base = resolve_base_from_args(ip=args.ip, port=args.port, hub=args.hub, hubs_path=args.hubs)
    except HubError as e:
        print(str(e), file=sys.stderr)
        return 2

    try:
        device_id = args.device if args.device is not None else resolve_device_id(
            fetch_devices(base, transport), args.name)
        full = fetch_full_json(base, device_id, transport)
        commands = commands_of(full)

        if args.list:
            print(json.dumps({"hub": base, "device_id": device_id,
                              "commands": describe_surface(commands)}, indent=2, default=str))
            return 0

        command = validate_command(commands, args.command)
        coerced = coerce_args(command, args.args)
        attr = related_attribute(command)
        verify = not args.no_verify
        before = current_states(full) if verify else {}

        response = run_method(base, device_id, args.command, coerced, transport)

        verification = None
        if verify:
            after_full = fetch_full_json(base, device_id, transport)
            verification = verify_outcome(before, current_states(after_full), attr)
    except HubError as e:
        print(str(e), file=sys.stderr)
        return 1

    result = {
        "hub": base,
        "device_id": device_id,
        "command": args.command,
        "args": coerced,
        "dispatched": response["success"],
        "response": response,
        "verification": verification,
    }
    print(json.dumps(result, indent=2, default=str))
    # success:false means the hub did not dispatch the method — a real failure, not a quiet no-op.
    return 0 if response["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
