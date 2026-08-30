# Hubitat Hub Endpoints (grounded)

Verified 2026-07-14 against three **C-8 Pro** hubs on platform **2.5.1.125**, local network, **Hub Security off**. These endpoints are **undocumented and version-sensitive** — Hubitat does not support them and they can shift between firmware releases. Only the Maker API and the `/management/*` token API are officially supported. Re-verify after a platform update; the `_meta.verified_platform` in `skills/_reference/capabilities.json` tracks the baseline.

Base is `http://<hub-ip>:8080` unless noted. Websockets are on port `80` (`ws://<hub-ip>/...`). With Hub Security off, **no authentication** is needed — no login, no cookie. If a hub ever enables Hub Security, every call below needs a session cookie from `POST /login`.

## Code enumeration (what's installed in the editors)

Confirmed returning clean JSON on 2.5.1.125:

| Endpoint | Returns |
|----------|---------|
| `GET /hub2/userAppTypes` | Array of user **app** code entries: `{id, name, namespace, oauth, lastModified, ...}` |
| `GET /hub2/userDeviceTypes` | Array of user **driver** code entries: `{id, name, namespace, capabilities, ...}` |
| `GET /hub2/userLibraries` | Array of **library** code entries: `{id, version, author, category, description, ...}` |

The `id` from these lists is the `<codeId>` used in the code round-trip and update endpoints below. (These `/hub2/user*` endpoints supersede the older `/app/list/...` HTML pages the community catalogs list.)

## Code round-trip (read source + version)

| Endpoint | Returns |
|----------|---------|
| `GET /app/ajax/code?id=<codeId>` | `{id, name, version, source, status}` |
| `GET /driver/ajax/code?id=<codeId>` | `{id, version, source, status}` |
| `GET /library/list/single/data/<libId>` | Library source |

`version` is an integer bumped on every save. It is the **optimistic-concurrency stamp** — see below.

## Code deploy (create / update)

All `Content-Type: application/x-www-form-urlencoded`.

| Action | Endpoint | Body | Notes |
|--------|----------|------|-------|
| Create app | `POST /app/save` | `id=` (empty), `version=` (empty), `create=`, `source=<groovy>` | New id comes back in the `Location` redirect: `/app/editor/<id>` |
| Update app | `POST /app/ajax/update` | `id`, `version`, `source` | Returns JSON `{status:"success"}`. **Must send the current `version`** |
| Create driver | `POST /driver/save` | `id=`, `version=`, `create=`, `source=<groovy>` | New id from `Location`: `/driver/editor/<id>` |
| Update driver | `POST /driver/ajax/update` | `id`, `version`, `source` | Same version rule as apps |
| Enable OAuth (app) | `POST /app/edit/update` | `id`, `version`, `oauthEnabled=true`, `_action_update=Update` | OAuth cannot be enabled from source alone |

**Optimistic concurrency:** the hub rejects an update whose `version` is not the current one. This is the "don't clobber a newer hub edit" guard. The deploy flow: read current `version` via `/…/ajax/code`, send it with the update; on rejection, re-pull and reconcile — never blindly retry with a bumped number. **Save == compile:** a Groovy compile error is returned inline and the code does not save.

## Live logging & events (websockets)

Both confirmed on 2.5.1.125 — `GET` upgrade returns `HTTP 101 Switching Protocols`, server frames are unmasked text (opcode 1), no external library required.

| Socket | Frame shape (JSON per message) |
|--------|-------------------------------|
| `ws://<hub-ip>/logsocket` | `{name, msg, id, time, type, level}` — `level` ∈ `error\|warn\|info\|debug\|trace`; `type` ∈ `dev\|app` |
| `ws://<hub-ip>/eventsocket` | `{source, name, displayName, value, type, unit, deviceId, hubId, installedAppId, descriptionText}` |

Verified `/logsocket` frame captured live: `{"name":"mZone-Butler Pantry Zone","msg":"...is inactive","id":1199,"time":"2026-07-14 08:05:53.760","type":"dev","level":"info"}`.

REST log pulls also exist: `GET /logs/json`, `/logs/eventsJson`, `/logs/past/json`.

## Event history (undocumented — grounded 2026-07-16)

Both verified on **2.5.1.128**. These answer "when did this *actually* change?", which no status field can.

| Endpoint | Returns |
|----------|---------|
| `GET /device/eventsJson/<deviceId>` | Device event history: `date`, `name`, `value`, `descriptionText`, `source`, `type`, `producedBy`, `triggered`, `isStateChange`, `physical`, `digital`, `unit`. `[]` for a device that has never evented (measured: 23 of 156 devices) |
| `GET /hub/eventsJson` | Hub events — `systemStart`, `update`, `manualReboot`, `cloudBackup`. `value` on `update`/`systemStart` is the **build number**, so this is the hub's firmware timeline |

**Commands are events too, and they name their caller.** `/device/eventsJson` carries `command-<name>` entries (`type: "command"`) alongside attribute changes, so a command being *issued* is visible separately from the attribute *moving* — that gap is the whole diagnosis in a silent-failure case. `producedBy` names the app that issued it. Verified frame:

```json
{"name": "command-on", "value": null, "type": "command", "date": "2026-07-14T11:01:54.455-0500",
 "descriptionText": "Command called: on()", "isStateChange": false, "deviceId": 442,
 "producedBy": "<a href='/installedapp/configure/583' target='_blank' class='text-base'>HomeKit Integration</a>"}
```

`/hub/eventsJson` is how you correlate "it broke around Tuesday" with a platform update, and it pairs with the version-sensitivity warning at the top of this file: it is how you find out *when* the platform moved.

**HTML rides inside JSON string fields.** `producedBy` above is an anchor, not a name. So is `ipAddress` in `/hub/details/json` (`<a href="http://192.0.2.12">192.0.2.12</a> (Ethernet)`), and app names in the log endpoints carry status markup (`Ecobee Suite Manager<span style="color:green"> Online</span>` — **3570 of 8205** past-log lines held markup on the measured hub). Strip tags before matching on any of these; a name compared raw will not match.

## The two log endpoints disagree about time and order

Measured on one C-7, one moment, hub TZ `US/Central` (`-0500`):

| Source | Sample | Shape |
|---|---|---|
| `GET /hub/details/json` → `currentTime` | `2026-07-16T20:14:07+0000` | UTC, explicit offset |
| `GET /logs/eventsJson` → `date` | `2026-07-16T12:00:06.874-0500` | **hub-local**, explicit offset |
| `GET /logs/past/json` → stamp | `2026-07-16 20:12:40.424` | **UTC, and naive — no offset to warn you** |

Correlating an app's log line against an event across these two silently mis-orders by the hub's offset — you conclude a handler never fired when it fired five hours "earlier". Same trap as the zwaveJS `lastTime` note above, on two endpoints a debugger uses together constantly.

**They are also ordered oppositely** — `/logs/past/json` is **oldest-first**, `/logs/eventsJson` is **newest-first**. And `/logs/past/json` returns a JSON array of raw pre-formatted **strings**, not objects: three tab-separated fields, the third pipe-delimited.

```
"2026-07-16 20:12:40.424\tTRACE\tapp|4|Ecobee Suite Manager|Updates sent (132 / 2095ms)"
```

## Hub info & identity

| Endpoint | Returns |
|----------|---------|
| `GET /hub/details/json` | Hub identity: `platformVersion`, `hardwareVersion`, `hubName`, `hubUID`, `ipAddress`, `macAddress`, `timeZone`, ... (confirmed ~49 KB on 2.5.1.125) |
| `GET /hub2/hubData` | Newer JSON hub backend |
| `GET /hub2/devicesList` | Devices: `{suggestBackup, devices:[{key, data:{id, name, ...}, children[], parent, child}]}` — a **tree**: `parent`/`child` are bools ("is a parent" / "is a child"), and children appear **only nested** in `children[]`, never at the top level. Iterating `devices[]` flat misses every child device (`skills/_reference/parent-child-devices.md`) |
| `GET /hub2/appsList` | Installed apps + `systemAppTypes` — a **tree**, exactly like `/hub2/devicesList`: child apps appear **only nested** in `children[]`, never at the top level. Iterating `apps[]` flat misses most of the hub |
| `GET /hub/advanced/freeOSMemoryHistory` | The hub's own CSV series: `Date/time, Free OS, 5m CPU avg, Total Java, Free Java, Direct Java`, ~5 min cadence, **reset on reboot**. `…/freeOSMemoryLast` is the latest row. The load-average source that needs no driver (`skills/_reference/hub-load.md`) |
| `GET /logs/eventsJson` | LOCATION events. `severeLoad` lives here and **not** in `/hub/eventsJson`; its `value` is the tripping load average (`skills/_reference/hub-load.md`) |
| `GET /modes/json` | Real hub mode ids and names. Room Lighting's setting value `"0"` is an **All Modes sentinel**, not an id from this list |
| `GET /hub/edit` | The **Settings** page (UI). Not `/hub/settings`, which 404s — the nav link is the authority |
| `GET /installedapp/direct/<builtInAppType>` | Opens a built-in app, redirecting to a transient instance at `/installedapp/configure/<newId>/mainPage` (e.g. `swapDevice` → Settings → Swap Device). The instance takes the next app id and is **not** a persistent install: its **Cancel** discards it, after which `/installedapp/statusJson/<id>` returns `{}` and it is absent from `/hub2/appsList`. Verified 2.5.1.128 |

## Dashboards: the whole tile grid is one JSON POST (grounded 2026-08-30, 2.5.1.169)

```
GET  /apps/api/<appId>/dashboard/<appId>/layout?requestToken=<rt>
POST /apps/api/<appId>/dashboard/<appId>/layout?requestToken=<rt>    body = the whole layout object
     Authorization: Bearer <dashboard access_token>
```

Found in `/ui2/dashboard2/js/app.js`'s `saveLayout` action — the tile editor itself does exactly this. The app id
appears **twice** (parent path segment and child), which is not obvious from the UI URL.

**Two different tokens, and mixing them fails with `Invalid request token`:**

- `Bearer` is the dashboard's **`access_token`**, also at `installedapp/statusJson/<id>` → `appState.accessToken`.
- `requestToken` is **`javascriptRequestToken`**, scraped from the dashboard page HTML. It is **not** `parentToken`,
  which is also present on that page and is rejected.

Round-trip the GET and edit only `tiles`; every other key (`cols`, `bgColor`, `iconSize`, `rowHeight`, the pin
fields …) must be sent back or the POST overwrites them with defaults. Tile shape, `col`/`row` 1-indexed:

```json
{"id": 18, "device": "1694", "template": "water", "col": 4, "row": 3, "colSpan": 1, "rowSpan": 1}
```

`device` is a **string**. Templates seen: `water`, `valve`, `hsm` (with a negative pseudo-device id, `-3`),
`humidity` for a soil probe. Verified non-destructive: after a single-field edit a re-GET showed 15 tiles unchanged
in count, zero non-tile key drift, and zero change to the 14 untouched tiles.

**Authorization is separate from the tile.** A device must be in the dashboard app's `devicesPicked` **and** have a
tile; authorizing is not a tile, and a tile on an unauthorized device does not render.

**Creating one:** `GET /installedapp/createchild/hubitat/Dashboard/parent/<parentAppId>` creates the child
immediately and redirects to its config, **skipping the name prompt**, so it lands labelled `Dashboard`. Set
`input#label` on `/installedapp/configure/<id>` and press Done. The label surfaces in `/hub2/appsList` as `name`;
`statusJson.label` stays `null` and is not the field to check. `devicesPicked` on a *fresh* dashboard is an EMPTY
input, so the hidden-value write needs the `device-btn-empty` → `device-btn-filled` class flip
(`skills/_reference/playwright-ui.md`).

**Unexplained, recorded as an observation only.** After `GET /device/createLinked/...` minted a hub-mesh mirror,
that mirror appeared in `devicesPicked` on **6 of the 7** dashboards on that hub with no action taken — verified by
re-reading `installedapp/statusJson/<id>` for each. The one it did not join was the most recently built by explicit
hidden-input writes. The mechanism was not identifiable from HTTP: the device's own `dashboardIds` is `null`, as on
every sibling sensor. Anyone reproducing a fresh `createLinked` should check whether this is real, and whether it is
a platform convenience or a stale-render artefact.

## Device usage / blast radius (undocumented — grounded 2026-07-16)

`GET /device/fullJson/<deviceId>` returns the hub's own **computed** "in use by" list for a device — verified live on 2.5.1.128 (C-8 Pro, Hub Security off). This is the removal blast radius, straight from the hub; `skills/_scripts/hub_device_usage.py` projects it and the `device-removal` skill reads it.

| Field | Shape |
|-------|-------|
| `appsUsing` | Array of `{id, name, label, trueLabel, disabled}` — every app referencing the device. `disabled` is only the app's switch state; **enabled does not prove the reference is live** |
| `appsUsingCount` | **String** on the wire (`"2"`) |
| `appsUsingForDialog` / `appsUsingForDialogMore` | The same list shaped for the "in use by N apps" confirm dialog |
| `dashboards` | Array of dashboards showing the device (`[]` when none) |
| `parentApp` | The app that created the device, or `null` (non-null for app-managed integrations like CoCoHue / HubiThings Replica) |
| `childDevices` / `hasChildren` | `childDevices` is a dict `{parentId: [child device objects]}`; a delete of the parent takes the children with it |

`appsUsing` is the **delete blast radius**, not a liveness list. Rule Machine can leave a stale
`tDev-N` setting and `state.trigDevsW` entry after its live trigger moved; the old device remains in
`appsUsing` even though `state.trigDevs` points elsewhere. The inert reference still belongs in a
delete warning.

## Device command & state surfaces (`fullJson`, grounded 2.5.1.134–135)

The same `GET /device/fullJson/<id>` that carries the blast radius also carries the device's command surface and both persistence stores — read them here instead of reading driver source or inferring from the declared capability.

| Field | Holds |
|-------|-------|
| `commands[]` | The **authoritative command list**: each `{name, parameters:[{type, defaultValue, constraints?}], arguments, relatedAttribute, capability:<bool>}`. `capability:true` is a capability-required command; `false` is a driver custom command — inferring commands from the declared capability via `skills/_reference/capabilities.json` **misses every custom command**. An ENUM parameter carries `constraints` (its allowed values). Each `runmethod` `args` entry is a **typed object** matching `parameters[].type`, never a bare value (see the runmethod note below) |
| `deviceState` (top level) | Groovy `state` — the driver's **internal bookkeeping**. `{}` for drivers that keep none (`rules/state-vs-attributes.md`) |
| `device.currentStates` (nested) | **Attributes / Current States**, a dict keyed by attribute name. Each entry: `value`, `date`, typed variants (`numberValue`, `floatValue`, `stringValue`, `jsonValue`), `dataType` — so a device's full attribute set is discoverable without driver source (useful for `lint-review`, `scaffold`) |

`deviceState` and `device.currentStates` are **easy to reach for backwards** — Groovy `state` is top-level `deviceState`, attributes are nested under `device`. **`currentStates[attr].date` is the last _change_, not the last report** — it sits right beside `value` and reads like a freshness stamp but is not one (the `rules/state-vs-attributes.md` trap, embodied in a field). Measure a real gap distribution with `GET /device/eventsJson/<id>`.

## Read-route response-shape traps (grounded 2.5.1.134)

Defensive-parse these — each bit during the irrigation recon:

- `GET /hub2/appsList` returns a **dict with an `apps` key**, not a list. Built-in app entries populate `name` and leave `label` null.
- Each entry is `{key, data:{id, name, type, disabled}, children:[…same shape, recursively…], parent, child}`. **Walk `children[]` recursively.** Measured 2026-08-30 on 2.5.1.169 across three hubs, top-level `apps[]` vs the full walk, per hub: apps **37 → 186**, devices 9 → 13, bits 6 → 9. On the busiest hub **80% of the apps are children** — every Rule Machine rule is a child of the RM parent and every dashboard a child of `Hubitat® Dashboards`, so a flat iteration audits neither. `appsUsing` on `fullJson` does not have this problem; the hub computes it. The trap is in a hand-rolled census, which is what you write the moment the question is "which app *setting* contains this id" (`rules/device-lifecycle.md`).
- `GET /installedapp/statusJson/<id>` can return a bare `{}` — another instance of this route being unreliable for reading app config (below).
- `GET /installedapp/configure/json/<appId>/mainPage` can return **non-JSON** — parse defensively, do not assume a JSON body.
- **A wrong page name on `configure/json/<appId>/<page>` is not a free miss — it logs an `ERROR` against that app.** `Cannot find page 'meansPage' for app 1230`. On any hub running an error-notifier app that is a **push notification to a human**, naming a real app and reading exactly like a genuine fault in it. Three probes produced three Telegram alerts (2026-08-30, 2.5.1.176). **Never enumerate page names by guessing.** The loop is also useless: all five probes in that run returned no settings at all, the two *valid* page names included — this route did not render settings for any of them.
- **Enumerate sub-pages from the rendered DOM instead**, reading the config page's href buttons: `[...document.querySelectorAll('button[name^="_action_href"]')].map(b => ({name: b.name, label: b.innerText.trim()}))`. For Room Lighting 1230 that returns the real structure directly — `_action_href_name|onMeansPage|8`, `…|offMeansPage|9`, `…|onDevicesPage|7` — with no guessing and no error log.
- **Match on the `_action_href` prefix, not the full literal.** The Device Status Announcer's custom group page is `_action_href_pageCustomDeviceGroup0Href|pageCustomDeviceGroup|2`.
- **`mainPage` is not universal.** App 329 (a Device Status Announcer child) names its page `pageMain`, and a tool assuming `mainPage` fires the same logged ERROR. Read the name from the config page's own `configPage.name`.
- `GET /device/edit/<id>` is a **6.8 KB SPA shell** — no state, no routes, nothing in the served HTML. It is the natural first thing to try for a device and it is a dead end; drive the page with Playwright and capture the request instead (`skills/_reference/playwright-ui.md`).

## Installed-app configuration and live-consumer surfaces (grounded through 2026-07-24)

`GET /installedapp/statusJson/<appId>` carries several distinct surfaces:

| Field | Meaning |
|-------|---------|
| `settings` | Null for device/capability inputs on the verified builds; do not use it to inventory them |
| `appSettings[]` | Resolved settings, including `name`, `deviceIdsForDeviceList`, and `deviceList`; best one-call answer to "which input holds this device?" |
| `eventSubscriptions[]` | Current subscriptions; match the device against `typeId` for positive live evidence |
| `state.trigDevs` / `appState.trigDevs` | Rule Machine's authoritative trigger-device map |
| `state.trigDevsW` / `appState.trigDevsW` | Rule Machine withdrawn trigger bookkeeping; not a live trigger |
| `scheduledJobs[].prevRunTime` | Null until that schedule has fired since creation/reset; null does not mean disabled |

`GET /installedapp/configure/json/<appId>/<page>.settings` remains the page-specific configured-input
view and also carries `removeButton`. A page value is a device-id→label map, not a `{value: ...}`
wrapper. Configuration alone does not prove liveness.

For Rule Machine, read `trigDevs` even when `eventSubscriptions` is empty: a false Required
Expression temporarily removes subscriptions while the trigger remains correctly configured.
Absence from `trigDevs` is a negative only for a trigger-role reference. A Rule Machine action or
condition device may be live without appearing in that trigger map.
For arbitrary apps, a missing subscription is not a safe negative because command-only consumers
do not subscribe. `skills/_scripts/hub_device_usage.py --live` applies this three-state audit and
leaves unsupported negative cases `unknown`.

## Device control (official — Maker API)

Prefer Maker API for exercising devices in a test loop. Local: `http://<hub-ip>/apps/api/<makerAppId>/<path>?access_token=<token>`. **The `<token>` is a secret** — read it from an environment variable or the operator's secrets store at call time; never hardcode it, commit it, echo it into agent output, or write it to a log (a token carried in a query string otherwise leaks into request logs). The undocumented local endpoints above are the preferred path precisely because they need no token on a Hub-Security-off hub. Key paths: `/devices` (list), `/devices/all` (full JSON: capabilities, attributes, commands), `/devices/<id>`, `/devices/<id>/<command>/<secondaryValue>` (send command), `/devices/<id>/events`. Multi-hub note: with hubs meshed, one Maker API instance can expose devices from secondary hubs too — but **code** endpoints are per-hub and have no mesh.

## UI-fired requests you can replay (undocumented — grounded 2026-07-19 – 07-22)

Several operations documented as "UI-only" are ordinary HTTP requests the UI fires. Drive the UI **once** with Playwright, read the request the button fires (`browser_network_requests`), then **replay it directly** thereafter — the UI is the discovery tool, not the runtime. Baseline for this section: **C-8 Pro, 2.5.1.x, zwaveJS backend, local network, Hub Security off**; re-verify after a platform update. Still expanding as findings accumulate.

| Endpoint | Body / params | Effect |
|----------|---------------|--------|
| `POST /hub/zwave/nodeRemove` | `zwaveNodeId=<decimalNodeId>` (`application/x-www-form-urlencoded`, no CSRF token) | Force-removes a **FAILED** Z-Wave orphan → 302 to `/hub/zwaveInfo`; the node drops out of `/hub/zwaveDetails/json`. Removal is **async** — poll the census, don't assume instant |
| `GET /hub/zwaveRepair2?resetStats=false&maxHealth=10` | UI defaults; other values untested | Starts a **full Z-Wave network rebuild** (zwaveJS) → 200 `{"success":true,"message":null}`. A **trigger, never a status check** — see the note below. Poll with the two below (2026-07-22) |
| `GET /hub/zwaveRepair2Status` | — | Rebuild progress `{stage, html}`; `stage` is `IDLE` when none is running, `html` lists `Pending` / `Skipped` node ids in **hex** (`57` = node 87) (2026-07-22, re-verified 2026-08-13) |
| `GET /hub/checkZwaveRepairRunning` | — | `{"isZWaveNetworkHealRunning":"true"}` — whether a rebuild is in progress (2026-07-22, re-verified 2026-08-13) |
| `POST /device/runmethod` | JSON `{"id":<deviceId>,"method":"<command>","args":[{"type":"<T>","value":<v>}]}` | Sends a device command **without a Maker API app or token** → 200 `{"success":<bool>,"message":null}`. Each `args` entry is a **typed object** (`[]` for a no-arg command); a bare value 500s (see note). `success:true` means **dispatched**, not that anything changed — verify by observation (below) |
| `POST /device/update` | form-urlencoded, **the full device field set** (see note) | Renames / edits a device's **fields** (name, label, retention, driver, mesh booleans) → 200. Does **not** touch driver preferences — those have their own endpoint (below). **Omitting any field clears it.** Carries a `version` concurrency stamp; the mesh-boolean pair is destructive if mis-encoded (see note) |
| `POST /device/preference/save` | JSON `{deviceId, defaultCurrentState, commandRetry, showOnHome, preferences:[{name,type,value}]}` | Saves a device's **driver preferences** (the Preferences → Save panel), distinct from the `/device/update` field set → 200. Sends the **full** preference set; each entry `{name, type, value}` with `type` from `fullJson.settings[].type` (see note) |
| `POST /installedapp/disable` | JSON `{"id":<appId>,"disable":<bool>}` | Enables (`false`) / disables (`true`) any app instance → 200 `{"result":<bool>}` (verified 2026-07-21) |
| `POST /installedapp/update/json` | form-urlencoded, minimal: `_action_remove=Remove&formAction=update&id=<appId>&version=<version>&currentPage=mainPage` | **Removes** an installed app instance → 200 `{"status":"success","location":"/installedapp/list?section=automations"}`. Afterward `GET /installedapp/statusJson/<id>` returns `{}` and the id is absent from `/hub2/appsList`. Irreversible — see the app-removal note below (verified RM 5.1.8, 2026-07-27) |
| `GET /installedapp/createchild/hubitat/<ChildAppName>/parent/<parentAppId>` | path-encoded `<ChildAppName>` (e.g. `Room%20Lights`) | Creates a **parent/child** app instance → 302 to `/installedapp/configure/<newId>/mainPage` (2026-07-22) |
| `GET /installedapp/create/<appTypeId>` | `<appTypeId>` from `/hub2/appsList` `userAppTypes[].id` | Creates a **standalone** user-app instance → 302 to the transient configure page (2026-07-22) |
| `GET /device/listJson?capability=<capability.foo[,capability.bar]>` | capabilities comma-joined | Capability-filtered device list `[{id, displayName, …}]` — the list the classic `.btn-device` picker fetches; enumerate an input's candidate devices without the UI (2026-07-22) |
| `GET /hub/homekit/enableDevice/<deviceId>/<true\|false>` | — | **HomeKit: export / un-export** a device → 200. Persists immediately; the HAP DB rebuilds seconds later and the mDNS `c#` config generation bumps, which is what tells controllers to re-read. The app's **Done is not required**. Batch-safe at ~0.35 s spacing. The HomeKit Bridge app is `singleInstance`, so the path carries no app id. Found by reading `changeDeviceAuthorization()` in the bridge app page's JS (2.5.1.140, re-exercised 2.5.1.169) |
| `GET /device/addToMesh/<deviceId>` | — | **Hub Mesh: share** a local device to the mesh (run on the **source** hub) → 200; the device joins `sharedDevices[]` (2026-07-22) |
| `GET /device/createLinked/<sourceHubId>/<sourceDeviceId>` | `<sourceHubId>` = peer hub UUID (`hubMeshJson` `hubId` / `sharedDevices[].sourceHubId`) | **Hub Mesh: link** a peer-shared device (run on the **destination** hub) → 200; mints a new local linked device bound to the source (2026-07-22) |
| `GET /device/hubMeshFullRefreshNow` | — | Hub Mesh full resync (either hub) → 200; does **not** by itself link available devices (2026-07-22) |

**The HAP database is the honest surface for what is exported, not the app page.** `GET http://<hub-ip>:21063/accessories` is unauthenticated and is ground truth for what the bridge is actually serving; the app's `authorizedDevices` setting says what was *asked for*. `dns-sd -L "<bridge name>" _hap._tcp local` reads the pairing state (`sf=0` paired, `sf=1` unpaired). Verified 2026-08-30 on 2.5.1.169: one `enableDevice` GET moved `authorizedDevices` from 53 → 54 and the accessory appeared in the HAP DB ~10 s later with its Leak (`0x83`), Temperature (`0x8A`) and Battery (`0x96`) services. **The device-level `homeKitEnabled` field is not the export switch** — it reads `null` on correctly-exported sensors, because the export lives in the bridge app's `authorizedDevices` (`skills/_reference/homekit-mdns-network.md`, `rules/multi-hub-topology.md`).

**Hub Mesh sharing is two-sided.** The *source* hub shares a device (`addToMesh`); the *destination* hub must then explicitly **link** it (`createLinked`) — a shared device does not auto-appear on the destination, and neither a Linked-devices refresh nor `hubMeshFullRefreshNow` links it. The Hub Mesh UI lives at `/device/hubMesh` (**not** `/hub2/hubMesh`, which 404s). **Un-share / un-link are not yet captured** — `removeFromMesh` and a `removeLinked` counterpart are likely but unverified; do not assume the path. Read side is `/hub2/hubMeshJson` (Hub mesh section below); this grounds the cross-hub re-home in `skills/device-migration/SKILL.md`.

**Instance creation is transient — but abandonment leaves an orphan, not nothing.** `createchild` (parent/child, e.g. Room Lighting) and `create` (standalone user app) create the instance row **server-side by the GET itself**, before any page renders, then land on `/installedapp/configure/<newId>/mainPage`. What you do next is three-way, not two-way:

| what you do | result |
|---|---|
| **Done** (`_action_update`) | settings committed, instance kept |
| **Cancel** | instance discarded |
| **abandon** (navigate away / close tab / open a fresh tab) | instance **kept, empty** — an orphan that renders in the parent's child list as a nameless row |

**Cancel is the only discard** — a fresh browser tab is not a substitute, since the row already exists on the hub. Abandoning a half-built page (the natural move when the source needs another input and you want to start over) silently accumulates empty children under the parent. Clean an orphan with the app-removal POST above (`_action_remove`, minimal body: `…&id=<newId>&version=1&…`) — verified on both a Rule Machine child and a custom-parent child (2026-07-22). These are the parent/child and standalone companions to `GET /installedapp/direct/<builtInAppType>` for built-in apps (above). UI-drive mechanics for filling and committing the config pages are in `skills/_reference/playwright-ui.md` (gotchas 16, 27–28).

**App removal is a replayable POST, and `version` is its concurrency stamp.** The remove button on an app's mainPage fires `POST /installedapp/update/json` with the whole serialized form (every `settings[...]`, `params_for_action_href_*`, `referrer`, `_cancellable`, …) and `_action_remove=Remove` in front. **The five fields in the table are sufficient** — verified by replaying the minimal body — because `_action_remove` is branched on before any settings are applied. Read `version` fresh from `GET /installedapp/configure/json/<appId>/mainPage` → `.app.version`, the same read-fresh-then-send discipline as `/app/ajax/code` and `POST /device/update` (`rules/multi-hub-topology.md`). **What a stale `version` does on this route is untested** — whether it rejects like the code editor or silently proceeds — so pin it down before building a retry loop.

**Removal works on a DISABLED app, even though the UI hides the button.** A disabled app's config page (`GET /installedapp/configure/json/<id>/mainPage`) renders a stub — `removeButton: false`, `configPage.sections: []`, and on 2.5.1.x / RM 5.1.8 the rendered DOM offered **Enable alone** (no Remove, no Cancel). The endpoint removes it anyway: app 819 was disabled, `removeButton: false`, and the minimal POST removed it cleanly. So `removeButton: false` describes the *rendered UI*, not an enforced server-side guard — **at least for a Rule Machine child**. This does **not** license assuming the same for apps that set `removeButton: false` *by design* (HubiThings Replica, `rules/ui-automation.md`); that case is untested and the "remove not automatable" note stands until someone checks it.

**Beware the device-removal decoy on the app page.** If you drive the UI instead of replaying, the app-remove control is `button[name="_action_remove"]`, which raises a **PrimeVue** confirm (`Remove <app label> now?`, No / `button.p-confirm-dialog-accept`) — readable from the DOM, not a native `window.confirm`. The same page also carries a hidden `button[onclick="removeDeviceAndCallback()"]` **also labelled "Remove"**: that is the shared **device**-removal dialog (`GET /installedapp/deleteDevice/<appId>/<deviceId>`, removing a child device from the app), not the app remove. Match on `name="_action_remove"`, never on the visible text.

Baseline for app removal: C-8 Pro, 2.5.1.x, Rule Machine 5.1.8, local network, Hub Security off, 2026-07-27.

**`nodeRemove` is guarded to FAILED orphans only.** Verified 23× live on nodes with no bound `deviceId`, each confirmed by census diff against `/hub/zwaveDetails/json`. Behavior on a healthy/OK node (strict `removeFailedNode` vs. general remove) is **untested** — gate every call on `present + no deviceId + nodeState:FAILED`, and never POST a real device id.

**`runmethod` is the "flash a stale device to wake it" primitive** — verified `{"id":389,"method":"on","args":[]}` turned a plug on and flipped its Z-Wave `nodeState` FAILED→OK.

**Each `currentStates` row carries its own `deviceId`, and a swap leaves it stale.** The row is stamped with the device id it was created under, so after a **Swap Device** every row on the surviving record reads the *donor's* id until the next report overwrites it. It is the first thing you see in `fullJson` and it looks like a mis-bound device. The binding proof is `GET /hub/zwaveDetails/json` → `nodes[].deviceId` (`rules/device-lifecycle.md`).

**`runmethod` args are typed objects, and a wrong shape 500s before dispatch.** `args` is an **ordered array, one typed object per parameter** — a single-param command takes `args:[{"type":"ENUM","value":"3.0"}]`, a two-param `setLevel` takes `args:[{"type":"NUMBER","value":40},{"type":"NUMBER","value":5}]`. Each entry's `type` matches the parameter's declared `type` from `fullJson.commands[].parameters[].type` (an ENUM parameter also carries `constraints`, the allowed values). A **bare** value 500s both as a string (`["3.0"]`) and a number (`[3.0]`), as does form-urlencoded `id`/`method`/`args`; only the JSON typed-object form and the no-arg `args:[]` case succeed. The 500 is raised **before dispatch**, so the driver logs nothing — no exception, no `log.debug`, no trace — which is indistinguishable from the platform not supporting parameterized commands at all, and is why an earlier read concluded (wrongly) that `runmethod` was no-arg-only. `setLevel`-style ordered params are typed objects in order, not bare values. Measured on `setDetectionDistance(ENUM)`, 2.5.1.140.

**`POST /device/preference/save` sets driver preferences — `/device/update` does not.** `/device/update` covers the device **field** set; driver **preferences** are a separate endpoint, fired by the device page's Preferences → Save. Body `{deviceId, defaultCurrentState, commandRetry, showOnHome, preferences:[{name,type,value}]}`, each preference `type` one of `number`/`bool`/`enum`/`text` from `fullJson.settings[].type`, current values in `fullJson.inputValues[]`. Sends the **full** set like `/device/update`; omission semantics are **untested** — assume omitting a key clears it, and round-trip current values as a no-op first. Verified by a no-op round-trip then a real edit on two devices, 2.5.1.140. **Driver-swap stale-preference trap:** a device swapped onto a different driver **keeps preference values wherever names collide**, and an out-of-range leftover silently breaks the new driver's reporting — two TS0225 sensors carried `luxThreshold = -1` (a sentinel from the old driver) into a new driver declaring range `0..999`, and published only `motion` with **none** of their radar attributes (`detectionDistance`, `illuminance`, …) until a single in-range preferences save restored the full set; `configure()` / `refresh()` did not, and `setDetectionDistance` returned 200 changing nothing because the attribute never came back to contradict it. After any driver swap, save preferences before trusting anything the device reports or concluding it "doesn't support" an attribute. **UI-capture gotcha:** the Preferences panel is a PrimeVue `TabView` that renders `display:none` until the **Preferences** tab is clicked — filling it without clicking the tab first silently does nothing (same class as the Device-label note, `rules/ui-automation.md`).

**A command return code is not evidence it executed.** `runmethod` returns `{"success":true}` when the Groovy method is *dispatched*, not when the device moved — a method that throws, or a command to the state the device is already in, returns the identical payload, and in the already-in-state case the platform's change filter suppresses the event too (`rules/state-vs-attributes.md`), so "no event" does not distinguish worked from failed. This is the same success-shaped-lie as `/device/delete` returning 302 for a nonexistent id. Confirm through the command's `relatedAttribute` moving in `currentStates`, or through `GET /device/eventsJson/<id>`. The command surface (`fullJson.commands[]`) is in **Device command & state surfaces** above.

**`POST /device/update` (device rename/edit) — verified 9 devices, 2.5.1.135.** Same `/device/` namespace as `runmethod`, **entirely different conventions** — do not assume one shape from the other:

| | `POST /device/update` | `POST /device/runmethod` |
|--|--|--|
| encoding | form-urlencoded | JSON |
| payload | **full field set**, omissions clear values | minimal three keys |
| concurrency | `version` stamp required | none |
| response | HTML / redirect | `{"success":<bool>,"message":null}` |

The full field set: `name, label, zigbeeId, maxEvents, maxStates, spammyThreshold, deviceNetworkId, deviceTypeId, deviceTypeReadableType, roomId, meshEnabled, retryEnabled, meshFullSync, homeKitEnabled, locationId, hubId, groupId, dashboardIds, tags, defaultIcon, notes, id, version, controllerType`. Four traps:

- **`version` is an optimistic-concurrency stamp**, the same pattern as `/app/ajax/code` (`rules/multi-hub-topology.md`) — read it fresh from `fullJson` immediately before each POST; a successful update bumps it. It is the integer the hub bumps on every save, echoed back unchanged so a concurrent edit is detected, the role an HTTP `ETag` plays in an `If-Match` request.
- **Every boolean is checkbox-semantic — there is no second encoding.** `meshEnabled`, `meshFullSync`, `retryEnabled` and `homeKitEnabled` are each sent as `on` when true and **omitted entirely** when false. Posting the literal string `true` **clears** the field. Measured on **2.5.1.135** (2026-07-27): a no-op repost of device 326 sending literal `true` for `retryEnabled` / `homeKitEnabled` flipped **both** to `false` while the checkbox-encoded mesh pair survived. Re-confirmed on **2.5.1.169** (2026-08-30): uniform checkbox encoding round-trips with no drift on a Zigbee moisture sensor and on a hub-mesh mirror. "Serialize all booleans the same way" is the correct replay here — the trap is serializing them as JSON-style literals. Clearing the **mesh** pair is destructive: it disables hub-mesh sharing, removing the mirrored device on the consuming hub and breaking every app bound to the mirror. What clearing `homeKitEnabled` costs is **not** established — the HomeKit export is governed by the bridge app's `authorizedDevices`, and this device field reads `null` on correctly-exported sensors, so do not treat it as the export switch in either direction. `skills/_scripts/hub_device_update.py` encodes all four correctly; call it rather than hand-rolling the form.
- **The label input hides on an inactive PrimeVue tab** — UI-capture mechanics in `skills/_reference/playwright-ui.md`.
- **A Z-Wave `deviceNetworkId` is HEX, and every node-facing surface is DECIMAL.** See the node-addressing note in the Z-Wave census section below before feeding this field to anything that takes a node id.

**Round-trip a no-op before the first real write against a device whose field set you have not posted before.** `skills/_scripts/hub_device_update.py --hub <name> --device <id> --noop` reposts current state unchanged and reports what moved, split into `applied`, `benign_normalization` and `unexpected_drift`; a non-empty `unexpected_drift` means the write did not land as asked and no further edit should follow it. Argument contract, the field set, the checkbox list and the normalization table are in that script's module docstring and top-of-file constants. The two normalizations it forgives are the ones a **freshly linked hub-mesh mirror** produces on its first round-trip: `roomId` `null → 0` and `label` `null → ""`. A mirror is born from `GET /device/createLinked/...` with `name` = source label + `" on <source hub name>"`, `label` = `null`, `roomId` = `null` (2.5.1.169, 2026-08-30).

**Retention fields are display-tuned defaults.** `maxEvents` defaults to **11 per attribute** — 11 stored *changes*, not reports (`eventsJson` is change-filtered), so on a slow-moving signal it is a few hours and on a fast-changing one under an hour, rolled silently either way. `maxStates` 30, `spammyThreshold` 300. Raise `maxEvents` through this endpoint to use the hub as a short-horizon data buffer — 1000 covers ~two weeks of a slow-moving signal (`rules/data-collection.md`).

**Hub-mesh mirrors follow a rename into `name` only — not into `label`.** All nine mirror `name` values updated on the consuming hub with no action there (2.5.1.135). A mirror's `label` is custom, overrides the propagated `name`, and is the field every reader sees — the UI, device pickers, and any tool resolving by label; it changes only under an explicit rename on the consuming hub. Re-measured on **2.5.1.156**, renaming a mesh-shared soil probe on the source hub (`devices` 941) via `POST /device/update`:

| mirror field (`main` 1666) | before | after source rename |
|--|--|--|
| `name` | `… Right Leg on Devices In The New House…` | `… Drainage Channel on Devices In The New House…` — propagated immediately |
| `label` | `Zone 8 Back Yard Plants Soil - Right Leg` | `Zone 8 Back Yard Plants Soil - Right Leg` — unchanged, still so 15 min later |

So renaming a mesh-shared device is **two** operations, source first. Skipping the second leaves the source and every consumer on the other hub disagreeing, and it bites hardest when the freed name is reused: rename the source, pair the replacement, and the consuming hub holds **two devices with the identical label**, from which every label-resolving consumer picks one arbitrarily. This is still the **opposite** of delete, where mirrors survive the source's removal and need cleanup on both hubs (`rules/device-lifecycle.md`): **`name` propagates, `label` and delete do not.**

**`zwaveRepair2` is a trigger, and polling it to read progress starts a rebuild.** It answers by
state, which makes it look like a status route exactly once: idle → `{"success":true,"message":null}`
**and a rebuild is now running**; already running → `{"success":false,"message":"Inclusion or
rebuild is already running"}`. Poll `zwaveRepair2Status` (`{"stage":"IDLE","html":"Stage: Finished"}`
when none is running) or `checkZwaveRepairRunning` instead — both are read-only and both answered
live on 2026-08-13. Route names that do **not** exist (all 404): `/hub/zwaveRepairStatus`,
`/hub/zwaveRebuildStatus`, `/hub/zwaveNodeRebuildStatus`, `/hub/zwave/rebuildStatus`,
`/hub/zwaveRepairReport`. A second, independent progress signal is the radio log: during a rebuild
the stream carries `RequestNodeNeighborUpdate` / `NodeNeighborUpdate` / `DeleteReturnRoute` /
`GetRoutingInfo` at roughly one node per 15 s. Measured on one hub, same day: **active 83 lines/60 s**
(4 × `RequestNodeNeighborUpdate`, 4 × `NodeNeighborUpdate`, 3 × `DeleteReturnRoute`) vs **finished
9 lines/60 s**, the remainder being the routine `GetBackgroundRSSI` polls.

**Z-Wave rebuild is gated by node type.** The per-node "Rebuild route" action is offered only for **mains / always-listening** nodes (repeaters, plugs, lamps); **sleepy battery** nodes (e.g. a door lock) show only Refresh · State — no on-demand route rebuild. The global rebuild (`zwaveRepair2`) is the only lever that touches a sleepy node, and its route rebuilds **on its next wake** — it sits in the status `Pending` list and completes async. zwaveJS backend only (the "Rebuild network" label); legacy uses different wording. For a marginal battery node the durable fix is RF/topology — a repeater — not a repair click (`rules/zwave-zigbee-mesh.md`). **Observed once (unconfirmed):** a rebuild refreshes *routes* but may not refresh *neighbour tables* — a newly added repeater can stay invisible to the existing fleet (`heard by` ≈ 0 in `/hub/zwaveTopology`) across repeated rebuilds until a hub reboot re-interviews neighbours; reboot and a 2.5.1.134 → 2.5.1.140 update were confounded in that single observation (`rules/zwave-zigbee-mesh.md`).

## Z-Wave & Zigbee mesh detail (undocumented — grounded through 2026-07-23)

Both return clean JSON through 2.5.1.132, no auth with Hub Security off. Drive them for mesh
diagnostics; the `mesh-health` skill reads them via `skills/_scripts/hub_mesh.py`.

| Endpoint | Returns |
|----------|---------|
| `GET /hub/zwaveDetails/json` | `{enabled, healthy, zwaveJS, firmwareVersion, region, longRangeChannel, nodes:[...]}` |
| `GET /hub/zigbeeDetails/json` | `{enabled, networkState, healthy, inJoinMode, channel, weakChannel, panId, extendedPanId, powerLevel, devices:[...]}` |
| `GET /hub/zigbee/getChildAndRouteInfo` | **text/plain** — Child Data + Neighbor Table (`[name, shortId], LQI:<n>, age:...`) + Route Table. The per-device (router) **LQI** the JSON snapshot lacks |
| `GET /hub/zwaveTopology` | Routing matrix as an **HTML** `<table>` (not JSON) |

**Z-Wave `nodes[]` per-node fields:** `nodeId`, `deviceId` (Hubitat device id), `deviceName`,
`nodeState` (`OK` | `FAILED` — `FAILED` is a failed/ghost node), `msgCount` (int — traffic volume;
weigh `per` against it), `per` (cumulative packet-error **count**, not a %), `averageRtt` (ms, string),
`lwrRssi` (string — see scale note), `neighbors` (int), `routeChanges` (int or `N/A`), `route`,
`security`, `listening`, `beaming`, `batteryPercent`, `lastTime` (when the hub last heard the node —
see the timestamp trap below; **absent** on a node never heard, which is reported `nodeState:OK`).

**A Z-Wave `deviceNetworkId` is HEX; `nodeId` and the radio log are DECIMAL.** Hubitat stores the
device-side identifier (`GET /device/fullJson/<id>` → `device.deviceNetworkId`, and the same field in
the `POST /device/update` set) as **hex**, while every node-facing surface speaks **decimal**:
`nodes[].nodeId` here, `zwaveNodeId` on `nodeRemove`, the `[Node NNN]` text in `zwaveLogsocket`,
`hub_radiolog.py --node`, and `hub_mesh.py`. Measured on 2.5.1.156 (C-8 Pro, zwaveJS):

| device | `fullJson` → `deviceNetworkId` | node id in the radio log |
|--|--|--|
| Patio Right Motion Sensor (404) | `61` | **97** (`0x61`) |
| Patio Left Motion Sensor (405) | `62` | **98** (`0x62`) |

Both failure modes are **silent**, because a hex DNI like `61` is a perfectly plausible decimal node
id: on a hub where node 61 is asleep the filter matches nothing and reads as "the device isn't
transmitting", and on a hub that *does* have a node 61 you tail an unrelated device and draw
conclusions from its frames. In the #117 diagnosis the first capture was filtered on the decimal
reading and returned nothing; the real device surfaced only because an unfiltered capture caught
`Node 097` emitting `Tampering, product cover removed` as the sensor was handled. Pass the hex value
to `hub_radiolog.py --dni`, which converts it, or sidestep the conversion entirely with
`--device-id`. **Zigbee is unaffected** — `zigbeeId` is hex on both sides and reads as hex.

`listening:true` is the always-on / classic-mesh repeater indicator. `beaming` means the node
**requires** beam wake-up, not that it beams for others; its JSON value was false for every sampled
node on 2.5.1.132, including FLiRS locks, so read the Z-Wave Details UI Beaming column when that
status matters. `route` uses hexadecimal ids and includes hub plus destination: `01 -> 57` is direct
to node `0x57`; only intermediate ids are repeaters.

**Zigbee `devices[]` liveness trap:** `active` is not freshness. Devices silent for years still
reported `active:true` on 2.5.1.132. Use the offset-bearing `lastActivity` timestamp; a missing
timestamp is unknown. A generic `name:"Device"` / `type:"Device"` remains the unfinished-join
signature.

**Timestamp trap (grounded 2026-07-16, 2.5.1.128):** `lastTime` carries a different shape per Z-Wave
backend. The **legacy** backend emits an explicit offset — `2026-07-16T00:49:14+0000`, true UTC. The
**zwaveJS** backend emits a **naive** stamp in the hub's **local** zone — `2026-07-16T08:28:30.081`.
Reading a naive stamp as UTC ages every zwaveJS node by the hub's offset (measured: a 70-second-old
node read as 5.02 h on `America/Chicago`). The zone is `timeZone` in `GET /hub/details/json`. Zigbee's
`lastActivity` carries `+0000` on both. A second backend split beside the `lwrRssi` scale.

## Hub mesh (undocumented — grounded 2026-07-16)

`GET /hub2/hubMeshJson` — the hub's own peer table (read side). Hub mesh carries **commands** between hubs, so a
peer with a stale record drops them while every radio metric stays green; `skills/_scripts/hub_mesh.py`
analyzes it and the `mesh-health` skill reads it. The write side — share (`addToMesh`) and link
(`createLinked`) a device — is in the UI-fired requests section above.

| Field | Shape |
|-------|-------|
| `hubList[]` | Peers: `{name, hubId, ipAddress, active, offline, warning, deviceIds[], lastActive, uiSSLOnly, uiSecurityEnabled, hubVarNames[]}` |
| `hubList[].deviceIds` | Devices shared over that link — the **blast radius** of removing the peer (each is a link an app can bind to) |
| `hubList[].lastActive` | Epoch **milliseconds** (not an ISO string like everything else here) |
| `sharedDevices[]` | `{id, name, appsUsing[], childCount, sourceHubId}` — `sourceHubId: null` means the device is **local** to this hub |
| `availableLinkedDevices[]` | Shared by a peer and **not linked here**: `{deviceId, hubName, hubId, deviceDisplayName, linkedLocally: false, childCount, childDevice}` |
| `localLinkedDevices[]` | Linked here. Each carries its own `appsUsing[]`, which is how a mirror census answers "is any mirror an orphan" |
| `modeHubId` | The hub that owns mode, or `null` |

**`availableLinkedDevices` vs `localLinkedDevices` is the shared-but-not-linked diagnosis.** Sharing is two-sided (write side above), and when the mirror is missing the source hub looks correct because it *is* correct. Grounded 2026-08-30 on 2.5.1.169: three freshly shared probes read `meshEnabled: true` / `meshFullSync: true` on the source, byte-identical to a working sibling, with no mirror on the destination. One read settled it — all three sat in `availableLinkedDevices` with `linkedLocally: false`, and the peer's `hubList[].deviceIds` listed all three. Advertised, not linked, which points straight at the missing `createLinked` call. Without this read the symptom has no cheap discriminator and the tempting conclusion is that sharing failed on the source.

**`hubId` == `hubUID`:** the `hubId` here is the same identifier as `hubUID` in `GET /hub/details/json`
(verified across three hubs). Fetching a peer's `ipAddress` and comparing its `hubUID` to the recorded
`hubId` is what distinguishes a live peer, a dead address, and an address reassigned to another hub.

**The peer fields do not detect a stale record.** A peer whose `ipAddress` pointed at a long-dead
address on another subnet reported `active:true, offline:false, warning:null`, with `lastActive`
refreshing every few seconds, while every command to it was silently dropped for 13.7 h. Only probing
the address finds it. The table is asymmetric — each hub keeps its own record of the others, and one
side can be correct while the other is stale.

**Zigbee `devices[]` per-device fields:** `id`, `name`, `type`, `active` (bool), `ping`,
`messageCount`, `lastActivity`, `lastMessage`, `shortZigbeeId` (16-bit), `zigbeeId` (64-bit IEEE).
**No per-device LQI or RSSI is exposed here** — per-device (router) LQI is in `getChildAndRouteInfo`
above; per-frame LQI+RSSI in the radio log sockets below. Use `lastActivity` for snapshot liveness;
`active` is not a freshness field.

**Live radio log websockets** (verified 2026-07-15 on 2.5.1.128, `HTTP 101`, unmasked text frames,
case-sensitive paths) — the per-frame decoded traffic, distinct from the driver `/logsocket`. Tail via `skills/_scripts/hub_radiolog.py`:

| Socket | Frame shape (JSON per message) |
|--------|-------------------------------|
| `ws://<hub-ip>/zwaveLogsocket` | `{sourceLabel, plainTextMessage, deviceId, time}` — `sourceLabel` ∈ `SERIAL\|CNTRLR\|DRIVER`; node id and per-frame `RSSI: -NN dBm` live inside the decoded `plainTextMessage` text (`deviceId` is `-999` for hub-level lines) |
| `ws://<hub-ip>/zigbeeLogsocket` | `{name, id, deviceId, profileId, clusterId, sourceEndpoint, destinationEndpoint, groupId, sequence, lastHopLqi, lastHopRssi, type, payload, time}` — **`lastHopLqi` (0–255) and `lastHopRssi` (dBm) of the last hop into the hub** (the repeater→hub link for a routed device) |

**`GetBackgroundRSSI` — the hub's own receive noise floor, already in the `zwaveLogsocket` stream**
(verified live 2026-08-11/13 on 2.5.1.151 and 2.5.1.125, zwaveJS 15.26.0, C-8 Pro, region USLR).
A zwaveJS hub polls `FUNC_ID_ZW_GET_BACKGROUND_RSSI` (`0x3B`) by itself roughly every 30 s whenever
its queues go idle; nothing has to be triggered. The request and the controller's raw serial
response are both plain `plainTextMessage` lines:

```
[DRIVER] hub: » [REQ] [GetBackgroundRSSI]
[SERIAL] hub: « [ACK] (0x06)
[SERIAL] hub: « 0x0107013ba0a2a2a5c7 (9 bytes)

  01   07   01   3B   a0   a2   a2   a5   c7
  SOF  len  RES  fn   ch0  ch1  ch2  ch3  checksum
```

- The response line carries **no field name** — only the blob. `hub_radiolog.py` keys the decode on
  the bytes and verifies the Z-Wave serial checksum (`0xFF` XOR the length byte through the last
  data byte). `len` counts every byte after itself; frame type `01` is the response (`00` is the
  outgoing request and holds no measurement).
- Channel bytes are **signed dBm** (`0xa0` = −96). Z-Wave RSSI sentinels apply — `127`
  not-available, `126` receiver-saturated, `125` no-signal-detected — and this platform also emits
  `0x80` (−128) for a channel it did not measure.
- **`ch1` and `ch2` were identical in every sample** across three hubs (~150 + 6 samples). Read them
  as one channel.
- **Yield is the gotcha.** Only some polls carry a payload (6 of 10 on one hub, ~1 in 5 reported on
  another), and the controller answers only while its radio is idle: a capture taken during a
  `zwaveRepair2` rebuild returns **zero** frames. `--summary` reports `polls` beside `samples` so a
  busy radio does not read as a missing endpoint. Budget a long window — a 300 s capture returned
  ~2 samples where 1200 s returned ~37.
- Reference measurements, 20-minute captures, same night, same method: a hub in a server rack read
  ch0 −96.2 / ch1−2 −95.4 / ch3 −91.0 (n=37); a quiet study −109.3 / −105.2 / −104.2 (n=17); an
  upstairs office −97.8 / −91.0 / −89.1 (n=38). Per-channel spread 4–5 dB, and a control hub
  re-measured 20 h later drifted 0.2–0.8 dB. Against a 700-series −97 dBm receiver sensitivity the
  rack hub is noise-limited rather than sensitivity-limited.

**Backend vs topology — two independent axes, both verified live (the load-bearing gotcha):**

- **Backend** (`zwaveJS` true/false) sets the `lwrRssi` scale — absolute dBm (negative, e.g. `-78db`) on zwaveJS vs dB *above the noise floor* (positive, e.g. `27dB`) on legacy — and whether `routeChanges` is reported (`N/A` on zwaveJS, an int on legacy). Higher RSSI is better on both; a fixed numeric cutoff does not transfer.
- **Topology** sets `neighbors` and routing: **node id ≥ 256 = Z-Wave Long Range** (a star — `neighbors:0`, a direct `01 -> <node>` route, no repeaters, dynamic power); **id ≤ 232 = classic mesh** (neighbors + multi-hop routes). Verified: a classic node and LR nodes on the *same* zwaveJS hub show `neighbors:5` vs `0`, so `neighbors:0` is LR topology, not the backend.

Field meanings and the LR-vs-mesh remediation split: `rules/zwave-zigbee-mesh.md`.

**zwaveJS hands a driver's `parse()` a decoded JSON value payload, not the classic `zw device: …` string** (`rules/driver-lifecycle.md`). It is the driver-side counterpart of the JSON surfaces above. Shape, per Door Lock CC v4 (`cc` / `cmd` / `ep` are **decimal**, not hex):

```json
{"cc":98,"cmd":3,"ep":0,"values":[
  {"propertyName":"currentMode","value":255,"prevValue":0,
   "metadata":{"states":{"0":"Unsecured","255":"Secured"}}},
  {"propertyName":"boltStatus","value":"locked","prevValue":"unlocked"},
  {"propertyName":"outsideHandlesCanOpenDoor","value":[false,false,false,false]}]}
```

Most values carry `prevValue`, often `metadata.states` (the device's own enum), and some hold fields with no typed-class equivalent — already decoded. `zwave.parse()` accepts it but is lossy and **throws on some command classes** (`rules/groovy-gotchas.md`), so read the JSON directly. Grounded one lock model, 2.5.1.135; payload stability across platform version and command class is unverified.

## Hub management (official — token API)

`GET /hub/advanced/getManagementToken` → token, then `/management/reboot?token=`, `/management/firmwareUpdate?token=`. The Hub Information Driver (HPM) wraps reboot/update as device commands over Maker API.
