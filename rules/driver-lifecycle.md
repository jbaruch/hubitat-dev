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

## Callbacks

- `installed()` — device created. `updated()` — user clicks **Save Preferences**. `uninstalled()` — cleanup.
- `initialize()` — on hub startup, only if the driver declares `capability "Initialize"`. Use it to re-establish telnet/websocket/socket connections.
- `configure()` — required by `capability "Configuration"`. `refresh()` — required by `capability "Refresh"`. `poll()` — required by `capability "Polling"`.

## parse() and sending

- `parse(String description)` receives raw inbound device data. Decode by source: Zigbee → `zigbee.parseDescriptionAsMap(description)`; Z-Wave → `zwave.parse(description, versionMap)` (returns **null** for unsupported command classes — always null-check); LAN → `parseLanMessage(description)`; MQTT → `interfaces.mqtt.parseMessage(description)`.
- Z-Wave uses multiple-dispatch `zwaveEvent(...)` overloads with a `hubitat.zwave.Command` catch-all placed **last**.
- Returning a formatted command string/List from a command method **auto-sends it** to the device — easy to trigger unintentionally. S2 devices wrap with `zwaveSecureEncap(...)`.
- First line of any protocol `parse()` while developing: `if (logEnable) log.debug "parse: ${description}"` — see `rules/logging-conventions.md`.
