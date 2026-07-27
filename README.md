[![tessl](https://img.shields.io/endpoint?url=https%3A%2F%2Fapi.tessl.io%2Fv1%2Fbadges%2Fjbaruch%2Fhubitat-dev)](https://tessl.io/registry/jbaruch/hubitat-dev)

# hubitat-dev

Context for developing and debugging **Hubitat Elevation** apps, drivers, and the hub environment. This plugin does not write your Groovy for you — it makes an agent write it *correctly*: the sandbox constraints, lifecycle idioms, and capability contracts that the platform enforces but the docs bury, plus thin mechanisms for the deploy / log-tail / lint loop the hub gives you no official API for.

Grounded against real hardware: Hubitat C-8 Pro, platform builds through 2.5.1.133, local network, Hub Security off. The code-editor and logging endpoints it drives are undocumented and version-sensitive — see `skills/_reference/endpoints.md` for what was verified and when.

## What it covers

- **Authoring** — apps and drivers are single Groovy 2.4 files run in a locked-down sandbox. The rules encode what that sandbox forbids and the idioms that keep an app from silently doing nothing.
- **Deploy / pull** — push source to a hub and pull it back over the same undocumented HTTP endpoints HPM and the VS Code extension use, with the `version` optimistic-concurrency token handled for you.
- **Debug** — tail the hub's `/logsocket` and `/eventsocket` websockets (structured JSON, no library needed) and read them against the code.
- **Mesh health** — read the Z-Wave/Zigbee mesh detail endpoints and flag ghost/failed nodes, packet errors, weak routes, and incomplete joins.
- **Device liveness** — rank real `lastActivity` evidence without trusting Zigbee's misleading `active` flag.
- **Live radio traffic** — tail `zwaveLogsocket` / `zigbeeLogsocket` for per-frame signal grounded in Hubitat's metrics and the Z-Wave Alliance/Silabs/IEEE 802.15.4 protocol specs.
- **Lint** — catch the sandbox violations and silent-failure traps (bad imports, handler-name typos, capability→command gaps, the `installed()`/`updated()` first-run trap) before you paste.
- **Test** — take apps and drivers off-hub for real unit tests.
- **UI automation** — for the operations the hub exposes only through its web UI (installing an app instance, configuring built-in/community apps, deleting a device or app, importing devices, reading a backup), drive it with the Playwright MCP — with the Vue/MDL selection traps and silent-failure gotchas documented so a mutation is never assumed to have stuck (`skills/_reference/playwright-ui.md`).
- **Device removal** — before deleting a device, read the hub's own "in use by" list (`/device/fullJson`) and warn with the concrete reference blast radius (enabled/disabled app switch state, dashboards, parent/child). Audit actual consumers separately through subscriptions or type-specific live state.
- **Device replacement** — capture old memberships before replacement and re-wire them onto the new device id.

## Rules

All rules are always-on — installing the plugin means you want this context.

| Rule | Purpose |
|------|---------|
| [sandbox-constraints](rules/sandbox-constraints.md) | What the Groovy 2.4 sandbox forbids — no user classes, threads, `sleep`/`println`; the 197-class import allow-list. |
| [app-lifecycle](rules/app-lifecycle.md) | App callbacks and the `installed()`→`updated()`→`unsubscribe()` idiom that keeps an app from silently doing nothing. |
| [driver-lifecycle](rules/driver-lifecycle.md) | Driver callbacks, the capability contract (declare = must implement), and the `parse()` dispatch pattern. |
| [logging-conventions](rules/logging-conventions.md) | The `logEnable`/`txtEnable` toggles and the `runIn(1800, logsOff)` auto-disable idiom. |
| [state-vs-attributes](rules/state-vs-attributes.md) | Attributes via `sendEvent` (subscribable) vs. `state`/`atomicState` (private, JSON-serializable). Why a value's timestamp can't tell you the source is alive. |
| [self-reported-vs-measured](rules/self-reported-vs-measured.md) | Distinguishing a cloud integration's measured attributes from model output computed off hand-entered config. Tell them apart by timestamp, rank suspicion by observability, and map the model with `runmethod` refresh + diff. |
| [groovy-gotchas](rules/groovy-gotchas.md) | Silent-failure traps the compiler misses: string handler names, `0`-is-falsy, null device inputs, reserved names. |
| [multi-hub-topology](rules/multi-hub-topology.md) | Code is per-hub-by-IP, devices can mesh; local-no-security assumption; the deploy version token. |
| [zwave-zigbee-mesh](rules/zwave-zigbee-mesh.md) | What the Z-Wave/Zigbee mesh metrics mean, including `listening` vs `beaming`, hex routes, the two-scale `lwrRssi` split, and `lastActivity` vs misleading `active`. |
| [ui-automation](rules/ui-automation.md) | Driving the hub web UI with Playwright for UI-only operations — the Vue/MDL selection traps, `statusJson.appSettings[]`, Room Lighting sentinel values, and verify-every-mutation. |
| [room-lighting-shades](rules/room-lighting-shades.md) | Room Lighting can group **shades**, not just lights (staff-endorsed) — `Act==Off` is a one-position preset (100=open, 0=closed), not a broken toggle; don't flag it. |
| [device-lifecycle](rules/device-lifecycle.md) | Removing a device — distinguish delete blast radius from live consumers, warn before deleting, verify after, and re-wire references onto a replacement. |
| [data-collection](rules/data-collection.md) | Using the hub to collect sensor data — name devices by function and position, raise event retention (`maxEvents` defaults to 11), harvest the change-filtered `eventsJson`, and reach for a driver only when the hub must act live. |

## Skills

| Skill | Use when |
|-------|----------|
| [scaffold](skills/scaffold/SKILL.md) | Generating a correct app or driver skeleton from declared capabilities, self-checked with the linter. |
| [deploy](skills/deploy/SKILL.md) | Pushing app/driver source to a hub and confirming it via the log stream — no browser copy-paste. |
| [debug](skills/debug/SKILL.md) | Tailing the log/event websocket, filtered, and reading it against the code to diagnose. |
| [mesh-health](skills/mesh-health/SKILL.md) | Diagnosing Z-Wave/Zigbee network problems — ghost/failed nodes, packet errors, weak routes, dead devices — from live mesh detail. |
| [lint-review](skills/lint-review/SKILL.md) | Linting Groovy for sandbox violations and silent-failure traps, then judging each finding. |
| [test](skills/test/SKILL.md) | Setting up offline unit tests (biocomp/hubitat_ci) so logic is exercised off-hub. |
| [hub-config](skills/hub-config/SKILL.md) | Managing `hubs.json` — register, list, and set the default hub (action router). |
| [device-command](skills/device-command/SKILL.md) | Running a command on a device over HTTP and confirming it landed — enumerate the real command surface, send it, and verify by re-reading the related attribute (dispatched ≠ executed). |
| [device-sequence](skills/device-sequence/SKILL.md) | Firing an ordered list of devices with a timed hold on each — walk the property and bind each photo or observation to a device id (which lamp/shade/valve/zone is which). |
| [device-removal](skills/device-removal/SKILL.md) | Safely removing a device — enumerate usage, warn on blast radius, verify after, and restore references onto a replacement. |
| [device-migration](skills/device-migration/SKILL.md) | Moving every app reference from an old device to a new one — Swap Device, a virtual bridge/parking slot, a Hub Mesh re-home across hubs, or a guided manual re-select, chosen by why the swap is blocked. |
| [sensor-onboarding](skills/sensor-onboarding/SKILL.md) | Onboarding sensors (or a fleet) with a verified step per device — pair, confirm the driver, name by function+position, raise retention, read the real preferences, mirror to a peer hub, add to inactivity monitoring, acceptance-test past the settling window, and reconcile the inventory. |

Typical loop: `scaffold` → `lint-review` → `deploy` → `debug`, with `hub-config` set up once and `test` for anything with real logic. `mesh-health` is orthogonal — reach for it when the problem is the radio network (a flaky device, a ghost node) rather than the code.

## Installation

```
tessl install jbaruch/hubitat-dev
```

## Hubs

Hub code operations are **per-hub by IP** (there is no mesh for code — only for devices). Hub connection details live in a `hubs.json` config the `hub-config` skill owns. Local network, no Hub Security assumed.
