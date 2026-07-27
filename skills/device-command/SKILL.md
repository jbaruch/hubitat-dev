---
name: device-command
description: Run a command on a Hubitat device over HTTP and confirm it landed — turn a switch or plug on/off, set a dimmer level, refresh a sensor, start an irrigation zone, or run a driver's custom command. Use when the user wants to command, control, operate, or exercise a device, test that a device responds, or run a device command and verify it took effect. Not for deleting a device (that is device-removal).
---

# Device-Command Skill

Process steps in order. Do not skip ahead.

Commanding a device is the "make the thing do the thing, then observe it" half of field diagnosis. The hub exposes each device's real command surface and runs a command over one undocumented endpoint; `hub_device_command.py` enumerates the surface, sends the command, and re-reads the result. This skill frames the command and interprets the outcome.

**A command's return code is not evidence it executed.** The hub answers `{"success":true}` when the Groovy method is *dispatched*, not when the device moved — a method that throws, or a command to the state the device is already in, returns the identical payload (`rules/state-vs-attributes.md`). Step 4 is what earns "it worked."

## Step 1 — Frame the command

Establish the hub (`--ip <addr>` or `--hub <name>`), the device (`--device <id>` or `--name "<exact display name>"`), and the command with any arguments. This runs over HTTP with Hub Security off (`skills/_reference/endpoints.md`). Commanding a **physical** device has real-world side effects — a valve, a lock, a garage door, lights. Confirm the device and command with the user before running one whose effect is physical and consequential. Proceed to Step 2.

## Step 2 — Enumerate the command surface

Read the device's real commands before sending one:

```
python3 .tessl/plugins/jbaruch/hubitat-dev/skills/_scripts/hub_device_command.py --ip <addr> --device <id> --list
```

Output is one JSON object listing each command's `name`, `parameters` (declared types, in order), `relatedAttribute`, and `capability` (false marks a driver custom command). Match the intended command and its argument types against this list — `setZoneWaterTime` takes a number, and a string fails oddly. The run in Step 3 rejects a name absent from this surface. Proceed to Step 3.

## Step 3 — Run the command

```
python3 .tessl/plugins/jbaruch/hubitat-dev/skills/_scripts/hub_device_command.py --ip <addr> --device <id> --command <name> [--arg <v> ...]
```

Argument contract, exit codes, and output shape: `skills/_scripts/hub_device_command.py` module docstring. The script validates the command against the surface, coerces each `--arg` to its declared type, POSTs `/device/runmethod`, then re-reads the command's `relatedAttribute` from `currentStates` and reports whether it moved. `--arg` is repeatable and positional. Proceed to Step 4.

## Step 4 — Interpret the result

Read the output object, not the exit code alone:

- `dispatched: true` means the hub ran the method, never that the device changed. `dispatched: false` is a real failure — the method threw or the endpoint refused (exit 1).
- `verification.changed: true` — the `relatedAttribute` moved; the command took effect.
- `verification.changed: false` — unchanged, and **not** a failure. Commanding a device to the state it is already in produces no event (the change filter, `rules/state-vs-attributes.md`). Re-run against a different target state, or confirm the command was issued via `GET /device/eventsJson/<id>` with `Skill(skill: "debug")`.
- `verification.changed: null` — the command declares no `relatedAttribute`, or the attribute is absent from the surface. Confirm through the event log rather than the command response.

Report what was commanded, whether it dispatched, and whether the attribute moved. To fire an ordered list of devices with a timed hold on each — mapping which physical thing each controls — use `Skill(skill: "device-sequence")`. Finish here.
