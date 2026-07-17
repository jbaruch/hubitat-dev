---
name: device-removal
description: Safely remove a Hubitat device — enumerate where it's used, warn about the blast radius before deleting, verify references cleared after, and re-wire them onto a replacement device. Use when the user wants to remove, delete, retire, or replace a device, or asks what a device is used by before deleting it.
argument-hint: "[device-id or name] [--hub <name>]"
---

# Device-Removal Skill

Process steps in order. Do not skip ahead.

Removing a device is effectively irreversible, and a replacement gets a **new device id** that
strands every prior reference (`rules/device-lifecycle.md`). This skill enumerates usage, warns
before the delete, verifies after, and restores references onto a replacement.

## Step 1 — Enumerate the device's usage

Read the hub's own usage list. Pass the device id, or `--name "<display name>"` to let the script
resolve the id from `/hub2/devicesList` (exact match — it fails clearly on zero or multiple matches;
do not resolve the name yourself):

```
python3 .tessl/plugins/jbaruch/hubitat-dev/skills/_scripts/hub_device_usage.py --device <id> --ip <addr>
python3 .tessl/plugins/jbaruch/hubitat-dev/skills/_scripts/hub_device_usage.py --name "<display name>" --ip <addr>
```

Use `--hub <name>` instead of `--ip` to resolve via `hubs.json` (`hub-config` skill). Argument and
output contract: `skills/_scripts/hub_device_usage.py` module docstring. The report splits `appsUsing` into
enabled vs disabled and lists dashboards, `parentApp`, and child devices. Proceed to Step 2.

## Step 2 — Warn with the concrete blast radius

State the actual references, never a bare count — name each enabled app, dashboard, parent app, and
child device. Distinguish load-bearing (enabled apps — live automations that break) from inert
(disabled apps, idle monitors). If nothing references the device, say so and proceed. Do not delete
anything in this step. Proceed to Step 3.

## Step 3 — Branch on retire vs replace

Ask whether the device is being **retired** (gone for good) or **replaced** (new hardware stands
in). On replace, the Step 1 report **is** the capture of memberships to restore — keep it.

**Retiring** — proceed to Step 4.

**Replacing** — move the references now, before the delete in Step 4:
`Skill(skill: "device-migration")`. Settings → Swap Device needs the old device to still exist, and
this skill's Step 4 destroys it. Migrating after the delete forfeits the swap and forces the
virtual/manual fallback (`rules/device-lifecycle.md`).

Where the hardware forces the old device out first — a radio exclusion, or reusing its physical
slot — the replacement cannot exist yet, so there is nothing to swap to: `device-migration` parks
the references on a virtual device instead. Take that path there, then return here. Proceed to
Step 4 once the references are off the old device.

## Step 4 — Guide the removal

Deletion is irreversible — the **user** performs it, not the agent. Navigate to the device's remove
control with Playwright and read the "in use by N apps" confirm dialog (`skills/_reference/playwright-ui.md`),
then have the user confirm the final removal. A radio (Z-Wave/Zigbee) device also needs a physical
exclusion/factory-reset only the user can do (`rules/zwave-zigbee-mesh.md`). An app with
`removeButton:false` (e.g. HubiThings Replica) is remove-not-automatable — tell the user. Proceed to
Step 5.

## Step 5 — Verify references cleared

Re-run the Step 1 command. Do not assume the hub auto-pruned — a reference that survives the delete
(a dashboard tile, a device input, a parent/child link) is a dangling pointer to fix on the
referencing app. Report what cleared and what did not. If retiring, finish here.

## Step 6 — Land a parked migration

Only when Step 3 parked the references on a virtual device. The replacement now exists, so the
references still sitting on the virtual have to reach it: return to
`Skill(skill: "device-migration")` for the second hop and its verification, and delete the virtual
device once it is empty.

If Step 3 migrated the references directly, they are already on the replacement and Step 5 verified
them — nothing is left here. Finish here.
