---
alwaysApply: true
description: When to use attributes/sendEvent vs state/atomicState in Hubitat apps and drivers
---

# State vs. Attributes

Hubitat has two distinct persistence mechanisms. Choosing the wrong one is a design bug that surfaces later as "my automation never fires" or "my data vanished".

## Attributes (external, subscribable)

- An attribute is device-facing state shown as **Current States** on the device page. Update it by generating an event: `sendEvent(name: "switch", value: "on", descriptionText: "${device.displayName} switch is on")`.
- Use an attribute whenever an app or rule might subscribe to the value changing (switch, temperature, contact). Changing it fires an event; that event is the whole point.
- By default the platform filters events whose value did not change. Force one with `isStateChange: true`. `type` is `"physical"` (user acted on the device) or `"digital"` (hub commanded it).
- `createEvent(...)` builds an event map without sending — used when returning events from `parse()`.

## Reader side: a value's timestamp is not a liveness signal

- `currentState(attr).date` dates the last **change** to the value, never the last report from the source. A healthy source reporting a steady value emits nothing and is indistinguishable from a dead one.
- Never derive liveness or freshness from a value attribute's timestamp (`temperature`, `humidity`, `thermostatOperatingState`). Its staleness measures the world's volatility, not the path's health. Raising the threshold trades detection window against false alerts; no threshold makes the wrong signal right.
- Derive liveness from a **monotonic** attribute — always advances, never change-filtered (`thermostatTime`, `runtimeUpdated`, `sensorsUpdated`). Strongest form: stamp your own clock on each verified read, monotonic by construction.
- An attribute *named* like a liveness signal is not one. An app's `lastPoll` whose value is a constant string like `Succeeded` is change-filtered like any value, and goes arbitrarily stale on a healthy integration.
- `isStateChange: true` is the writer-side counterpart: set it when a consumer must see every report, not only changes.
- `GET /device/eventsJson/<deviceId>` (`skills/_reference/endpoints.md`) is how you measure an attribute's real gap distribution before trusting it.
- Over HTTP both stores live in `GET /device/fullJson/<id>` and are easy to read backwards: Groovy `state` is top-level **`deviceState`**, attributes are nested **`device.currentStates`** (`skills/_reference/endpoints.md`).
- `currentStates[attr].date` in that payload is this trap made concrete — it sits beside `value`, reads like a freshness stamp, and is the last **change**, not the last report.

## Reader side: a value space can mix a reading with a flag

- An attribute may carry a warning code in the same slot as its measurement. Z-Wave's Battery command class encodes "battery low" as level `0xFF`, and drivers flatten it into the same `battery` attribute the percentage uses.
- The signature is a pair of events inside one wakeup — a plausible level, then an implausible one a few hundred milliseconds later.
- Read the pair, never either value alone. Two disagreeing values that close together are one report.
- An implausible value there is neither a percentage nor noise to filter out. It is the device asserting a condition.
- Confirm such a value; never dismiss it as a decoding glitch.
- Confirm against the raw frame rather than the attribute: force a wakeup with the device's Z-Wave button and read `[BatteryCCReport] level: N` in `skills/_scripts/hub_radiolog.py --radio zwave`.
- A sleepy sensor reports battery only on a wakeup. Tamper does not trigger one.
- The hub issues a `BatteryCCGet` on wake and the report lands within ~200 ms — a seconds-long check, not a wait for the wakeup interval.
- A flag present on some wakeups and absent on others is diagnostic, not noise. A cell at the trip threshold sags below it under transmit load and recovers at rest.
- A lithium primary reads near its nominal voltage from new until it collapses.
- Never treat a resting multimeter reading as refuting a low-battery flag. Swap in a known-good cell instead.
- Describe the symptom, never one encoding — a device may assert the same condition over the Power Management notification class instead of `0xFF`.

## state / atomicState (internal, private)

- `state` is a Map-like store for the app/driver's own data between wakes, serialized to/from JSON. `state.foo = "bar"`.
- Only JSON-serializable data survives. Storing a `DeviceWrapper`, a closure, or other live objects in `state` breaks — keep device references out of `state`.
- `state` writes just before the instance sleeps. `atomicState` commits immediately — use it only when overlapping executions can race, and prefer `singleThreaded: true` in `definition` as the cheaper alternative.
- `state` is serialized every execution; don't store large blobs there.

## The test

- "Would something else want to react to this value?" → attribute via `sendEvent`.
- "Is this my own bookkeeping?" → `state`.
