# Driving the Hubitat UI with Playwright MCP (grounded)

Observed live against a **C-8 Pro**, local network, **Hub Security off**. The `hubitat-dev`
toolset is HTTP/code only (`reference/endpoints.md`). A class of real tasks has **no documented
HTTP/code endpoint** and is reachable only through the hub's web UI at `http://<hub-ip>:8080`.
For those, drive the UI with the **Playwright MCP** — a headless Chromium that needs no browser
extension and no auth on a Hub-Security-off hub. Several gotchas below cost real time; one
silently overwrote a live Room Lighting scene before it was understood.

## When the UI is the only path

HTTP/code (`reference/endpoints.md`) handles source deploy/pull, log/event tail, mesh detail,
and device control via Maker API. The UI is required for:

- Installing an **app instance** — Add User App → configure pages → **Done**.
- Configuring **built-in / community apps** — Room Lighting, Notifications, Device Activity
  Check, CoCoHue, HubiThings Replica.
- **Deleting** a device or an app (also a physical step for radio devices — `rules/zwave-zigbee-mesh.md`).
- Importing devices (e.g. CoCoHue "Select Lights").
- Reading the backup list / downloading a backup.

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

1. **MDL/Vue checkbox & radio pickers lie about `input.checked`.** The device/capability pickers
   (notifier selection, Room Lighting "Devices to Automate") render selection state as a CSS class
   on the label — `label.is-checked` — while the underlying `input.checked` stays `false`. Read the
   class, never the property. Reading the property once made 15 selected members look unselected and
   nearly wiped them.

2. **Selections persist over a WebSocket, not observable HTTP.** The picker's "Update" button fires
   no HTTP request; the value commits on the page's **Done**. To make a device input actually save,
   do a genuine trusted click on the option, then Done. JS-forcing `.checked` or dispatching synthetic
   events bypasses the Vue model and does not persist.

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

## Grounding

Endpoints and hub behavior verified on a C-8 Pro with Hub Security off (baseline
`reference/endpoints.md`). Gotchas 1, 2, and 5 are the load-bearing ones — each was reached the
expensive way in real usage, and 5 corrupted a live scene. The Vue/MDL selection model and the
`statusJson` vs `configure/json` split are hub-firmware behavior; re-verify after a platform update.
