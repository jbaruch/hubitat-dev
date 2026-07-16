---
alwaysApply: true
description: What Hubitat's Z-Wave/Zigbee mesh metrics mean, the backend RSSI/timestamp traps, the command path vs the radio path, and what counts as a real problem
---

# Z-Wave & Zigbee Mesh Health

Radio-mesh diagnosis is a different axis from code debugging. The data comes from three
undocumented JSON endpoints (`reference/endpoints.md`); `scripts/hub_mesh.py` fetches and flags,
the `mesh-health` skill interprets. Hubitat publishes **no numeric "bad" thresholds** — flag
unambiguous signals and rank the rest; never assert an invented cutoff.

**A clean radio is not a working device.** Every metric here describes the *radio* path. Commands
reach a device over a separate *command* path (app → hub mesh → owning hub → radio), and a broken
command path leaves every radio metric green. Radio evidence can never conclude "the mesh is
healthy, so the devices are fine" — see `The command path`.

## Z-Wave node metrics (grounded meanings)

- `nodeState` `FAILED` — the node is unreachable. **Split it by `deviceId`** (`hub_mesh.py` tags `failure_kind`): FAILED **with** a bound `deviceId` is a *real device currently unreachable* — may be transient (recover it, do NOT delete); FAILED with **no** `deviceId` is an *orphan ghost* (a pairing that never bound a device) — safe to remove. Never force-remove a real device thinking it is a ghost.
- `lastTime` — when the hub last heard the node; **absent** on a node never heard at all (`zwave.never_heard[]`). Such a node is reported `nodeState:OK` and passes every radio check, so no FAILED-keyed check sees it. A never-heard node with **no** `deviceId` is a ghost by the split above; with a `deviceId` it is a real device that has never spoken — judge it, don't assume either way.
- `per` — cumulative packet-**error count** ("accumulation of packet errors for a node"), not a percentage. Lower is better; `0` is ideal. Nonzero means errors are occurring; judge severity relative to the node's `msgCount` and to peers.
- `averageRtt` — round-trip time in ms; lower is better. No spec cutoff — rank, don't threshold.
- `routeChanges` — classic-mesh stability indicator; frequent changes mean the Last Working Route keeps failing. Reported by the **legacy** backend; `N/A` on the zwaveJS backend.
- `neighbors` — how many nodes a **classic-mesh** node hears. `0` for Long Range nodes (a star has none — see below), not a backend artifact.

## The command path (hub mesh)

- **Actuator vs reporter is the diagnostic split**, not Z-Wave vs Zigbee. A **reporter** (lock, motion, presence) transmits on its own, so a fresh `lastTime` proves the radio works. An **actuator** (shade, outlet, lamp) transmits only *after* being commanded, so its `lastTime` is **when a command last landed** — silence is unknown, not broken.
- **Reporters fresh + actuators frozen = the command path is broken, not the radio.** When it spans *both* radios at once, no radio fault can explain it — two radios do not fail in the same second. Read `zwave.stalest` / `zigbee.stalest` this way. `hub_mesh.py` ranks staleness and never flags it — the classification is yours.
- Actuator timestamps clustered in one narrow window mark the last window in which commands landed; a scheduled automation is the usual source.
- `hub_mesh.problems[]` is **critical**: a peer that cannot carry commands is as dead as a failed radio. `peer_unreachable` / `peer_identity_mismatch` come from probing the recorded address; `peer_offline` / `peer_inactive` / `peer_warning` are the hub's own claims.
- **The hub's peer fields do not detect a stale record** — a peer holding a dead address reports `active:true, offline:false, warning:null` with `lastActive` ticking. Never read those three as an all-clear; only the probe is evidence. The peer table is **asymmetric** — each hub keeps its own record, and one side can be right while the other is stale.
- Re-adding a peer is **hub-UI** work, and `shared_device_count` is its blast radius: every shared device is a link an app can bind to, and removing the peer unbinds them. Quote the count before advising a re-add, and prefer editing the address over remove-and-re-add. A hub whose address drifts re-breaks the record — a **DHCP reservation on the hub's current IP** is the durable fix.

## The backend split (RSSI scale + timestamp traps)

- `lastTime` is stamped **differently per backend**: legacy emits an explicit `+0000` (true UTC); zwaveJS emits a **naive** stamp in the hub's **local** zone (`timeZone` in `/hub/details/json`). Reading naive as UTC ages every zwaveJS node by the hub's UTC offset. Zigbee's `lastActivity` carries `+0000` on both.
- `lwrRssi` is reported on **two different scales** depending on the Z-Wave backend. Read `zwaveJS` first.
- `zwaveJS:true` — absolute dBm (negative, e.g. `-78db`); closer to `0` is stronger. Silicon Labs receiver sensitivity: −97 dBm (700-series, 100 kbps GFSK) and −110 dBm (800-series LR channel, 100 kbps O-QPSK); the classic GFSK floor on 800 is a few dB higher. A route near the floor is genuinely weak.
- `zwaveJS:false` (legacy) — dB *above the noise floor* (positive, e.g. `27dB`); `≤ 0` is at/below noise.
- Higher is better on **both** scales, so worst-first ranking is the same; a fixed numeric cutoff is **wrong** across backends. `routeChanges` is also `N/A` under zwaveJS — a backend limit, independent of topology.

## Classic mesh vs Long Range (LR)

- A C-8 Pro runs both at once. **Node id ≥ 256 = a Z-Wave LR node; id ≤ 232 = classic mesh.** Classify by id before advising — `hub_mesh.py` tags each node's `topology`.
- **LR is a star**: every LR node talks **directly** to the hub — no routing, no hops, **no repeaters** (Z-Wave Alliance / Silicon Labs). `neighbors:0` and a direct `route` (`01 -> <node>`) are inherent to LR, not faults. LR uses per-transmission dynamic power control over a long link budget, so a distant LR node sitting at −85…−93 dBm can be normal.
- **Never suggest a repeater or a Z-Wave repair for an LR node** — neither exists in a star. An **unreliable LR device at distance** (weak signal and/or high RTT, intermittently FAILED) is a genuine tradeoff, not one fix. Improving the direct link (hub antenna/placement, LR channel/interference) keeps LR's simplicity and its reliability-when-the-link-holds. Re-including as classic mesh gains repeater routing for a marginal link but takes on mesh's routing flakiness (route changes, LWR failures, slow hops). The choice is situational and contested — **many networks find LR more reliable than mesh, so do not default to mesh** — surface the tradeoff and let the owner decide.
- **Classic mesh** (id ≤ 232) is the only place `neighbors`, multi-hop `route`, `routeChanges`, repeaters, and Z-Wave repair apply.

## Zigbee: liveness, and where signal actually lives

- The `zigbeeDetails` **snapshot** exposes no per-device LQI or RSSI — from it, judge liveness, not signal.
- Per-device **LQI** lives in a different surface: `GET /hub/zigbee/getChildAndRouteInfo` (the neighbor/route table, text — `LQI:<n>` per router). Per-frame LQI+RSSI come from the live `zigbeeLogsocket`. Don't rank Zigbee signal off the snapshot; use one of those.
- Per device (snapshot): `active:false` = not communicating (dead or an unfinished join). A generic `name:"Device"` of `type:"Device"` is a join that never initialized — the Zigbee ghost.
- Network-level problems: `networkState` ≠ `ONLINE`, `healthy:false`, or `weakChannel:true` (interference on the current channel). Zigbee uses 2.4 GHz channels 11–26; `weakChannel` overlaps busy Wi-Fi.

## Live radio traffic (the log sockets)

- `ws://<ip>/zwaveLogsocket` and `ws://<ip>/zigbeeLogsocket` stream per-frame decoded traffic — distinct from the driver `/logsocket`. Tail via `scripts/hub_radiolog.py`; the snapshot says who is weak, the log shows it happening.
- Read them for live signal (Zigbee `lastHopLqi`/`lastHopRssi`, Z-Wave per-frame `RSSI: -NN dBm`), `sequence` gaps (a soft missed-frame hint, not a hard drop count — the counter is shared across the device's traffic), and which cluster/command a device uses.
- `lastHopLqi`/`lastHopRssi` are the **last hop into the hub** — for a routed device that is the repeater→hub link, not the end device's own radio. ZCL cluster names for the common clusters; `0xFC00–0xFFFE` is manufacturer-specific, `0xE000–0xEFFF` is reserved space vendors (Tuya) use off-spec.
- The Z-Wave **TransmitReport** (`hub_radiolog --summary` → `transmit_report`) gives the noise floor and SNR at *both* ends. An elevated or spiky **hub** noise floor with `hub_snr` well below `dest_snr` means the hub's own receiver is the bottleneck, from its RF environment rather than the device or distance. Common culprits are co-located 900 MHz radios, USB3, and gear clusters. The fix gets the hub's receiver out of that noise by relocating the hub, fitting an external antenna, or separating co-located hubs. Never the device.

## Device lifecycle & removal

- **LR devices join via SmartStart, not classic inclusion** — the DSK/QR is added to the hub's provisioning list and the device auto-includes on power-up (no add-mode, no button). LR inclusion is S2-mandatory; the new node gets an id ≥ 256.
- **Graceful removal is two steps and one is physical**: remove the device from the SmartStart provisioning list (else it re-includes), *and* exclude/factory-reset the physical device. An agent cannot do the physical step — so removal is guide-the-user, not automate.
- **The tooling confirms a removal, it does not trigger one.** No groundable zwaveJS action endpoint exists (only `/hub/dismissWeakZigbee`); inclusion/exclusion/remove are hub-UI + physical. Confirm a removal two ways: the snapshot node-count/id diff, and the radio-log signature (`reference/zwave-lifecycle.md`).
- Force-remove (`RemoveFailedNode`) applies only to a FAILED **orphan ghost**, never a recoverable real device.

## Grounding sources

- Field meanings: Hubitat docs (Z-Wave Details, Zigbee Details, Troubleshoot Z-Wave/Zigbee) and staff mesh-details explanations.
- LR star topology, no repeaters, node-id ≥ 256, dynamic power, S2-only: Z-Wave Alliance and Silicon Labs LR overview.
- Protocol constants: Silicon Labs Z-Wave 700/800 receiver sensitivity (per modulation); IEEE 802.15.4 §8.1.2.2 (channels 11–26), LQI 0–255.
- Backend-vs-topology split and node/device shapes: verified live on 2.5.1.128 across a zwaveJS ("Apps") and a legacy ("Devices") hub (`reference/endpoints.md`).
