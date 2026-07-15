---
alwaysApply: true
description: What Hubitat's Z-Wave/Zigbee mesh metrics mean, the backend RSSI-scale trap, and what counts as a real problem
---

# Z-Wave & Zigbee Mesh Health

Radio-mesh diagnosis is a different axis from code debugging. The data comes from two
undocumented JSON endpoints (`reference/endpoints.md`); `scripts/hub_mesh.py` fetches and flags,
the `mesh-health` skill interprets. Hubitat publishes **no numeric "bad" thresholds** — flag
unambiguous signals and rank the rest; never assert an invented cutoff.

## Z-Wave node metrics (grounded meanings)

- `nodeState` `FAILED` — a failed/ghost node (a dead device, or a pairing that never completed). The one unambiguous critical signal. Refresh, then Remove from the Z-Wave Details page.
- `per` — cumulative packet-**error count** ("accumulation of packet errors for a node"), not a percentage. Lower is better; `0` is ideal. Nonzero means errors are occurring; judge severity relative to the node's `msgCount` and to peers.
- `averageRtt` — round-trip time in ms; lower is better. No spec cutoff — rank, don't threshold.
- `routeChanges` — mesh-stability indicator; frequent changes mean the Last Working Route keeps failing. Populated on the legacy backend only.
- `neighbors` — how many nodes this one can hear. Populated on the legacy backend only.

## The backend split (RSSI scale trap)

- `lwrRssi` is reported on **two different scales** depending on the Z-Wave backend. Read `zwaveJS` first.
- `zwaveJS:true` — absolute dBm (negative, e.g. `-78db`); closer to `0` is stronger. The Silicon Labs receiver-sensitivity floor is −97 dBm (700-series) / −110 dBm (800-series) — a route near the floor is genuinely weak.
- `zwaveJS:false` (legacy) — dB *above the noise floor* (positive, e.g. `27dB`); `≤ 0` is at/below noise.
- Higher is better on **both** scales, so worst-first ranking is the same; a fixed numeric cutoff is **wrong** across backends. `neighbors` and `routeChanges` are also blank/`N/A` under zwaveJS.

## Zigbee: liveness and network-level only

- The endpoint exposes **no per-device LQI or RSSI**. Do not rank Zigbee devices by signal — the data is not there.
- Per device: `active:false` = not communicating (dead or an unfinished join). A generic `name:"Device"` of `type:"Device"` is a join that never initialized — the Zigbee ghost.
- Network-level problems: `networkState` ≠ `ONLINE`, `healthy:false`, or `weakChannel:true` (interference on the current channel). Zigbee uses 2.4 GHz channels 11–26; `weakChannel` overlaps busy Wi-Fi.

## Grounding sources

- Field meanings: Hubitat docs (Z-Wave Details, Zigbee Details, Troubleshoot Z-Wave/Zigbee) and staff mesh-details explanations.
- Protocol constants: Silicon Labs Z-Wave 700/800 receiver sensitivity; IEEE 802.15.4-2011 §8.1.2.2 (channels 11–26), LQI 0–255.
- Backend split and node/device shapes: verified live on 2.5.1.128 across a zwaveJS ("Apps") and a legacy ("Devices") hub (`reference/endpoints.md`).
