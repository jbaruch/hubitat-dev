[![tessl](https://img.shields.io/endpoint?url=https%3A%2F%2Fapi.tessl.io%2Fv1%2Fbadges%2Fjbaruch%2Fhubitat-dev)](https://tessl.io/registry/jbaruch/hubitat-dev)

# hubitat-dev

Context for developing and debugging **Hubitat Elevation** apps, drivers, and the hub environment. This plugin does not write your Groovy for you — it makes an agent write it *correctly*: the sandbox constraints, lifecycle idioms, and capability contracts that the platform enforces but the docs bury, plus thin mechanisms for the deploy / log-tail / lint loop the hub gives you no official API for.

Grounded against real hardware: Hubitat C-8 Pro, platform 2.5.1.125, local network, Hub Security off. The code-editor and logging endpoints it drives are undocumented and version-sensitive — see `reference/endpoints.md` for what was verified and when.

## What it covers

- **Authoring** — apps and drivers are single Groovy 2.4 files run in a locked-down sandbox. The rules encode what that sandbox forbids and the idioms that keep an app from silently doing nothing.
- **Deploy / pull** — push source to a hub and pull it back over the same undocumented HTTP endpoints HPM and the VS Code extension use, with the `version` optimistic-concurrency token handled for you.
- **Debug** — tail the hub's `/logsocket` and `/eventsocket` websockets (structured JSON, no library needed) and read them against the code.
- **Lint** — catch the sandbox violations and silent-failure traps (bad imports, handler-name typos, capability→command gaps, the `installed()`/`updated()` first-run trap) before you paste.
- **Test** — take apps and drivers off-hub for real unit tests.

## Rules

All rules are always-on — installing the plugin means you want this context.

| Rule | Purpose |
|------|---------|
| [sandbox-constraints](rules/sandbox-constraints.md) | What the Groovy 2.4 sandbox forbids — no user classes, threads, `sleep`/`println`; the 197-class import allow-list. |
| [app-lifecycle](rules/app-lifecycle.md) | App callbacks and the `installed()`→`updated()`→`unsubscribe()` idiom that keeps an app from silently doing nothing. |
| [driver-lifecycle](rules/driver-lifecycle.md) | Driver callbacks, the capability contract (declare = must implement), and the `parse()` dispatch pattern. |
| [logging-conventions](rules/logging-conventions.md) | The `logEnable`/`txtEnable` toggles and the `runIn(1800, logsOff)` auto-disable idiom. |
| [state-vs-attributes](rules/state-vs-attributes.md) | Attributes via `sendEvent` (subscribable) vs. `state`/`atomicState` (private, JSON-serializable). |
| [groovy-gotchas](rules/groovy-gotchas.md) | Silent-failure traps the compiler misses: string handler names, `0`-is-falsy, null device inputs, reserved names. |
| [multi-hub-topology](rules/multi-hub-topology.md) | Code is per-hub-by-IP, devices can mesh; local-no-security assumption; the deploy version token. |

## Skills

| Skill | Use when |
|-------|----------|
| [scaffold](skills/scaffold/SKILL.md) | Generating a correct app or driver skeleton from declared capabilities. |

_More skills land as the plugin fills out (deploy, pull, debug, lint-review, test, hub-config)._

## Installation

```
tessl install jbaruch/hubitat-dev
```

## Hubs

Hub code operations are **per-hub by IP** (there is no mesh for code — only for devices). Hub connection details live in a `hubs.json` config the `hub-config` skill owns. Local network, no Hub Security assumed.
