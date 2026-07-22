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

`version` is an integer bumped on every save. It is the **optimistic-concurrency token** — see below.

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
| `GET /hub2/appsList` | Installed apps + `systemAppTypes` |
| `GET /hub/edit` | The **Settings** page (UI). Not `/hub/settings`, which 404s — the nav link is the authority |
| `GET /installedapp/direct/<builtInAppType>` | Opens a built-in app, redirecting to a transient instance at `/installedapp/configure/<newId>/mainPage` (e.g. `swapDevice` → Settings → Swap Device). The instance takes the next app id and is **not** a persistent install: its **Cancel** discards it, after which `/installedapp/statusJson/<id>` returns `{}` and it is absent from `/hub2/appsList`. Verified 2.5.1.128 |

## Device usage / blast radius (undocumented — grounded 2026-07-16)

`GET /device/fullJson/<deviceId>` returns the hub's own **computed** "in use by" list for a device — verified live on 2.5.1.128 (C-8 Pro, Hub Security off). This is the removal blast radius, straight from the hub; `skills/_scripts/hub_device_usage.py` projects it and the `device-removal` skill reads it.

| Field | Shape |
|-------|-------|
| `appsUsing` | Array of `{id, name, label, trueLabel, disabled}` — the apps referencing the device. `disabled` is the **load-bearing (enabled) vs inert (disabled)** split the removal warning turns on |
| `appsUsingCount` | **String** on the wire (`"2"`) |
| `appsUsingForDialog` / `appsUsingForDialogMore` | The same list shaped for the "in use by N apps" confirm dialog |
| `dashboards` | Array of dashboards showing the device (`[]` when none) |
| `parentApp` | The app that created the device, or `null` (non-null for app-managed integrations like CoCoHue / HubiThings Replica) |
| `childDevices` / `hasChildren` | `childDevices` is a dict `{parentId: [child device objects]}`; a delete of the parent takes the children with it |

**`statusJson` blind spot:** `/installedapp/statusJson/<appId>` reports device-input `settings` as `None` even when set (its `eventSubscriptions` covers event subscriptions only). Verify a specific device input via `/installedapp/configure/json/<appId>/<page>` (the `settings` object) — that page also carries `removeButton` (an app with `removeButton:false` cannot be removed from the UI). `fullJson.appsUsing` is the hub's computed list and does not have the `statusJson` blind spot.

## Device control (official — Maker API)

Prefer Maker API for exercising devices in a test loop. Local: `http://<hub-ip>/apps/api/<makerAppId>/<path>?access_token=<token>`. Key paths: `/devices` (list), `/devices/all` (full JSON: capabilities, attributes, commands), `/devices/<id>`, `/devices/<id>/<command>/<secondaryValue>` (send command), `/devices/<id>/events`. Multi-hub note: with hubs meshed, one Maker API instance can expose devices from secondary hubs too — but **code** endpoints are per-hub and have no mesh.

## UI-fired requests you can replay (undocumented — grounded 2026-07-19 – 07-22)

Several operations documented as "UI-only" are ordinary HTTP requests the UI fires. Drive the UI **once** with Playwright, read the request the button fires (`browser_network_requests`), then **replay it directly** thereafter — the UI is the discovery tool, not the runtime. Baseline for this section: **C-8 Pro, 2.5.1.x, zwaveJS backend, local network, Hub Security off**; re-verify after a platform update. Still expanding as findings accumulate.

| Endpoint | Body / params | Effect |
|----------|---------------|--------|
| `POST /hub/zwave/nodeRemove` | `zwaveNodeId=<decimalNodeId>` (`application/x-www-form-urlencoded`, no CSRF token) | Force-removes a **FAILED** Z-Wave orphan → 302 to `/hub/zwaveInfo`; the node drops out of `/hub/zwaveDetails/json`. Removal is **async** — poll the census, don't assume instant |
| `GET /hub/zwaveRepair2?resetStats=false&maxHealth=10` | UI defaults; other values untested | Starts a **full Z-Wave network rebuild** (zwaveJS) → 200. Poll with the two below (2026-07-22) |
| `GET /hub/zwaveRepair2Status` | — | Rebuild progress `{stage, html}`; `html` lists `Pending` / `Skipped` node ids in **hex** (`57` = node 87) (2026-07-22) |
| `GET /hub/checkZwaveRepairRunning` | — | `{"isZWaveNetworkHealRunning":"true"}` — whether a rebuild is in progress (2026-07-22) |
| `POST /device/runmethod` | JSON `{"id":<deviceId>,"method":"<command>","args":[<secondaryValues>]}` | Sends a device command **without a Maker API app or token** → 200. `args` is the ordered command params (`setLevel` → `[level, duration]`) |
| `POST /installedapp/disable` | JSON `{"id":<appId>,"disable":<bool>}` | Enables (`false`) / disables (`true`) any app instance → 200 `{"result":<bool>}` (verified 2026-07-21) |
| `GET /installedapp/createchild/hubitat/<ChildAppName>/parent/<parentAppId>` | path-encoded `<ChildAppName>` (e.g. `Room%20Lights`) | Creates a **parent/child** app instance → 302 to `/installedapp/configure/<newId>/mainPage` (2026-07-22) |
| `GET /installedapp/create/<appTypeId>` | `<appTypeId>` from `/hub2/appsList` `userAppTypes[].id` | Creates a **standalone** user-app instance → 302 to the transient configure page (2026-07-22) |
| `GET /device/listJson?capability=<capability.foo[,capability.bar]>` | capabilities comma-joined | Capability-filtered device list `[{id, displayName, …}]` — the list the classic `.btn-device` picker fetches; enumerate an input's candidate devices without the UI (2026-07-22) |
| `GET /device/addToMesh/<deviceId>` | — | **Hub Mesh: share** a local device to the mesh (run on the **source** hub) → 200; the device joins `sharedDevices[]` (2026-07-22) |
| `GET /device/createLinked/<sourceHubId>/<sourceDeviceId>` | `<sourceHubId>` = peer hub UUID (`hubMeshJson` `hubId` / `sharedDevices[].sourceHubId`) | **Hub Mesh: link** a peer-shared device (run on the **destination** hub) → 200; mints a new local linked device bound to the source (2026-07-22) |
| `GET /device/hubMeshFullRefreshNow` | — | Hub Mesh full resync (either hub) → 200; does **not** by itself link available devices (2026-07-22) |

**Hub Mesh sharing is two-sided.** The *source* hub shares a device (`addToMesh`); the *destination* hub must then explicitly **link** it (`createLinked`) — a shared device does not auto-appear on the destination, and neither a Linked-devices refresh nor `hubMeshFullRefreshNow` links it. The Hub Mesh UI lives at `/device/hubMesh` (**not** `/hub2/hubMesh`, which 404s). **Un-share / un-link are not yet captured** — `removeFromMesh` and a `removeLinked` counterpart are likely but unverified; do not assume the path. Read side is `/hub2/hubMeshJson` (Hub mesh section below); this grounds the cross-hub re-home in `skills/device-migration/SKILL.md`.

**Instance creation is transient.** `createchild` (parent/child, e.g. Room Lighting) and `create` (standalone user app) both land on `/installedapp/configure/<newId>/mainPage` that persists only on **Done** (`_action_update`) and is discarded on Cancel — the parent/child and standalone companions to `GET /installedapp/direct/<builtInAppType>` for built-in apps (above). The UI-drive mechanics for filling and committing those config pages are in `skills/_reference/playwright-ui.md` (gotchas 16, 27–28).

**`nodeRemove` is guarded to FAILED orphans only.** Verified 23× live on nodes with no bound `deviceId`, each confirmed by census diff against `/hub/zwaveDetails/json`. Behavior on a healthy/OK node (strict `removeFailedNode` vs. general remove) is **untested** — gate every call on `present + no deviceId + nodeState:FAILED`, and never POST a real device id.

**`runmethod` is the "flash a stale device to wake it" primitive** — verified `{"id":389,"method":"on","args":[]}` turned a plug on and flipped its Z-Wave `nodeState` FAILED→OK.

**Z-Wave rebuild is gated by node type.** The per-node "Rebuild route" action is offered only for **mains / always-listening** nodes (repeaters, plugs, lamps); **sleepy battery** nodes (e.g. a door lock) show only Refresh · State — no on-demand route rebuild. The global rebuild (`zwaveRepair2`) is the only lever that touches a sleepy node, and its route rebuilds **on its next wake** — it sits in the status `Pending` list and completes async. zwaveJS backend only (the "Rebuild network" label); legacy uses different wording. For a marginal battery node the durable fix is RF/topology — a repeater — not a repair click (`rules/zwave-zigbee-mesh.md`).

## Z-Wave & Zigbee mesh detail (undocumented — grounded 2026-07-15)

Both return clean JSON on 2.5.1.128, no auth with Hub Security off. Drive them for mesh
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
| `modeHubId` | The hub that owns mode, or `null` |

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
above; per-frame LQI+RSSI in the radio log sockets below; this snapshot is liveness + network-level only.

**Live radio log websockets** (verified 2026-07-15 on 2.5.1.128, `HTTP 101`, unmasked text frames,
case-sensitive paths) — the per-frame decoded traffic, distinct from the driver `/logsocket`. Tail via `skills/_scripts/hub_radiolog.py`:

| Socket | Frame shape (JSON per message) |
|--------|-------------------------------|
| `ws://<hub-ip>/zwaveLogsocket` | `{sourceLabel, plainTextMessage, deviceId, time}` — `sourceLabel` ∈ `SERIAL\|CNTRLR\|DRIVER`; node id and per-frame `RSSI: -NN dBm` live inside the decoded `plainTextMessage` text (`deviceId` is `-999` for hub-level lines) |
| `ws://<hub-ip>/zigbeeLogsocket` | `{name, id, deviceId, profileId, clusterId, sourceEndpoint, destinationEndpoint, groupId, sequence, lastHopLqi, lastHopRssi, type, payload, time}` — **`lastHopLqi` (0–255) and `lastHopRssi` (dBm) of the last hop into the hub** (the repeater→hub link for a routed device) |

**Backend vs topology — two independent axes, both verified live (the load-bearing gotcha):**

- **Backend** (`zwaveJS` true/false) sets the `lwrRssi` scale — absolute dBm (negative, e.g. `-78db`) on zwaveJS vs dB *above the noise floor* (positive, e.g. `27dB`) on legacy — and whether `routeChanges` is reported (`N/A` on zwaveJS, an int on legacy). Higher RSSI is better on both; a fixed numeric cutoff does not transfer.
- **Topology** sets `neighbors` and routing: **node id ≥ 256 = Z-Wave Long Range** (a star — `neighbors:0`, a direct `01 -> <node>` route, no repeaters, dynamic power); **id ≤ 232 = classic mesh** (neighbors + multi-hop routes). Verified: a classic node and LR nodes on the *same* zwaveJS hub show `neighbors:5` vs `0`, so `neighbors:0` is LR topology, not the backend.

Field meanings and the LR-vs-mesh remediation split: `rules/zwave-zigbee-mesh.md`.

## Hub management (official — token API)

`GET /hub/advanced/getManagementToken` → token, then `/management/reboot?token=`, `/management/firmwareUpdate?token=`. The Hub Information Driver (HPM) wraps reboot/update as device commands over Maker API.
