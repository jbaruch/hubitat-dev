---
alwaysApply: true
description: Hubitat app lifecycle callbacks and the subscribe/reinitialize idiom that keeps an app working
---

# App Lifecycle

An app is not a long-running process. The hub wakes it on an event, a schedule, a UI render, install/update/uninstall, or an HTTP endpoint hit, runs one method, and sleeps.

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

- `hubStartupHandler()` runs on hub startup **without** routing through `installed()`/`updated()`/`initialize()`. Any `state.*` map that `initialize()` sets up may be null when it fires, and reading it NPEs on boot (`cannot invoke method keySet() on null object`).
- The same trap catches any entry point the platform invokes before the first **Done** or after a state reset — `hubStartupHandler`, a subscribed event, a scheduled job.
- Do not initialize state maps in `initialize()` alone. Put the setup in a small helper, call it from **every** entry point that reads that state, or null-guard at the read site (`rules/groovy-gotchas.md`).

```groovy
private ensureState() {
    if (state.activeSince == null) state.activeSince = [:]
    if (state.stuck == null)       state.stuck = []
}
def initialize()        { ensureState(); /* subscribe … */ }
private reconcile()     { ensureState(); /* reached from hubStartupHandler(), which skips initialize() */ }
def hubStartupHandler() { reconcile() }
```

- Test it: a spec that drives the startup/handler path on a freshly-loaded, **never-initialized** instance and asserts no NPE catches this deterministically — it fails before the guard, passes after (`rules/testing-standards.md`).

## Subscriptions & scheduling

- Handler method names are passed as **bare strings**: `subscribe(dev, "switch", "switchHandler")`, `runIn(300, "checkState")`. A typo'd or missing handler name fails quietly — see `rules/groovy-gotchas.md`.
- Handlers take one `evt` param: `evt.name`, `evt.value`, `evt.device`.
- Prefer `runIn`/`runInMillis`/`runOnce`/`schedule` (7-field Quartz cron) over any busy-wait. Parent/child app communication goes through exposed methods, never shared `state` — see `skills/_reference/endpoints.md` only for hub-side APIs, not for cross-app calls.
