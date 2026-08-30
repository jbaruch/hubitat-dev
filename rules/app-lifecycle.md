---
alwaysApply: true
description: Hubitat app lifecycle callbacks and the subscribe/reinitialize idiom that keeps an app working
---

# App Lifecycle

An app is not a long-running process. The hub wakes it on an event, a schedule, a UI render, install/update/uninstall, or an HTTP endpoint hit, runs one method, and sleeps.

## `definition()` metadata — how the app is filed and instantiated

`definition()` carries more than `name`/`namespace`/`author`. These metadata keys change where the app appears and how it is created, and the platform supplies a default for any you omit — so leaving one off is a silent decision, not a no-op:

- **`menu`** — the left-nav menu the app sorts into. Set it explicitly on every app.
  - Omitting it defaults to `Apps`, misfiling an event/presence-driven automation.
  - Measured values (C-8 Pro, 2.5.1.135): `Apps`, `Automations`, `Integrations`.
  - Placement: third-party network integration → `Integrations`; device / time / presence automation → `Automations`; utility, manager, or dashboard → `Apps`.
- **`menu` is not `category`** — neither implies the other. Setting only `category` still leaves the app in `Apps`.
- **`category`** — separate metadata: `Convenience`, `Safety & Security`, `Utility`, `My Apps`, `Hidden`.
- **`singleInstance: true`** — the app installs once, not repeatedly (the usual parent-app case).
- **`installOnOpen: true`** — the instance is created when its page opens, not on the first Done.
- **`parent`** — declares this a child app under a named parent (`namespace:name`), set on the child.
- `lint-review` warns only on an **absent** `menu`, never on an unrecognized value.

## Callbacks

- `installed()` — first install only.
- `updated()` — every time the user presses **Done** on an already-installed app.
- `uninstalled()` — on removal; subscriptions and schedules are auto-cleaned, so use it only for external cleanup.
- `appButtonHandler(String name)` — a `button` input was pressed.
- `hubStartupHandler()` — auto-called on hub startup (no subscription needed).

## The reinitialize idiom (mandatory)

- On the first-ever **Done**, only `installed()` runs — **not** `updated()`. An app that creates its subscriptions solely in `updated()` silently does nothing until the second Done. This is the single most common app bug.
- The fix every app follows: `installed()` calls `updated()`; `updated()` calls `unsubscribe()` then re-subscribes.

```groovy
def installed() { updated() }
def updated()   { unsubscribe(); initialize() }
def initialize(){ subscribe(motionSensor, "motion", "motionHandler") }
```

- `unsubscribe()` at the top of `updated()`/`initialize()` prevents duplicate subscriptions when the user changes a selected device. The same applies to schedules — `unschedule()` before re-scheduling, or `runIn`/`schedule` stack silently unless `overwrite` is left at its default.

## Handlers that skip initialize()

- `hubStartupHandler()` runs on hub startup **without** routing through `installed()`/`updated()`/`initialize()`. Any `state.*` value that `initialize()` sets up may be null when it fires, and reading it NPEs on boot (`cannot invoke method keySet() on null object`).
- The same trap catches any entry point the platform invokes before the first **Done** or after a state reset — `hubStartupHandler`, a subscribed event, a scheduled job.
- Do not initialize `state.*` collections in `initialize()` alone — a Map and a List both start null (the example seeds each). Put the setup in a small helper, call it from **every** entry point that reads them, or null-guard at the read site (`rules/groovy-gotchas.md`).

```groovy
private ensureState() {
    if (state.activeSince == null) state.activeSince = [:]
    if (state.stuck == null)       state.stuck = []
}
def initialize()        { ensureState(); /* subscribe … */ }
private reconcile()     { ensureState(); /* reached from hubStartupHandler(), which skips initialize() */ }
def hubStartupHandler() { reconcile() }
```

- Test it: a spec that drives the startup/handler path on a freshly-loaded, **never-initialized** instance and asserts no NPE catches this deterministically — it fails before the guard, passes after (`skills/test`).

## Mutating settings from code

- `app.updateSetting(name, value)` writes a setting; `app.removeSetting(name)` / `clearSetting(name)` drop one. All undocumented platform methods.
- `removeSetting` is **deferred** — the in-memory `settings` map still returns the old value for the rest of the current execution, so it is cleanup for the next wake, never a same-render crash-guard (`rules/groovy-gotchas.md`).

## Reader side: what an installed app actually watches

- `statusJson.eventSubscriptions[].typeId` is the only honest statement of what an installed app watches.
- Read the subscriptions, never the setting.
- A built-in app's **"use all X" toggle is a snapshot taken at the last Done**, not a live filter.
- Adding a device does not extend an existing app's coverage.
- A device added after the last Done is absent from `eventSubscriptions`, and its handler never fires.
- Coverage is correct only when the subscription count matches the device census.
- Committing an installed app's config is a **UI Done** (`rules/ui-automation.md`).
- Grounded on Hubitat Safety Monitor, whose `useAllWater = 'true'` and `hasWater = "All leak and water detectors"` both read as automatic coverage and are neither. `useAllSmoke` is assumed to behave identically and is **unverified**.

## Subscriptions & scheduling

- Handler method names are passed as **bare strings**: `subscribe(dev, "switch", "switchHandler")`, `runIn(300, "checkState")`. A typo'd or missing handler name fails quietly — see `rules/groovy-gotchas.md`.
- Handlers take one `evt` param: `evt.name`, `evt.value`, `evt.device`.
- Prefer `runIn`/`runInMillis`/`runOnce`/`schedule` (7-field Quartz cron) over any busy-wait. Parent/child app communication goes through exposed methods, never shared `state` — see `skills/_reference/endpoints.md` only for hub-side APIs, not for cross-app calls.
