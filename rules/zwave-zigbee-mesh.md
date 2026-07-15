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
- `routeChanges` — classic-mesh stability indicator; frequent changes mean the Last Working Route keeps failing. Reported by the **legacy** backend; `N/A` on the zwaveJS backend.
- `neighbors` — how many nodes a **classic-mesh** node hears. `0` for Long Range nodes (a star has none — see below), not a backend artifact.

## The backend split (RSSI scale trap)

- `lwrRssi` is reported on **two different scales** depending on the Z-Wave backend. Read `zwaveJS` first.
- `zwaveJS:true` — absolute dBm (negative, e.g. `-78db`); closer to `0` is stronger. Silicon Labs receiver sensitivity: −97 dBm (700-series, 100 kbps GFSK) and −110 dBm (800-series LR channel, 100 kbps O-QPSK); the classic GFSK floor on 800 is a few dB higher. A route near the floor is genuinely weak.
- `zwaveJS:false` (legacy) — dB *above the noise floor* (positive, e.g. `27dB`); `≤ 0` is at/below noise.
- Higher is better on **both** scales, so worst-first ranking is the same; a fixed numeric cutoff is **wrong** across backends. `routeChanges` is also `N/A` under zwaveJS — a backend limit, independent of topology.

## Classic mesh vs Long Range (LR)

- A C-8 Pro runs both at once. **Node id ≥ 256 = a Z-Wave LR node; id ≤ 232 = classic mesh.** Classify by id before advising — `hub_mesh.py` tags each node's `topology`.
- **LR is a star**: every LR node talks **directly** to the hub — no routing, no hops, **no repeaters** (Z-Wave Alliance / Silicon Labs). `neighbors:0` and a direct `route` (`01 -> <node>`) are inherent to LR, not faults. LR uses per-transmission dynamic power control over a long link budget, so a distant LR node sitting at −85…−93 dBm can be normal.
- **Never suggest a repeater or a Z-Wave repair for an LR node** — neither exists in a star. A genuinely weak LR link is a hub-antenna / placement / distance question, or is simply accepted.
- **Classic mesh** (id ≤ 232) is the only place `neighbors`, multi-hop `route`, `routeChanges`, repeaters, and Z-Wave repair apply.

## Zigbee: liveness, and where signal actually lives

- The `zigbeeDetails` **snapshot** exposes no per-device LQI or RSSI — from it, judge liveness, not signal.
- Per-device **LQI** lives in a different surface: `GET /hub/zigbee/getChildAndRouteInfo` (the neighbor/route table, text — `LQI:<n>` per router). Per-frame LQI+RSSI come from the live `zigbeeLogsocket`. Don't rank Zigbee signal off the snapshot; use one of those.
- Per device (snapshot): `active:false` = not communicating (dead or an unfinished join). A generic `name:"Device"` of `type:"Device"` is a join that never initialized — the Zigbee ghost.
- Network-level problems: `networkState` ≠ `ONLINE`, `healthy:false`, or `weakChannel:true` (interference on the current channel). Zigbee uses 2.4 GHz channels 11–26; `weakChannel` overlaps busy Wi-Fi.

## Grounding sources

- Field meanings: Hubitat docs (Z-Wave Details, Zigbee Details, Troubleshoot Z-Wave/Zigbee) and staff mesh-details explanations.
- LR star topology, no repeaters, node-id ≥ 256, dynamic power, S2-only: Z-Wave Alliance and Silicon Labs LR overview.
- Protocol constants: Silicon Labs Z-Wave 700/800 receiver sensitivity (per modulation); IEEE 802.15.4 §8.1.2.2 (channels 11–26), LQI 0–255.
- Backend-vs-topology split and node/device shapes: verified live on 2.5.1.128 across a zwaveJS ("Apps") and a legacy ("Devices") hub (`reference/endpoints.md`).
