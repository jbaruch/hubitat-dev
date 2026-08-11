---
name: mcp-connector
description: Use the hub's first-party AI (MCP) Connector Integration to read hub state and drive devices over its local MCP endpoint — connect with the bearer token, discover the live tool surface, prefer read-only tools, gate sensitive actions, and verify the mutation. Use when the user wants to control or query a Hubitat hub through its MCP server / AI connector, or asks whether an operation should go through MCP or the hub_* scripts.
argument-hint: "[list-tools|call <tool>] [--hub <name>|--ip <addr>]"
---

# MCP Connector Skill

Process steps in order. Do not skip ahead.

The connector is a **runtime control + inventory + events** surface, distinct from the code/mesh/lint
tooling. Endpoint, transport, auth, the full tool catalog, and the when-to-prefer-MCP decision live in
`skills/_reference/mcp-connector.md`; this skill runs the client and applies judgment.

## Step 1 — Confirm the endpoint and token

The connector listens on `http://<hub-ip>/mcp` (port **80**, not `:8080`) and needs a **bearer token**
(enable the built-in app and read the URL/token from its UI — see the reference). The token is a
secret: it comes from `$HUBITAT_MCP_TOKEN` or a gitignored `--token-file`, never a CLI literal, never
logged (`rules/no-secrets.md`). Proceed to Step 2.

## Step 2 — Discover the live tool surface

Do not assume the tool set from the reference snapshot. List it live:

```
python3 .tessl/plugins/jbaruch/hubitat-dev/skills/_scripts/hub_mcp.py list-tools --hub <name>
```

Argument/output contract: `skills/_scripts/hub_mcp.py` module docstring. Add `--schemas` for input
schemas, `--ip <addr>` or `--url http://<ip>/mcp` instead of `--hub`. If the call returns `401`, the
token was rejected — fix the token before continuing. Proceed to Step 3.

## Step 3 — Choose MCP or the hub_* scripts

Decide per task (full rule in the reference, When to prefer MCP vs the grounded HTTP path):
- **MCP** for device/room/scene/mode control, running a rule, and intent-shaped inventory/event reads.
- **`hub_*` scripts / raw endpoints** for deploy, pull, lint, mesh detail, firmware, a **token-free**
  path on a Hub-Security-off hub, or any field the tools do not expose — hand off to `Skill(skill: "deploy")`,
  `Skill(skill: "lint-review")`, `Skill(skill: "mesh-health")`, `Skill(skill: "device-command")` as fits.

If the task belongs to a hub_* skill, hand off now and finish here. Otherwise proceed to Step 4.

## Step 4 — Prefer read-only tools

Reach for read-only tools first. The client unwraps the double-encoded `content[].text` into
structured data. If the whole task is a read, report the result and finish here. If it needs a
control action, proceed to Step 5.

## Step 5 — Resolve the target device or scene

When the user named a device or scene ambiguously, resolve it to an id before acting rather than
guessing:

```
python3 .tessl/plugins/jbaruch/hubitat-dev/skills/_scripts/hub_mcp.py call hubitat_search_devices --args '{"query":"patio"}' --hub <name>
```

Confirm the match against the returned id/label. Then read the selected tool's **live description and
schema** (from Step 2, `list-tools --schemas`) to decide sensitivity — the gateway's own metadata is
the authority, and the set of sensitive tools drifts with the app, so never judge from a memorized
list. If the tool requires `allowSensitive`, proceed to Step 6; otherwise proceed to Step 7.

## Step 6 — Gate a sensitive action

A sensitive action is a physical side effect, so it needs authorization before firing. An explicit
request that names the action (lock this door, set this thermostat) **is** that authorization — do not
pause to re-confirm; it takes `--allow-sensitive`, and you proceed immediately to Step 7. Ask one
clarifying question only when the intent is genuinely ambiguous (which device, on or off, act at all),
then proceed to Step 7.

## Step 7 — Fire the control action

Send the control tool with the resolved id/name (adding `--allow-sensitive` for a sensitive action
confirmed in Step 6):

```
python3 .tessl/plugins/jbaruch/hubitat-dev/skills/_scripts/hub_mcp.py call hubitat_lock --args '{"device":"Front Door"}' --allow-sensitive --hub <name>
```

`isError:true` in the result is the tool reporting a failure (bad args, guard, device miss) — surface
it, do not treat a printed result as success. Proceed to Step 8.

## Step 8 — Verify the mutation

A returned result is dispatch, not proof the device moved — the same trap as `/device/runmethod`
(`rules/state-vs-attributes.md`). Re-read the affected device with `get_device_state` (or
`list_device_events`) and confirm the relevant attribute changed. Report what changed and what did
not; an already-in-state no-op is not a failure. Finish here.
