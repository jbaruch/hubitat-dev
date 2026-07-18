[![tessl](https://img.shields.io/endpoint?url=https%3A%2F%2Fapi.tessl.io%2Fv1%2Fbadges%2Fjbaruch%2Fhubitat-dev)](https://tessl.io/registry/jbaruch/hubitat-dev)

# hubitat-dev

Context for developing and debugging **Hubitat Elevation** apps, drivers, and the hub environment. This plugin does not write your Groovy for you — it makes an agent write it *correctly*: the sandbox constraints, lifecycle idioms, and capability contracts that the platform enforces but the docs bury, plus thin mechanisms for the deploy / log-tail / lint loop the hub gives you no official API for.

Grounded against real hardware: Hubitat C-8 Pro, platform 2.5.1.125, local network, Hub Security off. The code-editor and logging endpoints it drives are undocumented and version-sensitive — see `skills/_reference/endpoints.md` for what was verified and when.

## What it covers

- **Authoring** — apps and drivers are single Groovy 2.4 files run in a locked-down sandbox. The rules encode what that sandbox forbids and the idioms that keep an app from silently doing nothing.
- **Deploy / pull** — push source to a hub and pull it back over the same undocumented HTTP endpoints HPM and the VS Code extension use, with the `version` optimistic-concurrency token handled for you.
- **Debug** — tail the hub's `/logsocket` and `/eventsocket` websockets (structured JSON, no library needed) and read them against the code.
- **Mesh health** — read the Z-Wave/Zigbee mesh detail endpoints and flag ghost/failed nodes, packet errors, weak routes, and dead devices, then tail the live radio log sockets (`zwaveLogsocket`/`zigbeeLogsocket`) for per-frame signal (Zigbee LQI/RSSI, Z-Wave per-frame RSSI) — grounded in Hubitat's metrics and the Z-Wave Alliance/Silabs/IEEE 802.15.4 protocol specs.
- **Lint** — catch the sandbox violations and silent-failure traps (bad imports, handler-name typos, capability→command gaps, the `installed()`/`updated()` first-run trap) before you paste.
- **Test** — take apps and drivers off-hub for real unit tests.
- **UI automation** — for the operations the hub exposes only through its web UI (installing an app instance, configuring built-in/community apps, deleting a device or app, importing devices, reading a backup), drive it with the Playwright MCP — with the Vue/MDL selection traps and silent-failure gotchas documented so a mutation is never assumed to have stuck (`skills/_reference/playwright-ui.md`).
- **Device removal** — before deleting a device, read the hub's own "in use by" list (`/device/fullJson`), warn with the concrete blast radius (enabled automations vs inert references, dashboards, parent/child), and verify the references cleared after. A replacement device gets a new id, so capture the old memberships and re-wire them onto the new one.

## Rules

All rules are always-on — installing the plugin means you want this context.

| Rule | Purpose |
|------|---------|
| [sandbox-constraints](rules/sandbox-constraints.md) | What the Groovy 2.4 sandbox forbids — no user classes, threads, `sleep`/`println`; the 197-class import allow-list. |
| [app-lifecycle](rules/app-lifecycle.md) | App callbacks and the `installed()`→`updated()`→`unsubscribe()` idiom that keeps an app from silently doing nothing. |
| [driver-lifecycle](rules/driver-lifecycle.md) | Driver callbacks, the capability contract (declare = must implement), and the `parse()` dispatch pattern. |
| [logging-conventions](rules/logging-conventions.md) | The `logEnable`/`txtEnable` toggles and the `runIn(1800, logsOff)` auto-disable idiom. |
| [state-vs-attributes](rules/state-vs-attributes.md) | Attributes via `sendEvent` (subscribable) vs. `state`/`atomicState` (private, JSON-serializable). Why a value's timestamp can't tell you the source is alive. |
| [groovy-gotchas](rules/groovy-gotchas.md) | Silent-failure traps the compiler misses: string handler names, `0`-is-falsy, null device inputs, reserved names. |
| [multi-hub-topology](rules/multi-hub-topology.md) | Code is per-hub-by-IP, devices can mesh; local-no-security assumption; the deploy version token. |
| [zwave-zigbee-mesh](rules/zwave-zigbee-mesh.md) | What the Z-Wave/Zigbee mesh metrics mean, the two-scale `lwrRssi` backend trap, and what counts as a real problem. |
| [ui-automation](rules/ui-automation.md) | Driving the hub web UI with Playwright for UI-only operations — the Vue/MDL selection traps, `statusJson` blind spot, Room Lighting recapture, and verify-every-mutation. |
| [device-lifecycle](rules/device-lifecycle.md) | Removing a device — enumerate its usage and warn before deleting, verify after, and re-wire references onto a replacement (which gets a new id) — swapping before re-selecting by hand. |

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
| [device-removal](skills/device-removal/SKILL.md) | Safely removing a device — enumerate usage, warn on blast radius, verify after, and restore references onto a replacement. |
| [device-migration](skills/device-migration/SKILL.md) | Moving every app reference from an old device to a new one — Swap Device, a virtual bridge/parking slot, a Hub-Mesh re-home across hubs, or a guided manual re-select, chosen by why the swap is blocked. |

Typical loop: `scaffold` → `lint-review` → `deploy` → `debug`, with `hub-config` set up once and `test` for anything with real logic. `mesh-health` is orthogonal — reach for it when the problem is the radio network (a flaky device, a ghost node) rather than the code.

## Installation

```
tessl install jbaruch/hubitat-dev
```

## Hubs

Hub code operations are **per-hub by IP** (there is no mesh for code — only for devices). Hub connection details live in a `hubs.json` config the `hub-config` skill owns. Local network, no Hub Security assumed.
