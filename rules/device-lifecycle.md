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
- The report splits `appsUsing` by the app's **enabled/disabled switch state** and lists dashboards, the `parentApp`, and child devices — the full reference blast radius (`skills/_reference/endpoints.md`).
- **Enabled does not mean live.** An enabled app may retain an inert device reference. Rule Machine keeps withdrawn `tDev-N` settings and `state.trigDevsW` entries in `appsUsing` after its live trigger moved elsewhere.
- A hand-rolled app census walks `/hub2/appsList` **recursively** (`skills/_reference/endpoints.md`).
- Child apps are nested in `children[]` and absent from the top level.
- A flat read returns a confident false negative on "nothing references this device".
- The `settings` field returned by `GET /installedapp/statusJson/<appId>` is null for device inputs. Its `appSettings[]` entries carry `deviceIdsForDeviceList`, `deviceList`, and the setting name. Use that one-call inventory or `/installedapp/configure/json/<appId>/<page>.settings` to identify which configured input points at the device.

## Audit live consumers separately

- Run `skills/_scripts/hub_device_usage.py --live` when the question is "what actually consumes this device?"
- The three-state result is `live`, `not_live`, or `unknown`.
- Never report `unknown` as inert.
- For a subscription-driven app, a matching `statusJson.eventSubscriptions[].typeId` is positive live evidence.
- Subscription absence is conclusive only for an app type known to consume the device by subscription.
- Command-only consumers legitimately have no subscription and remain `unknown` without another live surface.
- For a Rule Machine **trigger**, `state.trigDevs` is authoritative. A trigger remains there while a Required Expression is false even though its event subscription is temporarily absent.
- A Rule Machine action or condition device need not appear in `trigDevs`. Absence from that trigger map is not a liveness negative without trigger-role evidence such as a matching `tDev*` setting or `trigDevsW` entry.
- `state.trigDevsW` and stale `tDev-N` settings are withdrawn bookkeeping, not live triggers. They remain deletion blast-radius references.

## Warn with the concrete blast radius

- Surface the actual list before deleting — name every enabled and disabled app reference, dashboard, parent app, and child device.
- Distinguish **reference state** from **consumer liveness**.
- State that enabled/disabled is the app switch state.
- Use the live audit before calling an enabled reference active or inert.
- The usage script only reads — it never deletes. Deletion is irreversible: read the hub-UI confirm dialog's "in use by N apps" state with Playwright (`skills/_reference/playwright-ui.md`), then have the **user** perform the final removal — the agent guides and confirms, it does not click the destructive delete. A radio (Z-Wave/Zigbee) device also needs a physical exclusion/factory-reset only the user can do (`rules/zwave-zigbee-mesh.md`).

## Verify after removing or renaming

- Re-read usage after the delete — do not assume the hub auto-pruned every reference type. Auto-pruning of app subscriptions is not guaranteed for dashboards, device inputs, or parent/child links.
- A reference that survives the delete is a dangling pointer to fix on the referencing app.
- **Hub-mesh mirrors are asymmetric across operations.** A `POST /device/update` rename propagates to the mirror's **`name`** on the consuming hub; a delete does not, and the mirror survives the source's removal (`skills/_reference/endpoints.md`).
- A mirror's **`label`** is custom.
- A mirror's `label` overrides its propagated `name`.
- A mirror's `label` changes only under an explicit rename on the consuming hub.
- `label` is the field every reader sees — the UI, device pickers, and any tool resolving by label.
- Renaming a mesh-shared device is TWO operations, source first.
- Verify the mirror's `label` after every source rename.
- Two devices sharing a `label` make every label-resolving consumer pick one of them arbitrarily.
- Never leave a freed label duplicated when the source's old name is about to be reused.
- Round-trip the field set before the first real edit of any device — `skills/_scripts/hub_device_update.py --hub <name> --device <id> --noop` (argument and output contract in its module docstring).
- A mis-encoded mesh boolean pair unshares the device and deletes the mirror (`skills/_reference/endpoints.md`).
- Every boolean on that form is checkbox-semantic.
- A literal `true` clears the field.

## Replacement re-wires nothing

- App-managed integrations (CoCoHue, HubiThings Replica) always create the replacement as a **new device id** — every prior reference points at the old, now-deleted id and silently breaks.
- Capture the old device's app / dashboard / scene memberships **before** deleting it (the enumerate step above is the capture).
- After the replacement is created or imported, restore those memberships onto the new device id, then report what was re-wired versus left for the user. Selecting the new device in each app is a UI action (`skills/_reference/playwright-ui.md`) — verify the configured input and its live surface.

## Swap before re-selecting by hand

- **Settings → Swap Device** re-points every app from one device to another in one action — reach for it before any manual re-select (`skills/device-migration/SKILL.md`).
- It is **not** available for a child device: devices owned by a parent device or parent app are excluded from its lists by design (`skills/_reference/parent-child-devices.md`). An app-managed replacement above re-wires by hand for that reason. A virtual-device hop does not lift the exclusion. The last swap of any chain still targets the child.
- The swap is **bidirectional**: apps already using the *new* device are moved onto the *old* one. Check the replacement's usage before swapping, not after.
- Hubitat scopes the swap to apps and claims nothing about **dashboards** — verify dashboard tiles separately rather than reporting them migrated.
- Order the work **references first, delete second** — swap while the old device still exists. A deleted device cannot be swapped from. When the old must go first (a radio exclusion), park the references on a virtual device and swap them onto the replacement afterwards.
