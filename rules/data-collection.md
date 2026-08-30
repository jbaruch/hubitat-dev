---
alwaysApply: true
description: Collecting and analyzing sensor data on a Hubitat hub, covering device naming, event-retention buffering, the change-filtered harvest endpoint, and when a driver is the wrong answer
---

# Data Collection

The hub is an always-on host that can buffer and serve sensor data, but its defaults are tuned for live automation rather than collection. A few choices decide whether the data still exists when you go to use it.

## Name devices by function and position

- Name a device by **function and position** (`<function> - <position>`), never function alone.
- Qualify even a single-sensor unit. A second sensor on the same unit otherwise forces a rename, and a rename re-checks every app reference (`rules/device-lifecycle.md`).
- A staleness alert naming only the unit cannot say which sensor went quiet.

## Retention as a short-horizon buffer

- Event-retention defaults are display-tuned, not collection-tuned. `maxEvents` is **11 per attribute**, `maxStates` 30, `spammyThreshold` 300, and history rolls silently with no warning (`skills/_reference/endpoints.md`).
- Raise `maxEvents` via `skills/_scripts/hub_device_update.py --hub <name> --device <id> --set maxEvents=<n>` before relying on the hub for any baseline. 1000 buffers roughly two weeks of a slow-moving signal.
- Never hand-build the `POST /device/update` form.
- The script owns the field set and the boolean encoding (`rules/device-lifecycle.md`).
- The hub is always on and a laptop is not. Raise retention and harvest periodically rather than running an external poller.

## Harvesting the change-filtered event log

- `GET /device/eventsJson/<id>` is the harvest endpoint, returning stored `date`, `name`, `value` (`skills/_reference/endpoints.md`).
- A gap in the series means the value held steady, not that the device went quiet. The platform filters unchanged events (`rules/state-vs-attributes.md`).
- Derive liveness from `lastActivityTime` or battery, never from a value's presence or absence in the event log.

## When a driver is the wrong answer

- Only "the hub must act on this data live" justifies a driver. "I need to analyze this" does not.
- For analysis, fetch history from the vendor's API and join it offline. Many services serve historical observations retroactively at fine resolution.
- Installing an unmaintained community driver to obtain data an API already serves buys a permanent maintenance liability for nothing.
