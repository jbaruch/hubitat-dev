# Hubitat Hub Endpoints (grounded)

Verified 2026-07-14 against three **C-8 Pro** hubs on platform **2.5.1.125**, local network, **Hub Security off**. These endpoints are **undocumented and version-sensitive** — Hubitat does not support them and they can shift between firmware releases. Only the Maker API and the `/management/*` token API are officially supported. Re-verify after a platform update; the `_meta.verified_platform` in `reference/capabilities.json` tracks the baseline.

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

## Hub info & identity

| Endpoint | Returns |
|----------|---------|
| `GET /hub/details/json` | Hub identity: `platformVersion`, `hardwareVersion`, `hubName`, `hubUID`, `ipAddress`, `macAddress`, `timeZone`, ... (confirmed ~49 KB on 2.5.1.125) |
| `GET /hub2/hubData` | Newer JSON hub backend |
| `GET /hub2/devicesList` | Devices: `{devices:[{key, data:{id, name, ...}}]}` |
| `GET /hub2/appsList` | Installed apps + `systemAppTypes` |

## Device control (official — Maker API)

Prefer Maker API for exercising devices in a test loop. Local: `http://<hub-ip>/apps/api/<makerAppId>/<path>?access_token=<token>`. Key paths: `/devices` (list), `/devices/all` (full JSON: capabilities, attributes, commands), `/devices/<id>`, `/devices/<id>/<command>/<secondaryValue>` (send command), `/devices/<id>/events`. Multi-hub note: with hubs meshed, one Maker API instance can expose devices from secondary hubs too — but **code** endpoints are per-hub and have no mesh.

## Hub management (official — token API)

`GET /hub/advanced/getManagementToken` → token, then `/management/reboot?token=`, `/management/firmwareUpdate?token=`. The Hub Information Driver (HPM) wraps reboot/update as device commands over Maker API.
