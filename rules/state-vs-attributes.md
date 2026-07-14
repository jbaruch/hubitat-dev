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

## state / atomicState (internal, private)

- `state` is a Map-like store for the app/driver's own data between wakes, serialized to/from JSON. `state.foo = "bar"`.
- Only JSON-serializable data survives. Storing a `DeviceWrapper`, a closure, or other live objects in `state` breaks — keep device references out of `state`.
- `state` writes just before the instance sleeps. `atomicState` commits immediately — use it only when overlapping executions can race, and prefer `singleThreaded: true` in `definition` as the cheaper alternative.
- `state` is serialized every execution; don't store large blobs there.

## The test

- "Would something else want to react to this value?" → attribute via `sendEvent`.
- "Is this my own bookkeeping?" → `state`.
