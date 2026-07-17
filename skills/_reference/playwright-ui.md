# Driving the Hubitat UI with Playwright MCP (grounded)

Observed live against a **C-8 Pro**, local network, **Hub Security off**. The `hubitat-dev`
toolset is HTTP/code only (`skills/_reference/endpoints.md`). A class of real tasks has **no documented
HTTP/code endpoint** and is reachable only through the hub's web UI at `http://<hub-ip>:8080`.
For those, drive the UI with the **Playwright MCP** — a headless Chromium that needs no browser
extension and no auth on a Hub-Security-off hub. Several gotchas below cost real time; one
silently overwrote a live Room Lighting scene before it was understood.

## When the UI is the only path

HTTP/code (`skills/_reference/endpoints.md`) handles source deploy/pull, log/event tail, mesh detail,
and device control via Maker API. The UI is required for:

- Installing an **app instance** — Add User App → configure pages → **Done**.
- Configuring **built-in / community apps** — Room Lighting, Notifications, Device Activity
  Check, CoCoHue, HubiThings Replica.
- **Deleting** a device or an app (also a physical step for radio devices — `rules/zwave-zigbee-mesh.md`).
- Importing devices (e.g. CoCoHue "Select Lights").
- Reading the backup list / downloading a backup.
- **Swapping a device's app references** — Settings → Swap Device (`skills/device-migration/SKILL.md`).

Reach for HTTP first every time; open the UI only when the operation is on this list.

## Setup

The Playwright MCP is added once at **user scope**, not shipped with this plugin:

```
claude mcp add playwright -s user -- npx -y @playwright/mcp@latest
```

It runs its own Chromium against the hub's IP. Adding it needs a Claude Code restart before its
tools load. The tools used below are the standard Playwright MCP surface: `browser_navigate`,
`browser_snapshot`, `browser_click`, `browser_type`, `browser_evaluate`, `browser_take_screenshot`.

## The workflow

1. `browser_navigate` to the page (`http://<hub-ip>:8080/installedapp/configure/<id>`, a device
   edit page, `/hub/backup`, …).
2. `browser_snapshot` — read the **accessibility tree**, not a screenshot (see gotcha 9).
3. Act with **real** `browser_click` / `browser_type` — never `element.click()` inside
   `browser_evaluate` (gotcha 4).
4. **Verify every mutation** by re-reading the DOM or the hub's `configure/json` — these UIs fail
   silently. For a device input, `statusJson` lies (gotcha 3); read `configure/json` or run the code.
5. For destructive/irreversible actions (device/app delete, scene edits), read the confirm dialog
   first and re-verify after.

## The gotchas

1. **Read selection from `label.is-checked`, never from `input.checked`.** The device/capability
   pickers (notifier selection, Room Lighting "Devices to Automate") render selection state as a CSS
   class on the label. `input.checked` is **unreliable — it may or may not track, depending on the
   element and the platform version**, and it gives no warning which case you are in. Reading the
   property once made 15 selected members look unselected and nearly wiped them; on 2.5.1.128 the
   property agreed with the class on a device radio and on 14 multi-select checkboxes. Both
   observations are real, which is the point: the class is the safe superset in every case measured.

2. **Selections persist over a WebSocket, not observable HTTP.** The picker's "Update" button fires
   no HTTP request; the value persists to the hub on the page's **Done**. To make a device input
   actually save, do a genuine trusted click on the option, then Update, then Done. JS-forcing
   `.checked` or dispatching synthetic events bypasses the Vue model and does not persist.
   Update is not a no-op, though — it commits into the form, and gotcha 10 is how you check that.

3. **`statusJson` hides device settings.** `/installedapp/statusJson/<id>` reports capability/device
   inputs as `None` even when set. Verify device inputs via
   `/installedapp/configure/json/<id>/<page>` (the `settings` object), or by running the code.

4. **`element.click()` in `browser_evaluate` does not trigger framework handlers** (jQuery/Vue
   toggles, MDL buttons). Use a real Playwright `browser_click` for anything with a bound handler.
   The same trap catches `browser_select_option` on a Vue-wrapped `<select>`: on **Settings → Swap
   Device**, selecting the "old" device that way sets the native `<select>`'s value while the app
   never reacts — the dependent "new" picker stays `disabled` and empty (verified 2.5.1.128). Click
   the real "Click to set" control instead, and read the dependent list to confirm the selection
   registered rather than trusting the value you just set.

5. **Room Lighting auto-captures physical state.** Adding devices to a Room Lighting scene and
   clicking "Done with Room Lights" **re-captures the current physical state of every light in the
   scene** — if the lights happen to be on, it silently overwrites the scene. Instead: add members,
   then set each device's captured state directly (click the Level cell → the `dimLA` number input;
   click the Switch cell → the on/off toggles). Capture is optional. Avoid "Re-Capture" unless the
   physical lights are already in the exact desired state.

6. **`mainPage` and sub-pages have different table column layouts.** Do not compare a device table
   read on `mainPage` against one on `.../onDevicesPage` by column index — the misalignment produces
   a wrong diagnosis. Identify columns by their hidden `settings[...]` input names or by content
   pattern.

7. **Some apps set `removeButton: false`** (e.g. HubiThings Replica) and expose no UI remove; the
   platform also rejects synthetic removal endpoints. Note these as **remove not automatable**.

8. **Backups are a proprietary format.** `/hub/backupDB?fileName=...` downloads an H2 MVStore file
   wrapped/encrypted behind a `-- H2 0.5/B --` header; external H2 tools reject it ("Store header is
   corrupt"). Backups are **restore-to-hub only** (full-hub, all-or-nothing) — a single app's
   settings cannot be extracted from one.

9. **Screenshots aren't visually inspectable** in this setup. Rely on `browser_snapshot` (the
   accessibility tree) and DOM reads via `browser_evaluate` for state — not `browser_take_screenshot`.

10. **Snapshot `ref`s resolve to the wrong element on MDL divs — silently.** Hubitat's controls are
    MDL **`<div>`s, not `<button>`s**, so the accessibility snapshot labels a generic wrapper and the
    selector generated from a `ref` grabs the wrapper's class soup. `browser_find` returned a `ref`
    for a picker's "Update"; the click it generated was
    `page.locator('.w-full.flex.flex-row').first().click()` — a **container**. No error, the picker
    looked fine, and `settings[thermostatA]` stayed `""`. Anything resting on `ref` or
    `getByRole('button')` is unreliable here (verified 2.5.1.128).

11. **The pattern that works: tag-then-click.** Walk up from the hidden input — the only stable
    identifier on the page — tag the real control, then click the tag for real. Assigning an `id`
    inside `browser_evaluate` is not what gotcha 4 forbids: the ban is on synthetic `element.click()`,
    not on DOM tagging. This gets a precise target *and* a genuine event.

    ```js
    // browser_evaluate — tag only, never click here
    const hidden = document.querySelector('input[name="settings[thermostatA]"]');
    let box = hidden;
    for (let i = 0; i < 6 && box; i++) {
      box = box.parentElement;
      const save = box && box.querySelector('.device-save');
      if (save && save.offsetParent !== null) { save.id = 'claude-update-thermostatA'; break; }
    }
    ```
    ```js
    // then: browser_click(target: '#claude-update-thermostatA')   <- real click, exact element
    ```

12. **The picker's "Update" is `div.device-save`, and there are N of them.** Class
    `mdl-button mdl-js-button mdl-button--raised device-save` — not a `<button>`. There is **one per
    device input on the page**, so a bare `.device-save` selector hits the wrong picker. Scope by
    walking up from that input's `input[name="settings[<name>]"]` and filter on
    `offsetParent !== null` — only the open picker's Update is visible. Same for the picker's
    *trigger*, the "Click to set" control.

13. **The hidden input is the commit signal — check it after every Update.**
    `input[name="settings[<name>]"]` is `""` until the picker's Update commits, and holds a
    comma-separated id list after (`"35,33,26,21,…"`). Cheapest possible check, and it catches
    gotcha 10 immediately. `.value` order is **selection order, not sorted** — compare as a set.

14. **`submitOnChange` device inputs gate the sections below them.** A device input with
    `submitOnChange: true` re-renders its dependent sections only **after its picker's Update
    commits** — not when the option is clicked. Dropdowns built from the selected device's data do
    not exist before then, so there is nothing to `selectOption`. **Order matters: commit the device
    input first, then fill what it gates.** Selections already made in dependent sections survive a
    later `submitOnChange` re-render (verified: five dropdown values intact after a second device
    picker committed).

15. **Opening an app config page creates a transient instance — protect it with a second tab.**
    Opening a user app from **Add user app** lands on `/installedapp/configure/<newId>/mainPage` with
    a real id, but nothing persists until **Done**, and the form carries `_cancellable: false`.
    **Never navigate the configuring tab.** To touch another app mid-configuration, open a second tab
    (`browser_tabs(action: "new")`), do the work there, then select back and re-verify before Done.
    Config survived the tab switch intact (verified 2.5.1.128). Same family as the built-in app's
    transient instance discarded on Cancel (`skills/_reference/endpoints.md`, `/installedapp/direct/`).

## Grounding

Endpoints and hub behavior verified on a C-8 Pro with Hub Security off (baseline
`skills/_reference/endpoints.md`); gotchas 10–15 verified on 2.5.1.128 while installing a user app instance
end-to-end (2 device radios, 25 contact-sensor checkboxes across two multi-selects, 5 enum dropdowns,
Done). Gotchas 1, 2, 5 and 10 are the load-bearing ones — each was reached the expensive way in real
usage; 5 corrupted a live scene, and 10 silently discarded a setting while the page looked correct.

**Everything here fails silently, which is why 13 is the habit that pays**: a `ref` that clicks a
container, an Update that never commits, and a working page are indistinguishable on screen. Read the
hidden input.

The Vue/MDL selection model and the `statusJson` vs `configure/json` split are hub-firmware behavior;
re-verify after a platform update. Gotcha 1 is the standing warning about *how* they drift — the
`input.checked` mechanism documented before 2.5.1.128 did not reproduce on it, while the guidance
built on `label.is-checked` held. Prefer the safe superset over the mechanism.
