# Hubitat AI (MCP) Connector Integration (grounded)

The **first-party, built-in** `AI (MCP) Connector Integration` app turns the hub into a local
Model Context Protocol server, so an MCP-speaking agent can read hub state and drive devices without
the Maker API or the undocumented editor endpoints. This is a **different surface** from
`skills/_reference/endpoints.md`: it is an official app with a stable-ish JSON-RPC tool contract, not
a version-sniffed HTTP endpoint.

Grounded live **2026-08-10** on one **C-8 Pro**, platform **2.5.1.140**, local network, against
`http://<hub-ip>/mcp`. Server `hubitat-mcp-gateway 0.1.0`, MCP protocol **2025-06-18**. The app is
new and lightly documented (docs2.hubitat.com/en/apps/ai-mcp-connector-integration) — the tool
surface below was read from the live server, not the docs. Re-ground after a platform or app update:
the tool set is versioned by the app, not by the hub firmware.

## Enabling it

- Hub sidebar → **Integrations** → **Add Built-In Integration** → **AI (MCP) Connector Integration**.
- First run shows **Integration is NOT running — enable**; click **enable** to start it.
- The app UI then shows the **local MCP server URL** and per-agent config snippets, plus the **bearer
  token**.
- **Local only.** Cloud agents are not supported at this time — only the agent needs to be reachable
  on the local network. This matches the plugin's own scope (operator's own hub, local network).

## Endpoint, transport, session

| Property | Value |
|---|---|
| URL | `http://<hub-ip>/mcp` — port **80**, path `/mcp` (**not** the `:8080` admin UI) |
| Transport | **Streamable HTTP** — JSON-RPC 2.0 over `POST /mcp` |
| Event stream | `GET /mcp` with `Accept: text/event-stream` — SSE for `notifications/resources/updated` (resource subscriptions; `eventBufferSize` 512, incremental reads) |
| Protocol | `2025-06-18` |
| Session | `initialize` returns an `Mcp-Session-Id` **response header** (`Access-Control-Expose-Headers: Mcp-Session-Id`); echo it on every following request |
| Handshake | `initialize` → `notifications/initialized` (notification, no id) → `tools/list` / `tools/call` |

A `POST` response comes back as plain `application/json` on the calls measured; the `Accept` header
still offers `text/event-stream`, and the SSE framing (`data: {…}`) is what the resource stream uses —
parse for both shapes.

## Authentication — the token is a secret

- `Authorization: Bearer <token>`. Unauthenticated → **`401`** `{"error":"unauthorized"}` with
  `WWW-Authenticate: Bearer` (both GET and POST).
- The token is **password-grade** — it grants full hub access. Read it from `$HUBITAT_MCP_TOKEN` or a
  gitignored file; never a CLI literal, never committed, echoed, or logged (coding-policy `rules/no-secrets.md`).
- Rotate via the app's **reset authorization token** link; every configured agent must be updated
  after a reset.

## The response-shape trap — `content[].text` is double-encoded

A `tools/call` result is `{content:[{type:"text", text:"…"}], isError:<bool>}`, and each `text` part is
**itself a JSON document string**, not an object. Read it and parse again:

```
result.content[0].text = "{\"currentMode\":{\"id\":2,\"name\":\"Evening\"}}"   # a string, parse it
```

`skills/_scripts/hub_mcp.py` unwraps this (single JSON text part → parsed object). `isError:true` is
the **tool** reporting a failure (bad args, a guard, a device miss) — distinct from a JSON-RPC
`error` object (protocol failure).

## The sensitive-action guard — `allowSensitive: true`

Some tools refuse unless the arguments carry `allowSensitive: true`. **Which tools require it is owned
by the gateway and drifts with the app** — read each tool's own live description/schema
(`hub_mcp.py list-tools --schemas`) to see whether it needs the guard, rather than any hard-coded list
here or in the skill (coding-policy `rules/script-as-black-box.md`). `hub_mcp.py --allow-sensitive` merges the flag.

## Reads paginate

Large read tools page with `pageSize` + `cursor` (follow `nextCursor`); `limit`/`max` are **deprecated
aliases**. `includeAttributes` / `attributeNames` default off to keep responses compact — opt in when
you need attribute values. The "search"/lock/scene tools accept several **alias** identifier params
(`deviceId` / `device` / `name` / `query`) and show `required: []` even though one identifier is needed —
verify you passed one.

## Tool surface — 44 tools (grounded 2.5.1.140, `hubitat-mcp-gateway 0.1.0`)

Read the live list — including which tools require `allowSensitive` — with
`hub_mcp.py list-tools --schemas`. This is the 2026-08-10 snapshot of the tool names and categories only.

**Context & search**
- `hubitat_get_context_summary` — plain-text live hub summary for broad questions
- `hubitat_get_live_context` — filtered, paginated Home-Assistant-Assist-style snapshot
- `hubitat_search_devices` — search by name/id/room/capability/type (use before acting on an ambiguous name)

**High-level control (Hass-style)**
- `hubitat_turn_on` · `hubitat_turn_off` · `hubitat_toggle`
- `hubitat_light_set` (on/off, level, CT, hue, sat, color) · `hubitat_room_turn_on` · `hubitat_room_turn_off` · `hubitat_room_light_set`
- `hubitat_cover_set_position` · `hubitat_cover_open` · `hubitat_cover_close` · `hubitat_cover_stop`
- `hubitat_lock` · `hubitat_unlock`
- `hubitat_thermostat_set_temperature` · `hubitat_thermostat_set_mode` · `hubitat_thermostat_set_fan_mode`
- `hubitat_fan_set_speed` · `hubitat_room_fan_on` · `hubitat_room_fan_off` · `hubitat_room_fan_set_speed`
- `hubitat_press_button`
- `hubitat_set_device_disabled` · `hubitat_set_app_disabled` · `hubitat_set_mode`

**Inventory & state (read-only)**
- `list_devices` · `list_devices_by_capability` · `get_device` · `get_device_state`
- `list_rooms` · `list_modes`

**Events (read-only)**
- `list_events` · `list_device_events` · `list_hub_events` · `list_location_events`

**Apps, rules, scenes**
- `list_apps` · `list_actions` · `hubitat_run_action` (runs a Rule/Button/Visual-Rule action — only on explicit request)
- `list_scenes` · `hubitat_activate_scene`

**Diagnostics & escape hatch**
- `get_hub_diagnostics` — gateway + hub **health** (`freeOSMemoryKb`, `cpu5MinuteAverage`, Java heap); **no network / mDNS / IP** data
- `run_device_command` — run any advertised device command (a sensitive command needs `allowSensitive`)

## What it does NOT cover — reach for the `hub_*` scripts instead

The connector is a **runtime control + inventory + events** surface. It has **no** tools for:

- **Code deploy / pull** (app/driver source) → `skills/deploy`, `hubclient.py`
- **Linting** Groovy → `skills/lint-review`, `hub_lint.py`
- **Z-Wave / Zigbee mesh detail** (routes, RSSI, ghost nodes) → `skills/mesh-health`, `hub_mesh.py`
- **Firmware update** → `skills/firmware-update`
- **Networking / mDNS / discovery** — nothing exposes it; `get_hub_diagnostics` is memory/CPU only

## When to prefer MCP vs the grounded HTTP path

- **Prefer MCP** when a token is configured and the task is device control, room/scene/mode changes,
  running a rule, or a quick inventory/event read stated as intent — the tools are higher-level and
  self-describing, and `hubitat_search_devices` resolves an ambiguous name in one call.
- **Prefer the `hub_*` scripts / raw endpoints** for anything dev-facing (deploy, pull, lint, mesh,
  firmware), for a **token-free** path on a Hub-Security-off hub (the undocumented endpoints need no
  auth; MCP always needs the bearer token), and whenever you need a field the tools do not expose.
- Both share the plugin's disciplines: a returned success is dispatch, not proof — verify device
  moves via `get_device_state` / `list_device_events`, the MCP analog of the `runmethod`
  dispatched-≠-executed rule (`rules/state-vs-attributes.md`).

## Client contract

`skills/_scripts/hub_mcp.py` speaks the handshake, session, token hygiene, and content-unwrap so a
caller does not hand-roll JSON-RPC. Argument/output contract and the grounded facts above live in its
module docstring; actions are `list-tools`, `initialize`, and `call <tool> --args '<json>'`
(`--allow-sensitive` to merge the guard). Token from `$HUBITAT_MCP_TOKEN` or `--token-file`.

## Grounding

One C-8 Pro, 2.5.1.140, local network, 2026-08-10. `initialize` (protocol 2025-06-18, server
`hubitat-mcp-gateway 0.1.0`, session header), `tools/list` (44 tools, no pagination), and read-only
`tools/call` on `get_hub_diagnostics` and `list_modes` were verified live; the double-encoded
`content[].text`, the `401` unauth shape, and the `allowSensitive` guard were confirmed against the
running server. Sensitive-write tools were **not** fired during grounding.
