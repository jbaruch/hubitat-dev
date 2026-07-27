#!/usr/bin/env python3
"""Fire an ordered list of Hubitat devices with a timed hold on each — the "which physical thing
does this device control?" primitive.

Walk the property while zones (or lamps, shades, valves) activate in a known order, so every photo
or observation binds definitively to a device id instead of a guess. Generalizes past irrigation:
which lamp is `Kitchen 3`, which shade is `Office Left`, which valve is zone 7.

Per device, in order: announce the step, run a command (default `on`) over `/device/runmethod`, hold
for `--duration` seconds so the person in the field can observe, then optionally run an off command
(e.g. `off`) before the next device. Built on the device-command primitives — each command is
validated against that device's real surface and each argument coerced to its declared type
(see hub_device_command.py; ../_reference/endpoints.md).

The command is *dispatched*, not proven executed (rules/state-vs-attributes.md) — this script reports
per-step dispatch and leaves confirmation-by-observation to the operator in the field, which is the
whole point of the timed hold.

The deterministic pieces (id parsing, plan building, result aggregation) are pure and unit-tested;
the network (`transport`), the hold (`sleeper`), and the live narration (`announce`) are injected so
the sequence runs without a hub, a real clock, or real output in tests.

Usage:
    hub_device_sequence.py --ip 192.0.2.11 --devices 1639,1640,1641 --duration 120
    hub_device_sequence.py --ip 192.0.2.11 --devices 12,13 --command on --off-command off --duration 90
    hub_device_sequence.py --hub main --devices 1639,1640 --command push --arg 1 --duration 30
Output: one JSON object on stdout ({hub, command, steps:[...], summary}). Live per-step narration
goes to stderr. Exit 2 on a config/usage error, 1 when any step failed to dispatch, 0 otherwise.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# E402: imports must follow the sys.path insert so the sibling modules resolve when run as a script.
from hubclient import HubError, resolve_base_from_args  # noqa: E402
from hub_device_command import (  # noqa: E402
    coerce_args, commands_of, fetch_devices, fetch_full_json, resolve_device_id, run_method,
    validate_command,
)


def parse_device_ids(raw: str) -> list:
    """Pure. Parse a comma-separated device-id list ('1639,1640') into ordered ints. Raises HubError
    on an empty list or a non-integer token — a sequence with a bad id would fire the wrong device."""
    tokens = [t.strip() for t in (raw or "").split(",") if t.strip()]
    if not tokens:
        raise HubError("--devices is empty — give a comma-separated list of device ids in order")
    ids = []
    for token in tokens:
        try:
            ids.append(int(token))
        except ValueError as e:
            raise HubError(f"device id {token!r} in --devices is not an integer") from e
    return ids


def build_plan(device_ids: list, command: str, args: list, duration: float,
               off_command=None) -> list:
    """Pure. Build the ordered step plan. Each step names the device, the command and its args, the
    hold duration, and the optional off command run before the next device."""
    total = len(device_ids)
    return [
        {
            "index": i + 1,
            "total": total,
            "device_id": device_id,
            "command": command,
            "args": args,
            "duration": duration,
            "off_command": off_command,
        }
        for i, device_id in enumerate(device_ids)
    ]


def summarize(steps: list) -> dict:
    """Pure. Roll step results into counts. A step counts as failed when its command did not dispatch
    or its optional off command failed (off_dispatched is False)."""
    dispatched = sum(1 for s in steps if s.get("dispatched"))
    failed = [s for s in steps
              if s.get("error") or s.get("dispatched") is False or s.get("off_dispatched") is False]
    return {"total": len(steps), "dispatched": dispatched, "failed": len(failed)}


def _run_one(base: str, step: dict, transport, announce) -> dict:
    """Run a single device's command and (optionally) its off command. Returns the step result.
    A per-device failure is captured, never raised — one unreachable device must not abort the walk."""
    device_id = step["device_id"]
    result = dict(step)
    try:
        full = fetch_full_json(base, device_id, transport)
        commands = commands_of(full)
        device_name = (full.get("device") or {}).get("displayName") or str(device_id)
        result["device_name"] = device_name

        command = validate_command(commands, step["command"])
        coerced = coerce_args(command, step["args"])
        announce(f"[{step['index']}/{step['total']}] {device_name}: {step['command']} "
                 f"{coerced if coerced else ''}".rstrip())
        response = run_method(base, device_id, step["command"], coerced, transport)
        result["args"] = coerced
        result["dispatched"] = response["success"]
        if not response["success"]:
            result["error"] = f"runmethod returned success:false ({response.get('message')})"
    except HubError as e:
        result["dispatched"] = False
        result["error"] = str(e)
        announce(f"[{step['index']}/{step['total']}] device {device_id}: FAILED — {e}")
    return result


def _run_off(base: str, step: dict, result: dict, transport, announce) -> None:
    """Run the optional off command for a step after its hold. Best-effort; records off_error."""
    if not step.get("off_command"):
        return
    device_id = step["device_id"]
    try:
        full = fetch_full_json(base, device_id, transport)
        command = validate_command(commands_of(full), step["off_command"])
        response = run_method(base, device_id, step["off_command"], coerce_args(command, []), transport)
        result["off_dispatched"] = response["success"]
        if not response["success"]:
            result["off_error"] = (f"off '{step['off_command']}' returned success:false "
                                   f"({response.get('message')})")
            announce(f"[{step['index']}/{step['total']}] device {device_id}: off "
                     f"'{step['off_command']}' returned success:false")
    except HubError as e:
        result["off_dispatched"] = False
        result["off_error"] = str(e)
        announce(f"[{step['index']}/{step['total']}] device {device_id}: off '{step['off_command']}' "
                 f"FAILED — {e}")


def run_sequence(base: str, plan: list, transport=None, sleeper=None, announce=None) -> list:
    """Execute the plan in order. `transport` is the hub HTTP callable, `sleeper(seconds)` performs
    the hold (injected so tests do not wait), `announce(text)` narrates live (stderr by default).
    Returns the per-step results. A step that fails to dispatch is skipped without a hold — nothing
    fired, so there is nothing to observe."""
    sleeper = sleeper or time.sleep
    announce = announce or (lambda text: print(text, file=sys.stderr))
    results = []
    for step in plan:
        result = _run_one(base, step, transport, announce)
        if result.get("dispatched"):
            if step["duration"]:
                announce(f"    holding {step['duration']}s...")
                sleeper(step["duration"])
            _run_off(base, step, result, transport, announce)
        results.append(result)
    return results


def _resolve_ids(base, args, transport):
    """Resolve --devices ids, or --names (comma-separated display names) in order."""
    if args.devices:
        return parse_device_ids(args.devices)
    devices_list = fetch_devices(base, transport)
    names = [n.strip() for n in args.names.split(",") if n.strip()]
    if not names:
        raise HubError("--names is empty — give a comma-separated list of device names in order")
    return [resolve_device_id(devices_list, name) for name in names]


def main(argv=None, transport=None, sleeper=None) -> int:
    p = argparse.ArgumentParser(
        description="Fire an ordered list of Hubitat devices with a timed hold on each.")
    sel = p.add_mutually_exclusive_group(required=True)
    sel.add_argument("--devices", help="comma-separated device ids, in order (e.g. 1639,1640,1641)")
    sel.add_argument("--names", help="comma-separated device display names, in order")
    p.add_argument("--command", default="on", help="command to run on each device (default: on)")
    p.add_argument("--arg", action="append", default=[], dest="args",
                   help="positional argument for the command (repeatable)")
    p.add_argument("--off-command", dest="off_command",
                   help="command to run on each device after its hold (e.g. off)")
    p.add_argument("--duration", type=float, default=60.0,
                   help="seconds to hold each device before the next (default: 60)")
    p.add_argument("--ip")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--hub", help="named hub from hubs.json (when no --ip)")
    p.add_argument("--hubs", help="path to hubs.json (default ./hubs.json when --hub is given)")
    args = p.parse_args(argv)

    if args.duration < 0:
        print(f"--duration must be >= 0 seconds (got {args.duration}) — a negative hold is not a "
              f"valid sleep", file=sys.stderr)
        return 2

    try:
        base = resolve_base_from_args(ip=args.ip, port=args.port, hub=args.hub, hubs_path=args.hubs)
    except HubError as e:
        print(str(e), file=sys.stderr)
        return 2

    try:
        device_ids = _resolve_ids(base, args, transport)
    except HubError as e:
        print(str(e), file=sys.stderr)
        return 2

    plan = build_plan(device_ids, args.command, args.args, args.duration, args.off_command)
    steps = run_sequence(base, plan, transport, sleeper)
    summary = summarize(steps)
    print(json.dumps({"hub": base, "command": args.command, "steps": steps, "summary": summary},
                     indent=2, default=str))
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
