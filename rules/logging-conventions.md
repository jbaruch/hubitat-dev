---
alwaysApply: true
description: The logEnable/txtEnable logging convention and auto-disable idiom every Hubitat driver follows
---

# Logging Conventions

The only debugger Hubitat gives you is `log` + the live log stream (`ws://<hub>/logsocket`, see `reference/endpoints.md`). Logging discipline is load-bearing, not cosmetic.

## Levels

- `log.error`, `log.warn`, `log.info`, `log.debug`, `log.trace` — each tagged by level in the Logs page.
- `info` for user-meaningful state changes, `debug` for developer detail, `warn`/`error` for problems. Never log secrets or tokens.

## The two-toggle convention

Nearly every community driver and app exposes two boolean preferences and guards its chatty logs on them:

```groovy
input name: "logEnable", type: "bool", title: "Enable debug logging", defaultValue: true
input name: "txtEnable", type: "bool", title: "Enable descriptionText logging", defaultValue: true
```

- Guard debug with `if (logEnable) log.debug "..."` and descriptive info with `if (txtEnable) log.info "..."`.
- Auto-disable debug logging after 30 minutes so a hub is never left flooding its logs:

```groovy
def updated() { if (logEnable) runIn(1800, logsOff) }
void logsOff() {
    log.warn "debug logging disabled"
    device.updateSetting("logEnable", [value: "false", type: "bool"])
}
```

- Apps use the same `logEnable` guard but typically leave it to the user to disable rather than auto-timing-out.

## Debug flow

- Add `log.debug` at the point of uncertainty (raw `parse` input, a computed value, a branch taken), deploy, then read the log socket against the code — that is what the `debug` skill does. Remove or guard the noise before shipping.
