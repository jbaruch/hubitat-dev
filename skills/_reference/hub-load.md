# Hub Load (grounded)

Diagnosing a Hubitat hub's load, and why every obvious reading misleads. Grounded **2026-08-13**
across three C-8 Pro hubs on the same platform build, with the Hub Information Driver v3 installed
on each.

The headline result is a negative one, and it is the load-bearing part: on a hub carrying 3× the
load of its sibling there was **no runaway app**. Start an investigation from that premise, not
from "find the expensive automation".

## `cpu5Min` is a load average, not a percent

- `cpu5Min` is a **Unix load average**. On a quad-core C-8 Pro, **`4.0` is 100% of the box**.
- Hub Information Driver v3 reads column 3 ("5m CPU avg") of `freeOSMemoryLast` into `cpu5Min` and
  derives `cpuPct = (load / 4.0) * 100`. Nothing on the hub reports load as a percent on its own.
- So a `severeLoad` trip at **~2.1 is ~52% of the box** — not 2.1%, and not "one core busy".
- **Never compare a `severeLoad` value against `cpu5Min` — they are different windows.** One
  `severeLoad` event reported `2.17` while the `freeOSMemoryHistory` row stamped the same second
  read `1.5`. Reading a spike gauge against a 5-minute average is how "zero headroom" gets
  mis-concluded.

## Two routes worth naming

| Route | Shape |
|-------|-------|
| `GET /logs/eventsJson` | `severeLoad` is a **LOCATION event** and its `value` is the tripping load average. It is **not** in `/hub/eventsJson` |
| `GET /hub/advanced/freeOSMemoryHistory` | The hub's own CSV series: `Date/time, Free OS, 5m CPU avg, Total Java, Free Java, Direct Java`, ~5 min cadence, **reset on reboot**. `…/freeOSMemoryLast` is the latest row |

`freeOSMemoryHistory` is the load-average source that needs no driver installed. Sampling
`severeLoad` from `/logs/eventsJson` is how the ~2.1 threshold was established (observed trips:
2.11, 2.17, 2.21, 2.28, 2.32, 2.38).

**There is no `appStats` / `deviceStats` HTTP endpoint.** Those tabs are websocket-fed, so per-app
attribution needs the browser (`skills/_reference/playwright-ui.md`).

## Load scales with device count

| hub | apps | devices | 5m load | db MB | load/device |
|--|--|--|--|--|--|
| apps | 37 | 500 | 1.59 | 78 | 0.0032 |
| devices | 9 | 167 | 0.55 | 75 | 0.0033 |
| bits | 6 | 29 | 0.07 | 10 | 0.0024 |

Per-device cost on the loaded hub is within 3% of the healthy control. Five hypotheses were killed
with measurements — recorded so they are not re-run:

- **App execution** — all apps ≈ 3% of the 4-core box, all drivers ≈ 1%. So ~90% of the load is
  platform-internal.
- **Database size** — 78 vs 75 MB while load differs 3×.
- **Heap / GC** — load vs heap-used% `r = +0.06`.
- **Event volume** — the calmer hub processes *more* events at a third the load.
- **Orphaned mesh mirrors** — 158 mirrors, all used by ≥2 apps, zero orphans.

Caveat: `n = 3`, and device count correlates with app count on these hubs, so the two are not
separated by this data.

## The self-sustaining reboot loop

A post-update reboot's boot burst crosses the `severeLoad` threshold at **boot + 14 min, every
time**. A rule that reboots the hub on `severeLoad` therefore feeds the condition it reacts to —
4 cycles in 90 minutes, observed.

- Guard it with a Required Expression on hub uptime **plus Evaluate Required Expression at System
  Startup**. Without `evalOnBoot`, Rule Machine can carry a stale TRUE across the reboot and miss
  the one boot the guard exists for.
- Break-glass is disabling the **rebooter**, not the alerting rule — the signal survives.
- An RM rule that reads correct is not a rule that runs; verify it at `eventSubscriptions`
  (`rules/ui-automation.md`).

## Finding hub endpoints without guessing

Hub Information Driver v3 enumerates **27 grounded hub endpoints** in its `path:` literals —
`databaseSize`, `maxEventAgeDays`, `maxDeviceStateAgeDays`, `stateCompressionStatus`,
`internalTempCelsius`, `rebuildDatabaseAndReboot`, `zigbee/healthStatus`, `zwave/healthStatus` and
more. Pull the driver source with `skills/_scripts/hub_pull.py` and grep it rather than probing
paths. Guessing a path is not free on every route — see the `configure/json` note in
`skills/_reference/endpoints.md`.
