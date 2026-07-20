---
name: device-migration
description: Move every app reference from an old Hubitat device onto a new one — via Settings → Swap Device where possible, a virtual-device bridge or parking slot where the swap list blocks it, and a guided manual re-select where neither works. Use when the user wants to swap, migrate, replace, or move a device's references to a different device, re-home a device to a different hub over Hub Mesh, or asks why a device does not appear in the Swap Device list.
argument-hint: "[old device] [new device] [--hub <name>]"
---

# Device-Migration Skill

Process steps in order. Do not skip ahead.

A replacement device gets a **new device id**, and every app pointing at the old one silently breaks
(`rules/device-lifecycle.md`). **Settings → Swap Device** re-points apps in one shot when it is
available. It often is not, and the fallbacks are not interchangeable — Step 3 picks one by *why*
the swap is blocked.

This skill moves **references**. It does not delete the old device — `Skill(skill: "device-removal")`
owns that. Run this **before** that skill's delete: a deleted device cannot be swapped from.

Re-homing a device to a **different hub** over Hub Mesh is the park fallback with the orphaned mesh
link as the slot — Step 4 covers it.

## Step 1 — Capture what the old device is used by

The Step 5 verification and the manual path both need this, and it must be read **before** anything
changes:

```
python3 .tessl/plugins/jbaruch/hubitat-dev/skills/_scripts/hub_device_usage.py --device <id> --ip <addr>
python3 .tessl/plugins/jbaruch/hubitat-dev/skills/_scripts/hub_device_usage.py --name "<display name>" --ip <addr>
```

Argument and output contract: `skills/_scripts/hub_device_usage.py` module docstring. Use `--hub <name>`
instead of `--ip` to resolve via `hubs.json` (`hub-config` skill). Keep the report — it is the
capture, listing apps (enabled vs disabled), dashboards, `parentApp`, and child devices. Proceed to
Step 2.

## Step 2 — Try Settings → Swap Device

Navigate to `http://<hub-ip>:8080/installedapp/direct/swapDevice` (the Settings → Swap Device tile,
subtitled "Replace device across all apps at once"). It redirects to a transient app instance at
`/installedapp/configure/<id>/mainPage`. Drive it with Playwright per `skills/_reference/playwright-ui.md`.

The page has two pickers — **"Select device to be replaced in apps (old)"** and **"Select
replacement device for apps (new)"**. The new picker stays **disabled until an old device is
selected**; the hub then filters it to devices sharing at least one capability with the old one.

Read both lists before acting, and **never assume a selection stuck** — the pickers are Vue
controls that ignore a synthetic `selectOption` on the underlying `<select>` (the page's own value
changes while the new list stays empty). Click the real "Click to set" control.

Two hazards before you swap:

- **The swap is bidirectional.** Per Hubitat's doc, apps already using the *new* device do not stay
  with it — they are swapped onto the **old** device. Verify the new device is not already in use
  (run Step 1's script against it too); if it is, say so and get the user's decision first.
- **Capabilities should match.** Replacing a dimmer with a plain switch leaves apps calling
  `setLevel` on something that has no such command.

If both devices are listed, have the user perform the swap, then proceed to Step 5. If the target is
absent, proceed to Step 3.

## Step 3 — Diagnose why the target is absent

Do not reach for a fallback until you know which one can work. The reason decides:

- **Missing from the OLD list entirely ⇒ it is a child device.** Devices owned by a parent device or
  a parent app are excluded by design (`skills/_reference/parent-child-devices.md`) — the page says so:
  *"Most child devices are not swappable and are not listed here."* **A virtual hop cannot lift that
  exclusion.** The last swap of any chain still targets the child, still ineligible. Go to Step 6.
  (Exceptions Hubitat allows: AirPlay, Bluetooth, HomeKit Controller, Tuya, Wiz.)
- **Old device listed, new device missing from the NEW list ⇒ no overlapping capability.** That list
  is filtered to at least one shared capability. If the new device is itself a child, it is the case
  above. Otherwise go to Step 4.
- **The new device does not exist yet**, or the old must be excluded/factory-reset before the
  replacement can pair (`rules/zwave-zigbee-mesh.md`) ⇒ nothing to swap *to* yet. Go to Step 4 and
  park.

Confirm child-vs-capability against the hub rather than guessing: a device absent from
`/hub2/devicesList`'s **top level** is a child (children are nested only), and a `parentApp` in
`/device/fullJson/<id>` marks an app-owned child. Proceed to the step the diagnosis names.

## Step 4 — Bridge, park, or re-home across hubs

A virtual device is swappable (verified: `[Virtual] …` devices appear in the list), which makes it
useful for exactly two jobs:

- **Bridge** a capability gap — create a virtual device overlapping the old device's capabilities
  *and* the new one's, then swap old → virtual, then virtual → new. If no single virtual driver
  covers both sides, the bridge does not exist; go to Step 6.
- **Park** references when the old device must die first — swap old → virtual, then remove the old
  device and pair the new one, then swap virtual → new. The references wait on the virtual instead
  of dangling.

Create it under Devices → Add Device → Virtual, matching the capability you need (Virtual Switch,
Virtual Dimmer, Virtual Lock, …). Each hop is a Step 2 swap and carries Step 2's hazards. After the
final hop, delete the virtual device — a parked virtual left behind is a device that answers
commands and silently does nothing.

**Re-home across hubs over Hub Mesh needs no virtual device.** The "old" device is a Hub Mesh
**linked** device — `data.source: Linked` in `/hub2/devicesList` (`skills/_reference/parent-child-devices.md`).
Removing it on its **source** hub orphans the link here: it drops to **`[offline]`** but keeps its id
and its app bindings — the parking slot, ready-made. Capture its `appsUsing[]` first
(`/hub2/hubMeshJson` → `sharedDevices[]`, or Step 1's script). Then remove on the source hub, pair the
replacement natively here, and swap the offline link → the new device as in Step 2. If the orphan is
the only offline device, the swap's "old" pick is unambiguous. With others offline, pick it by the
id/name captured in Step 1 — `data.source: Linked` marks it as the mesh link. The emptied link is
then a normal removal (`Skill(skill: "device-removal")`). Proceed to Step 5.

## Step 5 — Verify the references actually moved

Re-run Step 1's script against **both** devices. The new device should now carry the apps the old one
had; the old should be down to nothing (or only what you intended to leave). A swap that reports
success in the UI but leaves references behind is the failure this step exists to catch.

**Dashboards are not covered by any claim here.** Hubitat's doc scopes the swap to "all apps" and
says nothing about dashboard tiles. Check the old device's dashboards from the Step 1 capture and
re-point any that survived by hand — do not report them as migrated without looking. Proceed to
Step 6 if anything did not move; otherwise proceed to Step 7.

## Step 6 — Manual swap, app by app

The fallback when no swap path exists — the doc's own remedy for an incompatible device, and the
only path for a child device. Work the Step 1 capture: for **each** app using the old device, open
it, select the new device where the old was selected, de-select the old, configure the new to match
if the app needs it (every app differs), and hit **Done** — the change does not commit until then,
and not over observable HTTP (`skills/_reference/playwright-ui.md`).

Re-point traps, all grounded in `skills/_reference/playwright-ui.md`:

- The device input is frequently on a **sub-page**, not the app's main page. Scan for
  `button[name^="_action_href"]` to reach it (gotcha 19) rather than calling a setting unreachable.
- **Add the new device before removing the old.** A required input taken empty will not re-commit via
  automation, and Done then rejects the page as incomplete (gotcha 17). Keeping it populated dodges the
  trap.
- Before toggling a **large** multi-select, confirm the full selection renders — a virtualized list
  drops the off-screen picks on Update (gotcha 21).
- In Room Lighting, a `submitOnChange` button on a `settings[...]` id is a **live action**, not
  navigation — "Activate" switches the real lights (gotcha 20).
- Room Lighting stores the turn-off sensor in **`motionsInactive`** on some instances, not `motionsOff`
  — a swap of `motions` and `motionsOff` alone misses it silently.
- Rule Machine hides the trigger device behind **Select Trigger Events** (gotcha 22) and keeps a stale
  `tDev-1` beside `tDev1`. Verify its re-point via `state.trigDevs` in the rule's `statusJson`, never the
  raw `tDev*` setting.

Verify each app individually via `/installedapp/configure/json/<appId>/<page>` (the `settings`
object). Do **not** verify device *inputs* with `/installedapp/statusJson/<appId>` — it reports them as
`None` even when set (`skills/_reference/endpoints.md`). Its `childDevices` list is a separate, reliable
field: a managed child device such as `mZone*` is absent from `/hub2/devicesList` top level, and its id
is read from the parent app's `statusJson.childDevices` (`skills/_reference/parent-child-devices.md`).
Trust this device-level re-read over the picker values — it is what catches a turn-off sensor left
behind in `motionsInactive`. A stale Rule Machine `tDev-1` keeps a re-pointed old device listing the
rule in Step 1's script, but that reference is **inert** — no live subscription. Once `state.trigDevs`
shows the new device, the old one is safe to delete. Re-run Step 1's script when done. Proceed to Step 7.

## Step 7 — Report what moved and what is left

State which apps moved, by what path (swap / bridge / park / manual), and name anything left for the
user — dashboards, scenes, an app that would not commit, a virtual device still to delete. Never
report a migration complete on a UI success message alone; report it on the Step 5 re-read.

Removing the **old app** (a superseded zone controller, say) is a two-step PrimeVue confirm — a
"Remove … now?" prompt, then a "This will remove N child device(s)…" prompt, both `button.p-confirm`
labelled "Yes" — and it **deletes the app's owned child devices**. A dangling reference from another app does not block
it: a stale Rule Machine `tDev-1` pointing at a deleted device is inert (`skills/_reference/playwright-ui.md`
gotcha 22). Agent-initiated Remove/Delete clicks are refused by the auto-mode classifier regardless of
in-chat approval — the **user performs the destructive delete** (`rules/device-lifecycle.md`), after
which a fresh retry proceeds.

If the old device is now to be removed, hand off: `Skill(skill: "device-removal")`. Finish here.
