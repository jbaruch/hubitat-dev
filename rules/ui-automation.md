---
alwaysApply: true
description: Driving the Hubitat web UI with Playwright for UI-only operations — silent-failure traps, authoritative state, and misleading field values
---

# UI Automation

The `hubitat-dev` toolset is HTTP/code only. A class of operations has no documented endpoint and
is reachable only through the hub web UI at `http://<hub-ip>:8080`, driven with the Playwright MCP.
The setup, full workflow, selectors, and per-gotcha detail all live in `skills/_reference/playwright-ui.md`.

## Scope and secrets

- This automation targets **only the operator's own hub on the local network**, never an external or arbitrary URL.
- The DOM it reads is the hub's first-party admin UI, not third-party or user-generated web content.
- Any Maker API token is a secret read from the environment, never hardcoded, echoed into output, or logged.

## Reach for HTTP first

- Source deploy/pull, log/event tail, mesh detail, and device control (Maker API) have grounded HTTP endpoints — use them (`skills/_reference/endpoints.md`).
- **Removing an installed app is now an HTTP route too** — `POST /installedapp/update/json` with `_action_remove` (`skills/_reference/endpoints.md`, app-removal note). It even fires on a disabled `removeButton:false` app. Keep the read-the-confirm-first discipline as a human checkpoint (below), but the mechanism no longer needs the UI.
- **A dashboard's whole tile grid is one JSON POST** to `/apps/api/<appId>/dashboard/<appId>/layout`, not N clicks in the tile editor (`skills/_reference/endpoints.md`).
- Dashboards are not UI-only work.
- **HTTP-first does not license guessing endpoint paths.** A wrong page name on `/installedapp/configure/json/<appId>/<page>` logs an app `ERROR` and pushes a notification to a human (`skills/_reference/endpoints.md`).
- Enumerate an app's sub-pages from the rendered DOM's `_action_href` buttons, never by probing plausible names.
- **Committing an installed app's config is a UI Done.** This is the one place the HTTP-first preference loses (`skills/_reference/playwright-ui.md`).
- **Never hand-serialize `_action_update` on `POST /installedapp/update/json`.** It is not the counterpart of the `_action_remove` route.
- `_action_remove` is branched on before settings are applied.
- `_action_update` applies settings and clears anything omitted (`skills/_reference/playwright-ui.md`).
- Match the Done on `name="_action_update"` / `id=btnDone`, never on position and never on visible text.
- Drive the UI only for the operations with no endpoint: installing an app instance, configuring built-in/community apps (Room Lighting, Notifications, CoCoHue, HubiThings Replica), deleting a device, uninstalling Hubitat Package Manager packages, importing devices, reading/downloading a backup, swapping a device's app references (`skills/device-migration/SKILL.md`).

## Read state the way the framework stores it

- MDL/Vue checkbox and radio pickers keep selection in a `label.is-checked` CSS class. Read the class, never `input.checked` — the property is unreliable and may or may not track, depending on element and platform version.
- Act with real `browser_click` / `browser_type`. `element.click()` inside `browser_evaluate` does not fire jQuery/Vue/MDL handlers.
- Snapshot `ref`s are unreliable on Hubitat's MDL `<div>` controls — a `ref` resolves to a wrapper and the click hits a container, silently. Tag the real control by walking up from its hidden `settings[...]` input, then click the tag: `skills/_reference/playwright-ui.md` gotchas 10–12. Tagging in `browser_evaluate` is not the banned synthetic click.
- The framework **differs by page**. App-config pages are MDL/jQuery — the `label.is-checked`, MDL `<div>`, unreliable-`ref` guidance here applies to them. The **device edit page on 2.5.1.135 is PrimeVue** (`p-inputtext`, `p-tabview-panel`, `data-pc-section` / `data-pc-name`); confirm which framework a page uses before assuming a selector strategy.
- On the PrimeVue device page a `TabView` panel renders `display:none` until its tab is clicked. The **Device label** input (for `POST /device/update`) sits in the *Device Info* panel; Playwright times out filling an invisible element, which reads like a selector bug. Click the tab first, then address it `input[inputid="Device label"]` (these inputs carry no `name` or `id`) (`skills/_reference/endpoints.md`).
- A device input persists to the hub on the page's **Done** over a WebSocket, not over observable HTTP. Forcing `.checked` or dispatching synthetic events does not persist.
- The hidden-`settings[<name>]` write is a shortcut only for an **already-filled** device input — set its hidden value directly and Done serializes it. A **never-set** input renders `device-btn-empty` whatever its `required:` value, and writing the hidden value does not flip that class. An empty **optional** input then makes Done a silent no-op; an empty **required** one rejects. Both need the picker or a `fill()` flip (`skills/_reference/playwright-ui.md` gotchas 14, 17).
- Commit device inputs **before** filling the sections a `submitOnChange` gates — the dependent controls do not exist until the picker's Update commits.
- Not every device picker is the MDL `device-save` picker. Newer **inline Vue** pickers (Room Lighting *activation-options* switch guards, `switchesD`/`switchesOE`) mount inline under the button, **not** in `#deviceListModal` (a dead shell): filter with real keystrokes (`locator.fill()` doesn't trigger the Vue filter) and click the checkbox and its `div.mdl-button` **Update** by coordinate — a label-locator click collapses the dropdown (`skills/_reference/playwright-ui.md` gotcha 26).
- Rule Machine action dropdowns are **SumoSelect**. `browser_select_option` sets the native `select.value` and reports success, but the widget never fires `submitOnChange` and the page never advances — drive the widget (real-click `p.CaptionCont` to open, then the `li`), same as RL's enum guards (`skills/_reference/playwright-ui.md` gotcha 30).
- The device picker's **Update** control is `class="… device-save"` but its **tag and DOM position both vary** — `<div>` vs `<button>`, and sometimes mounted outside the input's `#<name>-options` container. Query document-wide and filter to the visible one (`offsetParent`); match by class + visibility, never by tag and never by container (`skills/_reference/playwright-ui.md` gotcha 12).

## Verify every mutation

- These UIs fail silently — re-read the DOM or the hub's `configure/json` after every change, then re-read the app's live surface when the change is meant to alter behavior.
- For a device picker the concrete signal is its hidden `input[name="settings[<name>]"]`: `""` until the picker's Update commits, a comma-separated id list after. Compare as a set — the order is selection order, not sorted.
- Never navigate the tab configuring an app — nothing persists until **Done**. Use a second tab for work elsewhere.
- Installed-app device verification follows `rules/device-lifecycle.md` **Audit live consumers separately**.
- Do not read `statusJson.settings` as the configured-input inventory.
- Verify the configured and type-specific live surfaces defined in `rules/device-lifecycle.md`.
- `mainPage` and its sub-pages use different table column layouts — identify a column by its hidden `settings[...]` input name or by content, never by index across pages.
- A **disabled** Rule Machine rule's `configure/<id>/mainPage` is a stub — no `settings[...]`, `removeButton:false`, and a rendered control set that varies by build (Cancel/Remove/Enable on 2.5.1.134; Enable alone on 2.5.1.x/RM 5.1.8). Enable it first (`POST /installedapp/disable {"id":<id>,"disable":false}`) or an empty settings set reads as an empty rule. Note the hidden Remove button does not mean un-removable — HTTP removal works regardless (`skills/_reference/playwright-ui.md` gotcha 31).
- **Verify a Rule Machine rule at `statusJson.eventSubscriptions`, never at its config page.**
- On a rule **known to carry an event trigger**, zero subscriptions is a broken Required Expression, not a mode mismatch. Confirm the RE state before diagnosing.
- RM subscribes to a rule's triggers only while the Required Expression is true, and to the RE's own source always.
- An action-only rule invoked by another rule legitimately holds no subscription. Establish the trigger role first (`rules/device-lifecycle.md`).
- One subscription in a non-matching mode is the healthy state.
- `POST /installedapp/disable` off-and-on and RM's own **Update Rule** button both leave it at zero (`skills/_reference/playwright-ui.md`).
- Cutting a Rule Machine action leaves its `actType.N`/… settings behind — a present `settings[N]` does not mean action N exists. Verify against the rendered action rows or the "Select Actions to Run" summary (`skills/_reference/playwright-ui.md` gotcha 33).
- To change a Rule Machine action's type, **add the replacement action before cutting the old** — the type cannot be changed in place, and add-before-cut keeps the rule from going actionless (`skills/_reference/playwright-ui.md` gotcha 32).
- Screenshots are not visually inspectable in this setup — read state from `browser_snapshot` and DOM reads, not `browser_take_screenshot`.

## Interpret misleading values

- Room Lighting `modes: ["0"]` means **All Modes**. It is not mode id 0.
- Resolve real mode ids from `/modes/json`.
- Room Lighting activation exclusions live in `modeXD`.
- Room Lighting turn-off mode triggers live in `modeXOff`.
- Both settings use real mode ids, never the all-modes sentinel.
- `scheduledJobs[].prevRunTime: null` records no previous firing for the current schedule. It does not mean the job is disabled.
- A dashboard tile rendering **`?`** means its `template` names an attribute the bound `device` does not have.
- A `?` tile is not a dead sensor report. Read the bound device's `currentStates` for the template's attribute before concluding the sensor failed.

## Destructive operations

- Read the confirm dialog before an irreversible action (device/app delete, scene edit) and re-verify after. Removing an app is irreversible; the confirm names the app, which is the last checkpoint. When driving the UI, match the app-remove button on `name="_action_remove"`, never the visible text — the same page carries a same-labelled `removeDeviceAndCallback()` **device**-removal decoy (`skills/_reference/endpoints.md`, app-removal note).
- **App removal has an HTTP route** — `POST /installedapp/update/json` with the minimal `_action_remove` body (`skills/_reference/endpoints.md`). Prefer it over UI-driving, but keep the read-the-confirm-first human checkpoint: read `version` fresh from `configure/json/<id>/mainPage`, confirm the target app by name, then POST. Verify after via `statusJson` (`{}`) and absence from `/hub2/appsList`.
- Room Lighting re-captures physical state on "Done with Room Lights" — verified for **level/CT values on an already-captured instance**, where an on light silently overwrites the scene. It did **not** fire for a newly added device on a *fresh* instance: the switch defaulted to `swVal:"on"` even with the light physically off at Done (`skills/_reference/playwright-ui.md` gotchas 5, 40). Add members, then set each device's captured state directly (Level cell → `dimLA` input; Switch cell → on/off toggles; or edit any cell via `buttonClick(this)`, gotcha 40). Read the capture table before Done regardless — `on(off)` is correct, `off(off)` is not. Avoid "Re-Capture" unless the physical lights already hold the desired state.
- Rule Machine's `button[id="settings[runAction]"]` ("Run Actions") executes the rule's actions immediately — a live side effect, not navigation. Target it as an attribute selector (the bracketed id is not a CSS id-selector) and read the button before clicking (`skills/_reference/playwright-ui.md` gotcha 35).
- `removeButton: false` means two different things. A **disabled** app renders `removeButton:false` incidentally, yet HTTP removal still works (above). Apps that set `removeButton: false` **by design** (e.g. HubiThings Replica) are the untested case — record them as remove-not-automatable until someone confirms otherwise; do not generalize the disabled-app result to them.
- Backups are a proprietary encrypted H2 file, restore-to-hub only (full-hub, all-or-nothing) — a single app's settings cannot be extracted from one.
