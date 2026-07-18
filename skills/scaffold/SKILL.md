---
name: scaffold
description: Generate a correct Hubitat app or driver skeleton from declared capabilities, with the required lifecycle callbacks, subscription/schedule idioms, and logging conventions wired in. Use when starting a new Hubitat app or driver, or when the user asks to create/scaffold/bootstrap Hubitat Groovy code.
---

# Scaffold Skill

Process steps in order. Do not skip ahead.

The always-on Hubitat rules (`sandbox-constraints`, `app-lifecycle`, `driver-lifecycle`, `logging-conventions`, `state-vs-attributes`, `groovy-gotchas`) are already in context — apply them while generating. The capability contract lives in `skills/_reference/capabilities.json`; the import allow-list in `skills/_reference/allowed-imports.txt`.

## Step 1 — Determine what to build

Establish, asking the user only for what is not already clear:
- app or driver
- `name`, `namespace` (unique — typically the user's handle), `author`
- for a driver: the device protocol (Zigbee, Z-Wave, LAN/cloud, virtual) and the capabilities it exposes
- for an app: what it watches and what it controls

Proceed to Step 2.

## Step 2 — Resolve the capability contract (driver) or UI shape (app)

For a **driver**, for each declared capability read its required commands and attributes from `skills/_reference/capabilities.json` (`capabilities.<Name>.commands[].name`, `.attributes[].name`). Every required command becomes a Groovy method. Marker capabilities (`Actuator`, `Sensor`) add no methods.

For an **app**, decide the preference inputs (see `skills/_reference/input-types.md`) and the events to subscribe to.

Proceed to Step 3.

## Step 3 — Generate the skeleton

Emit one Groovy file wiring in, per the rules:
- **Driver**: `metadata { definition(...) { capability lines, custom command/attribute } preferences { logEnable/txtEnable } }`; `installed()`, `updated()` (with `runIn(1800, logsOff)` when logging), `logsOff()`; a method for **every** required command; `configure()`/`refresh()` if those capabilities are declared; a `parse(String description)` with the protocol-appropriate decode and a `log.debug` of raw input when a protocol is involved.
- **App**: `definition(...)`, `preferences { page { section { inputs } } }`, `installed() { updated() }`, `updated() { unsubscribe(); initialize() }`, `initialize()` with the subscriptions, and a handler method for each subscription. Guard chatty logs on `logEnable`/`txtEnable`.

Write the file to the path the user wants (default `<name>.groovy`). Proceed to Step 4.

## Step 4 — Self-check with the linter

Run the sandbox linter on the generated file and resolve anything it flags:

```
python3 .tessl/plugins/jbaruch/hubitat-dev/skills/_scripts/hub_lint.py <file.groovy>
```

Contract and finding shape: `skills/_scripts/hub_lint.py` module docstring. A correct skeleton should lint clean; if a `missing-command` or `unresolved-handler` appears, a required method or handler is absent — add it. If no findings, say so and proceed.

## Step 5 — Offer to deploy

The skeleton exists locally but is not on any hub. Offer to deploy it with `Skill(skill: "deploy")`. Finish here.
