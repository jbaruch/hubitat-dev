---
alwaysApply: true
description: Removing a Hubitat device — enumerate its usage and warn before deleting, verify after, and re-wire references onto a replacement
---

# Device Lifecycle

Removing a device is effectively irreversible, and the hub mints a **new device id** for any
replacement, stranding every prior reference. Enumerate a device's usage and warn before deleting;
verify the references cleared after; on a replacement, capture the old memberships first and restore
them onto the new id.

## Enumerate before removing

- The hub computes a device's usage itself — read it from `GET /device/fullJson/<id>` via `skills/_scripts/hub_device_usage.py` (output contract in its module docstring). Never delete a device whose usage has not been read first.
- The report splits `appsUsing` into **enabled** (live automations a delete breaks) and **disabled** (inert), and lists dashboards, the `parentApp`, and child devices — the full blast radius (`skills/_reference/endpoints.md`).
- Do not enumerate device inputs from `/installedapp/statusJson/<appId>` — it reports them as `None` even when set. `fullJson.appsUsing` is the hub's computed list; for a specific input, read `/installedapp/configure/json/<appId>/<page>` instead.

## Warn with the concrete blast radius

- Surface the actual list before deleting — name each enabled app, dashboard, parent app, and child device that references the device.
- Distinguish load-bearing from inert: an enabled app is a live automation; a disabled app or an idle monitor is inert. State which is which, never a bare count.
- The usage script only reads — it never deletes. Deletion is irreversible: read the hub-UI confirm dialog's "in use by N apps" state with Playwright (`skills/_reference/playwright-ui.md`), then have the **user** perform the final removal — the agent guides and confirms, it does not click the destructive delete. A radio (Z-Wave/Zigbee) device also needs a physical exclusion/factory-reset only the user can do (`rules/zwave-zigbee-mesh.md`).

## Verify after removing

- Re-read usage after the delete — do not assume the hub auto-pruned every reference type. Auto-pruning of app subscriptions is not guaranteed for dashboards, device inputs, or parent/child links.
- A reference that survives the delete is a dangling pointer to fix on the referencing app.

## Replacement re-wires nothing

- App-managed integrations (CoCoHue, HubiThings Replica) always create the replacement as a **new device id** — every prior reference points at the old, now-deleted id and silently breaks.
- Capture the old device's app / dashboard / scene memberships **before** deleting it (the enumerate step above is the capture).
- After the replacement is created or imported, restore those memberships onto the new device id, then report exactly what was re-wired versus left for the user. Selecting the new device in each app is a UI action (`skills/_reference/playwright-ui.md`) — verify each one stuck.

## Swap before re-selecting by hand

- **Settings → Swap Device** re-points every app from one device to another in one action — reach for it before any manual re-select (`skills/device-migration/SKILL.md`).
- It is **not** available for a child device: devices owned by a parent device or parent app are excluded from its lists by design (`skills/_reference/parent-child-devices.md`). An app-managed replacement above re-wires by hand for that reason. A virtual-device hop does not lift the exclusion. The last swap of any chain still targets the child.
- The swap is **bidirectional**: apps already using the *new* device are moved onto the *old* one. Check the replacement's usage before swapping, not after.
- Hubitat scopes the swap to apps and claims nothing about **dashboards** — verify dashboard tiles separately rather than reporting them migrated.
- Order the work **references first, delete second** — swap while the old device still exists. A deleted device cannot be swapped from. When the old must go first (a radio exclusion), park the references on a virtual device and swap them onto the replacement afterwards.
