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

- The hub computes a device's usage itself — read it from `GET /device/fullJson/<id>` via `scripts/hub_device_usage.py` (output contract in its module docstring). Never delete a device whose usage has not been read first.
- The report splits `appsUsing` into **enabled** (live automations a delete breaks) and **disabled** (inert), and lists dashboards, the `parentApp`, and child devices — the full blast radius (`reference/endpoints.md`).
- Do not enumerate device inputs from `/installedapp/statusJson/<appId>` — it reports them as `None` even when set. `fullJson.appsUsing` is the hub's computed list; for a specific input, read `/installedapp/configure/json/<appId>/<page>` instead.

## Warn with the concrete blast radius

- Surface the actual list before deleting — name each enabled app, dashboard, parent app, and child device that references the device.
- Distinguish load-bearing from inert: an enabled app is a live automation; a disabled app or an idle monitor is inert. State which is which, never a bare count.
- The usage script only reads — it never deletes. The delete itself is a hub-UI action: drive it with Playwright after reading the confirm dialog (`reference/playwright-ui.md`). A radio (Z-Wave/Zigbee) device also needs a physical exclusion/factory-reset the agent cannot perform (`rules/zwave-zigbee-mesh.md`).

## Verify after removing

- Re-read usage after the delete — do not assume the hub auto-pruned every reference type. Auto-pruning of app subscriptions is not guaranteed for dashboards, device inputs, or parent/child links.
- A reference that survives the delete is a dangling pointer to fix on the referencing app.

## Replacement re-wires nothing

- App-managed integrations (CoCoHue, HubiThings Replica) always create the replacement as a **new device id** — every prior reference points at the old, now-deleted id and silently breaks.
- Capture the old device's app / dashboard / scene memberships **before** deleting it (the enumerate step above is the capture).
- After the replacement is created or imported, restore those memberships onto the new device id, then report exactly what was re-wired versus left for the user. Selecting the new device in each app is a UI action (`reference/playwright-ui.md`) — verify each one stuck.
