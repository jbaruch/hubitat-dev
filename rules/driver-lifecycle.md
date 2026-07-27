---
alwaysApply: true
description: Hubitat driver lifecycle, the capability contract, and the parse() dispatch pattern
---

# Driver Lifecycle

A driver is a single Groovy file with a `metadata { definition(...); preferences {...} }` block plus top-level methods. It is the layer apps and users talk to a device through.

## The capability contract

- Declaring `capability "X"` is a **promise to implement every command X requires**, as a Groovy method with the matching name and parameters. `capability "Switch"` obliges `on()` and `off()`; `capability "SwitchLevel"` obliges `setLevel(level, duration)`.
- The authoritative capability → attributes/commands mapping is `skills/_reference/capabilities.json`. A declared capability with a missing command method is a real defect the `lint-review` skill flags.
- Marker capabilities (`Actuator`, `Sensor`) carry no commands — they only classify a device as controllable vs. reporting.
- Custom `command`/`attribute` declarations go inside `definition`. Attributes surface as **Current States**; update them with `sendEvent` (see `rules/state-vs-attributes.md`).
- `GET /device/fullJson/<id>` → `commands[]` is the authoritative **per-device** command list — each `{name, parameters:[{type, defaultValue}], relatedAttribute, capability:<bool>}`, with `capability:false` marking a driver custom command. Read it instead of inferring commands from the declared capability, which misses every custom command (`skills/_reference/endpoints.md`).

## Callbacks

- `installed()` — device created. `updated()` — user clicks **Save Preferences**. `uninstalled()` — cleanup.
- `initialize()` — on hub startup, only if the driver declares `capability "Initialize"`. Use it to re-establish telnet/websocket/socket connections.
- `configure()` — required by `capability "Configuration"`. `refresh()` — required by `capability "Refresh"`. `poll()` — required by `capability "Polling"`.
- `capability "Configuration"` declares that a `configure()` method **exists** — never what it configures. Whether it reaches Zigbee attribute reporting is not discoverable from the declaration.
- A driver's real feature surface is its `preferences`, readable as `settings` in `fullJson`. A built-in driver is frequently **narrower than the hardware** — a sensor advertising a 1–240-minute reporting interval may expose only `logEnable` / `txtEnable`. Read `settings` before designing around any vendor-advertised configurable (`skills/_reference/endpoints.md`).

## parse() and sending

- `parse(String description)` receives raw inbound device data. Decode by source: Zigbee → `zigbee.parseDescriptionAsMap(description)`; Z-Wave → `zwave.parse(description, versionMap)` (returns **null** for unsupported command classes — always null-check); LAN → `parseLanMessage(description)`; MQTT → `interfaces.mqtt.parseMessage(description)`.
- Z-Wave uses multiple-dispatch `zwaveEvent(...)` overloads with a `hubitat.zwave.Command` catch-all placed **last**.
- Returning a formatted command string/List from a command method **auto-sends it** to the device — easy to trigger unintentionally. S2 devices wrap with `zwaveSecureEncap(...)`.
- First line of any protocol `parse()` while developing: `if (logEnable) log.debug "parse: ${description}"` — see `rules/logging-conventions.md`.

## App-driven virtual devices

- The built-in `hubitat` **`Virtual Motion Sensor`** is unsafe for app-driven latched state: its `active()` **auto-reverts to `inactive` on a hardcoded ~15s timer**. An app that drives a virtual motion device to hold an aggregate state, such as a zone controller, cannot keep it `active` with the built-in.
- Verified 2.5.1.131: a direct `active()` from the device page emitted `inactive` exactly 15s later, and an app-created child device renders no `autoInactive` preference on its edit page to disable it. A dead-consistent ~15s active duration in the event history is the signature.
- For an app-owned "hold until commanded" device, write a trivial custom driver — `capability "MotionSensor"` plus `active`/`inactive` commands that only `sendEvent`, no timer.
- Harden the owning app to self-heal: re-assert the device to the intended state whenever it diverges, not only on a transition. An out-of-band command or a hub restart then re-syncs rather than latching.
- Swap an already-created device onto the custom driver in place — the swap keeps the device id and every app reference (`skills/_reference/playwright-ui.md` gotcha 25).
