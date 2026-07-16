---
alwaysApply: true
description: Driving the Hubitat web UI with Playwright for UI-only operations — the silent-failure traps and the read-the-framework-state rule
---

# UI Automation

The `hubitat-dev` toolset is HTTP/code only. A class of operations has no documented endpoint and
is reachable only through the hub web UI at `http://<hub-ip>:8080`, driven with the Playwright MCP.
The setup, full workflow, selectors, and per-gotcha detail all live in `reference/playwright-ui.md`.

## Reach for HTTP first

- Source deploy/pull, log/event tail, mesh detail, and device control (Maker API) have grounded HTTP endpoints — use them (`reference/endpoints.md`).
- Drive the UI only for the operations with no endpoint: installing an app instance, configuring built-in/community apps (Room Lighting, Notifications, CoCoHue, HubiThings Replica), deleting a device or app, importing devices, reading/downloading a backup, swapping a device's app references (`skills/device-migration/SKILL.md`).

## Read state the way the framework stores it

- MDL/Vue checkbox and radio pickers keep selection in a `label.is-checked` CSS class, and leave `input.checked` at `false`. Read the class, never the property.
- Act with real `browser_click` / `browser_type`. `element.click()` inside `browser_evaluate` does not fire jQuery/Vue/MDL handlers.
- A device input commits on the page's **Done** over a WebSocket, not on the picker's "Update" and not over observable HTTP. Forcing `.checked` or dispatching synthetic events does not persist.

## Verify every mutation

- These UIs fail silently — re-read the DOM or the hub's `configure/json` after every change.
- `/installedapp/statusJson/<id>` reports device/capability inputs as `None` even when set. Verify device inputs via `/installedapp/configure/json/<id>/<page>` (the `settings` object), or by running the code.
- `mainPage` and its sub-pages use different table column layouts — identify a column by its hidden `settings[...]` input name or by content, never by index across pages.
- Screenshots are not visually inspectable in this setup — read state from `browser_snapshot` and DOM reads, not `browser_take_screenshot`.

## Destructive operations

- Read the confirm dialog before an irreversible action (device/app delete, scene edit) and re-verify after.
- Room Lighting re-captures the current physical state of every scene light on "Done with Room Lights" — an on light silently overwrites the scene. Add members, then set each device's captured state directly (Level cell → `dimLA` input; Switch cell → on/off toggles). Avoid "Re-Capture" unless the physical lights already hold the desired state.
- Some apps set `removeButton: false` (e.g. HubiThings Replica) and cannot be removed from the UI or a synthetic endpoint — record them as remove-not-automatable.
- Backups are a proprietary encrypted H2 file, restore-to-hub only (full-hub, all-or-nothing) — a single app's settings cannot be extracted from one.
