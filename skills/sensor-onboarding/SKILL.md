---
name: sensor-onboarding
description: Onboard one or more Hubitat sensors with verification at each step — pair, confirm the built-in driver auto-selected, name by function and position, raise event retention, read the real adjustable preferences, share to the mesh and confirm the mirror, add to inactivity monitoring, acceptance-test, and reconcile the inventory. Use when the user wants to onboard, install, add, or set up sensors (soil probes, contact/motion/temperature/energy sensors) on a hub, or roll out a sensor fleet.
---

# Sensor-Onboarding Skill

Process steps in order. Do not skip ahead. Each step verifies before the next.

## Step 1 — Snapshot the inventory before pairing

Capture `GET /hub2/devicesList` (`skills/_reference/endpoints.md`) before any pairing. A join can mint a stray or duplicate device, and only a pre/post diff (Step 10) makes that visible. Save the id set. Proceed to Step 2.

## Step 2 — Pair the sensor

Pairing is physical — the user puts the radio into inclusion and pairs each device; the agent guides and waits. For a batch, pair one at a time so each new id is attributable. Proceed to Step 3.

## Step 3 — Confirm the driver auto-selected

Read `GET /device/fullJson/<id>` and confirm the device landed on a real built-in driver, not the generic `Device` / `Device` unfinished-join signature (`rules/zwave-zigbee-mesh.md`). A generic driver is an incomplete join — re-pair before continuing. Proceed to Step 4.

## Step 4 — Name by function and position

Name the device by **function and position** (`<function> - <position>`), qualifying even a single-sensor unit so a second sensor later does not force a rename (`rules/data-collection.md`). The rename is a `POST /device/update` mutation carrying the full field set, the current `version` stamp, and a **destructive mesh-boolean trap** (`skills/_reference/endpoints.md`). Run it through `skills/_scripts/hub_device_update.py`, which owns the field set, the boolean encoding and the fresh-`version` read (argument and output contract in its module docstring) — never a hand-built POST:

```bash
python3 .tessl/plugins/jbaruch/hubitat-dev/skills/_scripts/hub_device_update.py \
  --hub <hub> --device <id> --noop        # prove the round-trip first
python3 .tessl/plugins/jbaruch/hubitat-dev/skills/_scripts/hub_device_update.py \
  --hub <hub> --device <id> --set label="<function> - <position>"
```

A non-empty `unexpected_drift` from either call means the write did not land as asked — stop and re-read the device rather than repeating it. The device edit page is the UI alternative (`skills/_reference/playwright-ui.md`). Proceed to Step 5.

## Step 5 — Raise event retention

This is the step that decides whether the collected data still exists tomorrow. `maxEvents` defaults to **11 changes per attribute** (`rules/data-collection.md`); raise it before relying on the hub as a buffer, through the same script as Step 4:

```bash
python3 .tessl/plugins/jbaruch/hubitat-dev/skills/_scripts/hub_device_update.py \
  --hub <hub> --device <id> --set maxEvents=1000
```

Proceed to Step 6.

## Step 6 — Read the real adjustable preferences

Read `settings` from `fullJson`, never the datasheet — a built-in driver is frequently narrower than the hardware (`rules/driver-lifecycle.md`). A sensor advertising a 1–240-minute reporting interval may expose only `logEnable` / `txtEnable`, with no interval control. Report what is actually adjustable rather than assuming a configurable exists. Proceed to Step 7.

## Step 7 — Mirror the device to the consuming hub

If the device serves another hub, share it — `GET /device/addToMesh/<id>` on the source, then link it on the destination (`GET /device/createLinked/...`); sharing is two-sided and a shared device does not auto-appear (`skills/_reference/endpoints.md`). Confirm the mirror exists on the consuming hub before trusting it — `Skill(skill: "mesh-health")` verifies peer/mirror state. Skip this step for a single-hub sensor. Proceed to Step 8.

## Step 8 — Add to inactivity monitoring

Add the sensor to the inactivity monitor keyed on `lastActivityTime` or battery, **never on a value's presence in the event log** — a slow-moving sensor is change-filtered and a gap means steady, not silent (`rules/state-vs-attributes.md`, `rules/data-collection.md`). Proceed to Step 9.

## Step 9 — Acceptance-test the sensor

Exercise the sensor and confirm it responds — `Skill(skill: "device-command")` runs a command (or `refresh`) and verifies by observation. **Exclude the settling window:** a freshly installed sensor's first hours can read garbage while it physically equilibrates (a soil probe settles over several wet/dry cycles). Do not baseline or health-check inside that window. Proceed to Step 10.

## Step 10 — Reconcile the fleet

Re-read `GET /hub2/devicesList` and diff against the Step 1 snapshot. Every new id must be a device you paired; a stray or duplicate join surfaces only here. Report the reconciled inventory and any sensor still short of a step. Finish here.
