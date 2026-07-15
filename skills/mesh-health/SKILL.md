---
name: mesh-health
description: Diagnose Hubitat Z-Wave and Zigbee network problems — ghost/failed nodes, packet errors, weak routes, dead or unjoined Zigbee devices, an unhealthy mesh. Use when the user wants to check mesh health, find ghost nodes, debug a flaky/slow Z-Wave or Zigbee device, or figure out why the radio network misbehaves.
---

# Mesh-Health Skill

Process steps in order. Do not skip ahead.

Radio-mesh problems are a different axis from code bugs (`rules/zwave-zigbee-mesh.md`). The hub
exposes mesh state as JSON; `hub_mesh.py` fetches and flags it, this skill interprets the flags
and rankings against the rule. Hubitat publishes no numeric thresholds — read the rankings as
evidence, not as pass/fail.

## Step 1 — Frame the question

Establish the hub (by `--ip` or `--hub` name) and the symptom: a specific slow/dropping device, or
a whole-network health check. Note whether it is Z-Wave, Zigbee, or both. Proceed to Step 2.

## Step 2 — Run the analyzer

```
python3 .tessl/plugins/jbaruch/hubitat-dev/scripts/hub_mesh.py --ip <addr> [--radio zwave|zigbee|both]
```

Argument contract, output shape, and every flag/rank rule: `scripts/hub_mesh.py` module docstring.
Output is one JSON object: `{zwave, zigbee, summary:{critical, warnings}}`. If `summary.critical`
and `summary.warnings` are both 0, report the mesh looks healthy and finish. Proceed to Step 3.

## Step 3 — Triage the critical signals

- Z-Wave FAILED nodes — split by `failure_kind`: `zwave.orphan_ghosts[]` (no `deviceId`, safe to remove) vs `zwave.unreachable_devices[]` (a real device currently unreachable — **may be transient; recover it, do not advise deleting it**). Name the node and device; never tell the user to remove an unreachable real device as if it were a ghost.
- Zigbee `zigbee.network_problems[]` — `weakChannel`, offline, or unhealthy network. A whole-radio issue, not one device.

These are grounded and definite. Removal itself is a hub-UI + physical action the skill guides but does not perform (`rules/zwave-zigbee-mesh.md` Device lifecycle). Proceed to Step 4.

## Step 4 — Read the warnings and rankings against the rule

Interpret, don't threshold — apply `rules/zwave-zigbee-mesh.md`:
- `zwave.packet_errors[]` — nonzero PER (cumulative error count); weigh against the node's `msgCount` and its peers, not an absolute number.
- `zwave.ranked.by_rtt_ms` / `by_rssi` — worst-first. **Check `zwave.backend` first**: `lwrRssi` is absolute dBm under `zwavejs` and dB-above-noise under `legacy` — the same number means different things.
- `zwave.weak_signal_heuristic[]` — backend-aware RSSI-near-floor flags; each carries `heuristic:true` and a cited `basis`. Present as a hint, not a fact.
- **Check each node's `topology` before advising a fix.** `lr` nodes are a star — no neighbors, no routes, **no repeaters or Z-Wave repair**. For an *unreliable `lr` device at distance*, present the tradeoff (improve the direct link — hub antenna/placement/LR-channel — vs. re-include as classic mesh for repeater routing at the cost of mesh flakiness); **do not default to mesh — many networks find LR more reliable** (`rules/zwave-zigbee-mesh.md`). Only `mesh` nodes take repeaters/repair.
- `zigbee.dead_devices[]` — `active:false`; `likely_incomplete_join:true` marks the `"Device"`/`"Device"` ghost. `zigbee.stalest` ranks by activity age.

Correlate a flagged node against the reported symptom. Proceed to Step 5.

## Step 5 — Watch the live radio traffic

The snapshot says *who* is weak; the radio log streams say *what is happening on the air* — per-frame
signal (LQI/RSSI) and sequence continuity (soft missed-frame hints). When a suspect device needs
confirming, tail its live traffic:

```
python3 .tessl/plugins/jbaruch/hubitat-dev/scripts/hub_radiolog.py --ip <addr> --radio zigbee|zwave \
    [--name "<device>" | --node <n>] --summary [--seconds N]
```

The script emits structured JSON by default; `--summary` gives the per-device rollup you want for
diagnosis (raw per-frame JSON otherwise). Argument and frame contract: `scripts/hub_radiolog.py` module docstring.
For a Z-Wave device that is slow or **flapping** (OK↔FAILED), operate it while capturing and read the
`--summary` `transmit_report` rollup: if `hub_snr_med` is well below `dest_snr_med` and the hub noise
floor is worse than the device's, the **hub's receiver** is the bottleneck (its RF environment), not
the device or distance — see `reference/zwave-lifecycle.md` (TransmitReport).
`--summary` aggregates the window into a per-device rollup (frame count, LQI/RSSI min+avg, `sequence_gaps`),
worst-signal first — the live counterpart to the snapshot. **Zigbee frames carry per-device
`lastHopLqi`/`lastHopRssi`** (the last hop into the hub — a repeater's link for a routed device). Read
values against `rules/zwave-zigbee-mesh.md` (higher LQI/RSSI better; no absolute cutoff). Skip this step
for a whole-network health check that needs no per-device confirmation. Proceed to Step 6.

## Step 6 — Report the Diagnosis

State the diagnosis with the evidence (the flag or ranking that showed it) and the grounded fix.
Ghost/failed-node removal and channel changes are **hub-UI actions** (Z-Wave Details → Refresh then
Remove; Zigbee Details → change channel/power) — this skill does not automate destructive mesh
operations. Guide the user through the UI step, and offer `Skill(skill: "debug")` if a driver-level
log-tail would confirm the device side. Finish here.
