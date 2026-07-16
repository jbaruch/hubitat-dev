---
name: debug
description: Tail a Hubitat hub's live log or event websocket, filtered, and interpret it against the code to diagnose an app or driver. Use when the user wants to debug, watch logs, tail the log stream, see live events, or figure out why a Hubitat app/driver misbehaves.
---

# Debug Skill

Process steps in order. Do not skip ahead.

Hubitat has no debugger — diagnosis is `log.debug` plus the live stream (`logging-conventions` rule). This skill tails that stream and reads it against the source.

## Step 1 — Frame the question

Read `reference/endpoints.md` before probing for any endpoint.

Establish what is wrong and which app/driver it concerns, and pick the socket:
- `logsocket` — the debug/info/warn/error log lines (default; use for "my code does X wrong").
- `eventsocket` — device attribute events (use for "the attribute isn't updating / the event isn't firing").

For a failure that already happened, reach for history instead of a live tail: `/device/eventsJson/<id>` (when an attribute actually moved, and which app issued a command) and `/hub/eventsJson` (whether a platform update landed near the onset).

Have the source in hand so the log can be read against it. Proceed to Step 2.

## Step 2 — Tail, filtered

```
python3 .tessl/plugins/jbaruch/hubitat-dev/scripts/hub_logtail.py --ip <addr> \
    [--socket logsocket|eventsocket] [--name "<name>"] [--min-level debug] [--seconds N | --follow]
```

Argument and output contract, filter semantics, and frame shape: `scripts/hub_logtail.py` module docstring, and `reference/endpoints.md` for the raw frame fields. Filter tightly — `--name` to the app/driver under test — so the stream is readable. Default is a bounded window; use `--follow` only when the user will actively trigger the behavior.

Proceed to Step 3.

## Step 3 — Trigger the behavior

While tailing, have the behavior exercised (press the device command, fire the app trigger, wait for the schedule). If the code lacks a log line at the point of doubt, add a guarded `log.debug` there (`if (logEnable) log.debug "..."`), redeploy with `Skill(skill: "deploy")`, and tail again. Proceed to Step 4.

## Step 4 — Read the frames against the source

A missing expected line means the branch wasn't reached; a `groovy.lang.MissingMethodException` or null error names the failing call. Cross-reference the `groovy-gotchas` and lifecycle rules — a handler that never logs is often the string-name, the first-run-`installed()`, or the 2.5.1 `e.statusCode` trap, where the NPE escapes the `catch` and kills the recovery below it.

**A status is not a fact** — for radios, see `rules/zwave-zigbee-mesh.md`:
- **Prefer timestamps to indicators.** Read `lastPollDate`, `authTokenExpires`, an event `date`. Treat an app's own status label as cached state, never as a probe — it can read a green "Online" through a multi-day outage.
- **Verify an action by re-reading the thing it changed** (`/device/fullJson/<id>`, `/device/eventsJson/<id>`), never by the app's own "I did X" log line. That line fires when the command is **sent**, not when it lands.
- **Compare a frozen attribute's event `date`, not its value.** Attributes hold their last good value rather than going null — a dashboard reads plausible while the integration is dead.

When two sources disagree, reconcile them before diagnosing — and mind that the log endpoints disagree about both timezone and ordering (`reference/endpoints.md`). Proceed to Step 5.

## Step 5 — Report

State the diagnosis with the evidence (the frame that showed it) and the fix. **Separate proven from inferred** — name which claims a frame or a timestamp demonstrates, and which are correlation you have not bisected. If the fix is a code change, offer to apply it and redeploy. Finish here.
