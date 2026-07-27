---
name: device-sequence
description: Fire an ordered list of Hubitat devices with a timed hold on each, so you can walk the property and bind each observation to a device id — which lamp is `Kitchen 3`, which shade is `Office Left`, which valve is irrigation zone 7. Use when the user wants to map which physical thing a device controls, identify which zone/lamp/shade/valve is which, run a set of devices one at a time in sequence, or fire devices on a timer for field verification.
---

# Device-Sequence Skill

Process steps in order. Do not skip ahead.

Answers "which physical thing does this device control?" by activating devices in a known order with a hold on each, so the person in the field binds every photo or observation to a device id instead of guessing. It builds on `device-command` — each command is validated against that device's real surface and dispatched over `/device/runmethod` — and adds the ordered walk, the timed hold, and live per-step narration.

**A command is dispatched, not proven executed** (`rules/state-vs-attributes.md`). The confirmation here is the operator watching the physical device during its hold — that is the whole point of the timed walk, not a return code.

## Step 1 — Frame the walk

Establish the hub (`--ip <addr>` or `--hub <name>`), the **ordered** device list (`--devices 1639,1640,1641` by id, or `--names "Zone 1,Zone 2"` by exact display name), the command to run on each (`--command`, default `on`), the per-device hold (`--duration` seconds), and an optional `--off-command` (e.g. `off`) run on each device after its hold before the next. Set the hold long enough for the person to reach and observe each device. Proceed to Step 2.

## Step 2 — Confirm the physical run

This **physically activates** each device in turn — valves open, lights turn on, shades move. Confirm with the user which devices will fire, in what order, and that they are positioned to observe and record as the walk runs. For irrigation or anything with a real-world cost, confirm the command and duration before starting. Proceed to Step 3.

## Step 3 — Run the sequence

```
python3 .tessl/plugins/jbaruch/hubitat-dev/skills/_scripts/hub_device_sequence.py --ip <addr> --devices <id,id,...> --command <name> --duration <seconds> [--off-command <name>]
```

Argument contract, exit codes, and output shape: `skills/_scripts/hub_device_sequence.py` module docstring. Live per-step narration (`[i/N] <device>: <command>`, then the hold) goes to stderr as the walk runs; the JSON summary lands on stdout at the end. A device that fails to dispatch is skipped without a hold and the walk continues. Proceed to Step 4.

## Step 4 — Bind observations to device ids

Read the `steps[]` log: each entry carries the `device_id`, `device_name`, and whether the command `dispatched`. Bind each photo or field note to the `device_id`/`device_name` of the step that was firing when it was taken — that mapping is the deliverable. Re-run any `dispatched:false` step individually with `Skill(skill: "device-command")` to see the command's own error and verification. Finish here.
