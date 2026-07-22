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
   The picker's own Update reads `is-checked` too, so assert on the class, not `input.checked`, when
   confirming what an Update will commit (reinforced 2.5.1.131).

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

14. **For an *optional* device input, skip the picker — write the hidden `settings[<name>]` value and let Done serialize it.**
    Done (`button[name="_action_update"]`) serializes the form's hidden `input[name="settings[<name>]"]`
    values over the websocket, so an optional device input needs no picker: set the hidden input's
    `.value` to the device id (comma-separated for multi-select — the same string the picker writes) in
    `browser_evaluate`, then a real `browser_click` on Done. This is **not** the synthetic-event trap
    (gotchas 2, 4): you write the real commit-signal field (gotcha 13), the exact value the picker's own
    Update writes and Done reads — not a faked event on an option.

    ```js
    // browser_evaluate — set the commit signal directly; Done already has a stable selector
    document.querySelector('input[name="settings[plug_565]"]').value = "1246"; // id list
    // then: browser_click(target: 'button[name="_action_update"]')   <- real click on Done (id=btnDone)
    ```
    Collapses each optional-input wiring from ~9 tool calls to ~3 (navigate → one evaluate → one Done
    click). **Optional only**: a `required: true` input still validates on Done via `device-btn-empty`
    (gotcha 17), which this does not flip — a required-empty input stays rejected, so use the real picker
    to reach `device-btn-filled` (or edit an already-filled one). Verify at the source, never the UI:
    after Done, `configure/json/<appId>/<page>` shows `settings[plug_565] = {"1246":"<label>"}` and every
    other hidden `settings[*]` untouched (verified 2.5.1.131, 14 single-plug zones).

15. **`submitOnChange` device inputs gate the sections below them.** A device input with
    `submitOnChange: true` re-renders its dependent sections only **after its picker's Update
    commits** — not when the option is clicked. Dropdowns built from the selected device's data do
    not exist before then, so there is nothing to `selectOption`. **Order matters: commit the device
    input first, then fill what it gates.** Selections already made in dependent sections survive a
    later `submitOnChange` re-render (verified: five dropdown values intact after a second device
    picker committed).

16. **Opening an app config page creates a transient instance — protect it with a second tab.**
    Opening a user app from **Add user app** lands on `/installedapp/configure/<newId>/mainPage` with
    a real id, but nothing persists until **Done**, and the form carries `_cancellable: false`.
    **Never navigate the configuring tab.** To touch another app mid-configuration, open a second tab
    (`browser_tabs(action: "new")`), do the work there, then select back and re-verify before Done.
    Config survived the tab switch intact (verified 2.5.1.128). Same family as the built-in app's
    transient instance discarded on Cancel (`skills/_reference/endpoints.md`, `/installedapp/direct/`).

17. **A fresh *required* device input will not commit via automation — the empty→filled transition
    fails silently.** The picker mechanism (gotchas 10–13) works for **edits** but not for a first
    fill. An empty required picker starts `device-btn-empty`; after checking devices and clicking
    Update, the hidden `input[name="settings[<name>]"]` **does** take the id list (gotcha 13's commit
    signal), yet the button **never flips** to `device-btn-filled`, so Done rejects the page with
    "Please complete the required fields", repeatedly. Editing an **already-populated** picker persists
    cleanly (verified: removed and re-added a member, Done, confirmed via `configure/json`). **For a
    swap, add the new device before removing the old** — the input never goes empty, stays
    `device-btn-filled`, and the trap never fires. This is the biggest limiter here: automated app
    *install* (empty required input) is unreliable while *edits* are fine (verified 2.5.1.131). If you
    author the app, declaring the input `required: false` sidesteps this entirely (gotcha 24).
    **Picker/build-scoped:** this empty→filled failure is specific to the picker you are on — reproduced
    on the 2.5.1.131 re-point picker and the inline-Vue picker (gotcha 26), but **not** on the classic
    `.btn-device` picker RL's own inputs use (gotcha 27, RL v1.2.3), where a fresh *required* input
    commits cleanly and scripted RL installs are viable. Confirm the flip empirically per input rather
    than assuming the trap.

18. **`is-invalid` on a text input is a red herring.** An MDL text input keeps `class="… is-invalid"`
    on an app that saved fine, and it does not block Done. Do not chase it — in a failed Done the real
    blocker is gotcha 17's `device-btn-empty`, not a text field's `is-invalid` (cost real time treating
    it as the blocker).

19. **Device inputs are often on sub-pages reached by `hrefElem` buttons, not `<a>` links.** The target
    input is frequently not on the app's main page. Scan for `button[name^="_action_href"]` to discover
    sub-pages instead of concluding a setting is unreachable. Room Lighting:
    `button[name="_action_href_name|onMeansPage|N"]` → `motions`; `…|offMeansPage|N` → `motionsOff`.
    Device Activity Check: `button[name="_action_href_pageDeviceGroup1Href|pageDeviceGroup|1"]` →
    `group1.devices`. The `|N` index **shifts on every `submitOnChange` partial re-render** (committing
    one input renumbers the sub-page buttons) — re-scan `button[name^="_action_href"]` before every
    navigation, never hardcode N. Return via `_action_previous` ("Done with …") or `_action_next`
    (`id=btnNext`); final commit is the main-page `_action_update` (`id=btnDone`).

20. **Room Lighting has live-trigger buttons that look like navigation — they have side effects.**
    The buttons with id `settings[activate]` ("Activate") and `settings[turnOff]` ("Turn Off") are
    `submitOnChange` buttons that **physically switch the room's lights** and flip the page title to
    "(Active)". Target them as `button[id="settings[activate]"]` — the bracketed id is not a CSS
    id-selector, so `#settings[activate]` misparses as id `settings` with an `activate` attribute.
    Clicking "Activate" blind turned real bathroom lights on. The tell: an `hrefElem`-class button is
    navigation (safe); a `submitOnChange` on a `settings[...]`-id button is a **live action**. Read the
    class and name before clicking any unfamiliar Room Lighting button.

21. **Large pickers: gate on virtualization before toggling.** One monitored-device input held 457
    devices. Before editing, open the picker and compare the rendered `label.is-checked` count to the
    known selection count. 481 checkboxes rendered with exactly 457 checked ⇒ the full set is in the
    DOM, not virtualized ⇒ Update reads all of it and toggling two is safe. If fewer render than are
    selected, the list virtualizes and Update **drops the off-screen selections** — abort. Capture the
    full baseline from `configure/json` first and diff after: the 457-device edit was verified to change
    exactly `{old}→{new}`, 455 others untouched.

22. **Rule Machine trigger devices hide behind Select Trigger Events, and RM keeps two settings —
    verify via `state.trigDevs`.** The trigger device is not on the rule's main page: **Select Trigger
    Events** (`button[name="_action_href_name|selectTriggers|N"]`) → click the existing trigger row (a
    `<div>` reading e.g. "mZone-X motion reports active") → a `Motion sensors` picker bound to
    `settings[tDev1]`; swap it like any picker (add-new-before-remove-old, gotcha 17). **RM stores two
    device settings, `tDev1` and `tDev-1`** — the trigger editor updates only `tDev1`, while `tDev-1` is
    a staging leftover that keeps pointing at the old device. The authoritative live subscription is
    **`state.trigDevs`** (e.g. `{"1580:Motion":["1"]}`), with `state.trigDevsW` listing withdrawn
    devices. Verify a re-point via `state.trigDevs` from the rule's `statusJson`, never the raw `tDev*`
    setting. Consequence: the stale `tDev-1` makes the old device still show in `hub_device_usage.py`,
    but that reference is **inert** (no live subscription) — deleting the old device is safe and RM keeps
    firing on the new one (verified 2.5.1.131).

23. **`browser_run_code_unsafe` runs *real* interactions — batch bulk re-points with it.**
    `mcp__playwright__browser_run_code_unsafe` runs genuine Playwright calls (`page.locator(sel).click()`,
    `page.goto`, `page.waitForTimeout`) in a loop inside one tool call. These are **trusted events that
    persist exactly like `browser_click`** — not the synthetic `element.click()` gotcha 4 forbids. It
    collapses each ~18-call Room Lights re-point into one call and a 26-toggle Device Activity Check swap
    into one, which is what made a 19-zone migration practical. Four caveats, each hit for real:
    - `page.evaluate` takes **one** argument — wrap multiples in an object (`{o,n,t}`), or it errors
      "Too many arguments".
    - Picker-open **timeouts** happen (~1 per batch of 5–6 apps) — wrap each item in try/catch, collect
      results, retry the failure individually.
    - Batched `page.goto` can **race** a prior page's in-flight navigation → `net::ERR_ABORTED` — retry
      those with `{waitUntil:'load'}` and longer waits.
    - `page.url()` reads **stale** right after a confirm-Yes navigation — verify via HTTP
      (`statusJson`/`fullJson`), not the returned url.

24. **To make an app scriptably installable, author its device inputs `required: false` — it sidesteps
    gotcha 17.** Gotcha 17's empty→filled trap blocks a scripted install of any app with a *required*
    device input. An **optional** device input clears Done validation under automation, and the picker's
    populated hidden-input value still persists on Done even while the button stays `device-btn-empty`
    (verified: the instance saved all members and created its child device). A member-less instance is
    then harmless and inert. This does not rescue a third-party app whose input is already required —
    there it stays gotcha 17 (verified 2.5.1.131).

25. **Swap a device's driver in place — it keeps the id, DNI, and every app reference.** Changing an
    existing device's Type re-points nothing: consumers (Room Lighting, Device Activity Check, Rule
    Machine) keep working transparently, which makes it the clean fix for a device on the wrong driver
    (e.g. off the auto-inactivating built-in Virtual Motion Sensor, `rules/driver-lifecycle.md`). On the
    2.5.1.x PrimeVue device page: `/device/edit/<id>` → **Device Info** tab → the **Type** control is a
    PrimeVue dropdown (`.p-dropdown-label`, **not** a native `<select>`, so a
    `querySelectorAll('select')` sweep finds nothing). Click the label to open, type into
    `.p-dropdown-filter`, click the `.p-dropdown-item` matching the driver name exactly, then page
    **Save**. The swap re-runs the new driver's `installed()`, so the device's states reset — reconcile
    the owning app after (its `updated()` re-derives and re-drives). Batches cleanly via
    `browser_run_code_unsafe` (gotcha 23) — 19 devices swapped this way (verified 2.5.1.131).

26. **RL activation-options switch guards use SumoSelect enums + an *inline* Vue picker — not `#deviceListModal`.** The "Disable/Re-enable Activation when a switch turns on/off" guard on an RL instance's *Activate Lights Options* sub-page
    (`/installedapp/configure/<id>/mainPage/onMeansPage/optionsOnPage`) has two control types, both automatable via `browser_run_code_unsafe` (gotcha 23 — these are `page.*` calls). Verified end-to-end on #918/#921, 2.5.1.x, 2026-07-21.
    - **Enums `settings[onDisable]`/`settings[onEnable]` are SumoSelect** (`select.SumoUnder`, wrapper `.SumoSelect`) that commit via `submitOnChange` on dropdown **close**, not per option-click: real-click `.CaptionCont` (open) → click the `li.opt` for the value → real-click `.CaptionCont` again (close). The close fires the AJAX partial re-render that persists the enum **and** reveals the dependent device picker (same reveal contract as gotcha 15).
    - **The switch pickers `settings[switchesD]`/`settings[switchesOE]` render as an *inline* Vue list, not `#deviceListModal`.** The button is `button[data-elemname="switchesD"][data-target="#deviceListModal"]`, but `#deviceListModal` is a **dead empty shell** — the real list (Filter box + scrollable MDL checkboxes `input[name="<elemname>"][value="<devId>"]` + a `Select all / Unselect all / Update` footer) mounts **inline under the button**. Recipe: (a) real-click the `data-elemname` button to open; (b) **filter with real keystrokes** — `page.keyboard.type("<name>")`, **not** `locator.fill()`, which sets the value without triggering the Vue filter and leaves all rows rendered; (c) **check the row by coordinate** — read the label's `getBoundingClientRect()` and `page.mouse.click(left+10, midY)`; a `label`-*locator* click auto-scrolls and the dropdown treats it as an outside-click and **collapses**; (d) **click `Update` by coordinate** — it is a `div.mdl-button` reading "Update", **not** a `<button>` (match on text / any element), and it flips the button `device-btn-empty`→`device-btn-filled`; (e) Done up the chain (`_action_previous` ×2 → `_action_update`), then verify via `configure/json` (`page.url()` reads stale right after — gotcha 23).
    - **Both `switchesD` and `switchesOE` are required once their enum is set.** A half-set guard (enum set, device empty) makes the RL config page **self-reject with a validation alert on load**, which blocks further tool calls until dismissed (a gotcha-17 variant). The hidden-value shortcut (gotcha 14) can't fill them — they are required, and manually flipping the class + hidden value does not pass validation. Revert path: clear both enums via the SumoSelect close-gesture, then Done.

27. **Which device picker am I on? RL's own inputs use the *classic* `.btn-device` picker (the gotcha
    10–13 family), NOT the inline-Vue picker of gotcha 26 — and a fresh *required* input DOES commit
    here.** Three device-picker mechanisms exist; do not assume which one is in front of you:
    - *MDL `.device-save` picker* (gotchas 10–13) — tag-then-click, hidden `settings[<name>]` commit signal.
    - *Classic `.btn-device` delegated picker* (this gotcha) — the same `.device-save`/hidden-input
      family, driven by the delegated handler `$(document).on('click', '.btn-device, .btn-device-required', …)`.
      RL's `roomDevsL`, `motions`, `switchesOnDO`, … render as `button[data-elemname="<name>"]`; a real
      click `$.getJSON('/device/listJson?capability=…')` (usable directly to enumerate an input's candidate
      devices — `skills/_reference/endpoints.md`), builds MDL rows into `#<name>-options`
      (`input[name="<name>"][value="<id>"]`, `id="<name><id>"`), pre-checks selected via `is-checked`,
      then `fadeIn()`s the sibling `.device-list`.
    - *Inline-Vue picker* (gotcha 26) — RL activation-options switch guards `switchesD`/`switchesOE`
      **only**; keystroke filter + coordinate click, and a label-click **collapses** it.

    Recipe for the classic picker (verified RL v1.2.3, 2026-07-21): real `browser_click` on
    `button[data-elemname="<name>"]` → `page.waitForFunction` until `#<name>-options input[name="<name>"]`
    count > 0 (rows build from the fetch — do **not** pre-reveal via `style.display`) → real click
    `label[for="<name><id>"]` to check it (a label-click does **not** collapse this picker, unlike the
    Vue one) → tag + real-click the `.device-save` "Update" (scope per gotcha 12) → verify the hidden
    `settings[<name>]` holds the id and the button flipped `device-btn-empty`→`device-btn-filled`
    (gotcha 13). **Gotcha-17 exception:** a fresh **required** input (`btn-device-required`) **does**
    flip empty→filled on first fill and Done accepts it (verified on `motions` and `switchesOnDO`, both
    starting empty+required) — so **scripted fresh RL installs are viable** here.

28. **Building a new Room Lighting instance end-to-end.** Verified RL "Version 1.2.3 (6/26/2025)",
    2.5.1.x, 2026-07-21, across an 8-rule RM motion-lighting → RL migration.
    - **Create the child:** `GET /installedapp/createchild/hubitat/Room%20Lights/parent/<parentAppId>` — the
      parent RL app id is per-hub (endpoint spec, and the standalone `/installedapp/create/<appTypeId>` variant, live in
      `skills/_reference/endpoints.md`). Lands on the transient `/installedapp/configure/<newId>/mainPage`,
      persisted only on **Done** (`_action_update`, `id=btnDone`), discarded on Cancel (transient-instance family, gotcha 16).
    - **Page tree**, each reached by an `_action_href` button (gotcha 19 — re-scan for the shifting `|N`):
      mainPage (`roomDevsL` lights, `origLabel` name) → `onMeansPage` (Means to Activate) → `optionsOnPage`
      (Activate Lights Options); `offMeansPage` (Means to Turn Off) → `optionsOffPage` (Turn Off Lights Options).
    - **SumoSelect enums commit on dropdown close, exactly as gotcha 26's `onDisable`/`onEnable`:**
      `onMeans`, `onConds`, `modeXD`, `offMeans`, `modeXOff`, `offConds`. Multi-select — click each
      `li.opt` to toggle, existing selections stay; scope via `select[name="settings[<x>]"].closest('.SumoSelect')`.
    - **`offConds` polarity trap (near-miss, cost real care):** "*Limit Turning Off under these
      Conditions*" lists the condition that **prevents** turn-off, so it is inverted from intuition. To
      keep lights ON while a switch (e.g. Housekeeping) is on, set `offConds` = **"Switch is on"**
      (reveals `switchesOnDO`, renders *"Don't Turn Off when Switches are on: <switch>"*); **"Switch is
      off" is backwards** — it blocks turn-off while the switch is off, the normal state. Same shape on
      activate: `onConds` = "Mode is" reveals `modeXD` labeled *"Don't Activate when mode is"*. **Always
      read the rendered `_action_href` summary text before Done** — it states the guard in plain English
      and catches an inverted condition.

## Room Lighting: the gotchas that travel together

RL knowledge is spread across the numbered list above; in build/edit order it reads as one path:

1. **Create + navigate** — new child instance and its page tree (gotcha 28); sub-pages are `_action_href`
   buttons whose `|N` index shifts on every re-render (gotcha 19).
2. **Pick devices** — RL's own inputs (`roomDevsL`, `motions`, `switchesOnDO`) use the classic
   `.btn-device` picker, where fresh required inputs commit (gotcha 27); **only** the activation-options
   switch guards (`switchesD`/`switchesOE`) use the inline-Vue picker (gotcha 26).
3. **Set conditions** — SumoSelect enums commit on dropdown close (gotchas 26, 28); watch the
   `offConds`/`onConds` polarity and read the rendered summary before Done (gotcha 28).
4. **Don't trip the live buttons** — `settings[activate]`/`settings[turnOff]` physically switch the room (gotcha 20).
5. **Don't auto-recapture** — "Done with Room Lights" re-captures physical state and can overwrite a live scene (gotcha 5).

## HPM (Hubitat Package Manager) uninstall

Removing HPM packages is a **destructive multi-step wizard** with no single endpoint — drive it via
Playwright and verify over HTTP. Grounded on **HPM v1.9.11, platform 2.5.1.132** (C-8 Pro, Hub Security
off), removing 12 packages in one pass → **27 apps + 11 drivers** removed, all keep-packages intact.

**Page flow** — under the HPM *instance* id (the installed app id, e.g. `3`; **not** the
`dcm.hpm:Hubitat Package Manager` *code* id in `/hub2/userAppTypes`):

```
/installedapp/configure/<id>/prefOptions               ← main menu ("What would you like to do?") → "Uninstall"
…/prefOptions/prefPkgUninstall                         ← package multi-select → Next
…/prefPkgUninstallConfirm                              ← THE GATE: expands each package into its apps + drivers
  → Next (this COMMITS, server-side, immediately)
…/prefPkgUninstallConfirm/prefUninstall               ← "in progress…" → "complete" → Next → main menu
```

Main-menu buttons: Install / Update / Modify / Repair / Uninstall / Match Up / View Apps and Drivers /
Package Manager Settings.

**The package multi-select is a native `<select multiple>` wrapped by a Materialize widget** — both
render in the a11y tree (a `listbox` of `option`s = the real select, and a `list` of `listitem`s = the
Materialize `<ul>` a human clicks). Set the native select directly and skip the per-`<li>` clicks:

- `browser_select_option` on the native `<select multiple>`, values = the **display names**
  (`["Ecobee Suite", "Homebridge v2", …]`).
- **Verify against the native select's `selectedOptions`, not the Materialize `<ul>`** — the Materialize
  list does **not** repaint after `browser_select_option` (`li.is-checked` stays empty). Same principle
  as gotcha 1 — read whichever surface the framework treats as authoritative, not whichever merely looks
  selected; there that surface happens to be the class `label.is-checked`, here it is the native
  `selectedOptions` and the Materialize `<ul>` is the stale one. Unlike gotcha 4's Vue-wrapped `<select>`
  (where `browser_select_option` never registers), here it **does** persist — once you dispatch the event below.
- Fire a `change` event on the select so HPM's submit picks it up:
  `sel.dispatchEvent(new Event('change', {bubbles:true}))`.
- Each `option.value` is the package's **`packageManifest.json` raw URL** (author-identifying), not the
  display text — read it to confirm each selection maps to the intended author/package
  (`Ecobee Suite → SANdood/Ecobee-Suite`) before committing.

**The confirmation page is the authoritative gate.** It expands every selected package into its
component **apps AND drivers** (child instances included) and warns "be sure the apps and device
drivers are not in use." Read it fully — a mis-selected keep-package shows here before anything is deleted.

**Scope limits:**

- HPM only lists/removes packages **it installed** (or "Match Up"'d). **Manually-pasted code never
  appears** — delete that directly via `/app/list` (Apps code) and `/driver/list` (Drivers code).
- Removing a package with a **running instance** or a **driver bound to a device** orphans them — HPM
  warns but does not block. Check `/hub2/appsList` and device driver usage first
  (`skills/_reference/endpoints.md` device usage / blast radius).

**Verify over HTTP, not the UI** — the uninstall commits server-side immediately (no "Done"): diff the
names in `GET /hub2/userAppTypes` (apps) and `GET /hub2/userDeviceTypes` (drivers) before/after
(`skills/_reference/endpoints.md` code enumeration).

**Reversible:** HPM removal reinstalls from the same public-repo manifests via the Install flow — it
passes the "undo exists" test and is **not** a no-undo action.

## Grounding

Endpoints and hub behavior verified on a C-8 Pro with Hub Security off (baseline
`skills/_reference/endpoints.md`); gotchas 10–13 and 15–16 verified on 2.5.1.128 while installing a user app instance
end-to-end (2 device radios, 25 contact-sensor checkboxes across two multi-selects, 5 enum dropdowns,
Done). Gotchas 17–21 verified on 2.5.1.131 while re-pointing two live apps' device inputs (Room
Lighting + Device Activity Check) from an old zone device to a new one. Gotchas 22–25 verified on
2.5.1.131 across a 19-zone Zone Motion Controllers → custom-app migration (Rule Machine trigger
re-pointing, `browser_run_code_unsafe` batching, `required: false` scriptable install, in-place driver
swap). Gotcha 14 verified on 2.5.1.131 while wiring 15 app instances' optional plug inputs on Zone
Motion Watchdog (14 single-plug zones, hidden value + Done, no picker). Gotcha 26 verified on
2.5.1.x (2026-07-21) wiring the "Watching Living Room TV" movie-scene switch guard on RL instances
#918/#921 — the inline Vue picker, keystroke filter, and coordinate-clicked checkbox/Update. Gotchas
27–28 verified on RL v1.2.3 (2.5.1.x, 2026-07-21) building a Room Lighting instance end-to-end during
an 8-rule RM→RL migration — the classic `.btn-device` picker (fresh required inputs commit), `createchild`
instance creation, the SumoSelect enum set, and the `offConds` polarity trap. Gotchas 1, 2, 5,
10, 17, 20 and 23 are the load-bearing ones — each was reached the expensive way in real usage; 5
corrupted a live scene, 10 silently discarded a setting while the page looked correct, 17 blocks
automated install where the required-input flip fails (picker/build-dependent — not the classic RL
picker, gotcha 27), 20 switched real lights on, and 23 is the only reason the 19-zone migration was
practical.

**Everything here fails silently, which is why 13 is the habit that pays**: a `ref` that clicks a
container, an Update that never commits, and a working page are indistinguishable on screen. Read the
hidden input.

The Vue/MDL selection model and the `statusJson` vs `configure/json` split are hub-firmware behavior;
re-verify after a platform update. Gotcha 1 is the standing warning about *how* they drift — the
`input.checked` mechanism documented before 2.5.1.128 did not reproduce on it, while the guidance
built on `label.is-checked` held. Prefer the safe superset over the mechanism.
