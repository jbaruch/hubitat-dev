# Z-Wave Device Lifecycle — grounded wire signatures

Captured live on 2026-07-15 against the "Apps" hub (C-8 Pro, zwaveJS backend, Z-Wave LR) by
tailing `ws://<ip>/zwaveLogsocket` (`skills/_scripts/hub_radiolog.py`) through a real inclusion and a real
graceful exclusion of a Zooz LR leak sensor. These are the signatures the `mesh-health` skill uses
to **confirm** a lifecycle event happened — the tooling observes and confirms, it does not trigger
(there is no groundable zwaveJS action endpoint; inclusion/exclusion/remove are hub-UI + physical).

## SmartStart inclusion (LR)

LR devices join via **SmartStart**, verified against Silicon Labs: the device DSK/QR is added to
the hub's provisioning list ahead of time, and the device **auto-includes on power-up** — no
inclusion mode, no button press. Confirmed on the wire (new node `374`, id ≥ 256 = LR):

- Every frame `Security2CCMessageEncapsulation`, `security class: S2_Authenticated` (LR is S2-mandatory).
- The interview streams the device's command classes: `WakeUpCCIntervalReport` (a sleepy leak
  sensor reported 43200 s / 12 h), `VersionCCReport`, `ManufacturerSpecificCCReport`
  (manufacturer `0x027a` = Zooz), `BatteryCCReport`, `AssociationCCSet` (lifeline group 1 → node 1).
- S2 nonce resync visible: `failed to decode... retrying with SPAN extension` → `Security2CCNonceReport SOS: true` → the hub sends a fresh SPAN sender-EI.
- Per-frame RSSI is on each inbound `[DRIVER]` line (`RSSI: -NN dBm`).

Confirm inclusion: the snapshot node count rises and a new id (≥ 256 for LR) appears; the node's
`nodeState` settles to `OK` with `security: S2_Authenticated`.

## Graceful exclusion (RemoveNodeFromNetwork)

Even an LR node excludes via the **classic `RemoveNodeFromNetwork`** API (verified — the node id
appears in the callback bytes). Signature, in order:

```
[DRIVER] hub: « [REQ] [RemoveNodeFromNetwork]              # hub entered exclusion mode
[SERIAL] 0x...4b1a03 0176 07...   status 0x03 (node found), node 0x176 = 374
[SERIAL] 0x...4b1a06 0176 07...   status 0x06 (remove DONE), node 0x176
[CNTRLR] hub: the exclusion process was stopped
[CNTRLR] hub: Node 374 was removed                         # THE event line
```

Then zwaveJS purges the node's value DB (`[-] <CC> ... (was ...)` teardown lines) — that is the
*aftermath*, not the exclusion. Read the `Node <n> was removed` line, not the teardown.

Confirm exclusion: `Node <n> was removed` on the wire **and** the node id drops out of the snapshot.

Graceful removal of a SmartStart device is **two steps**: remove it from the provisioning list
(else it re-includes on next power-up), and exclude/factory-reset the physical device.

## Force-remove (RemoveFailedNode) — NOT yet captured live

`RemoveFailedNode` is the force-remove for a node the controller has marked FAILED; it applies only
to an **orphan ghost** (FAILED with no bound `deviceId`), never a recoverable real device. Its exact
wire signature was **not captured** this session (we declined to remove real devices), so it is not
asserted here — capture it before documenting the frames.

## TransmitReport — the richest RF diagnostic

Every hub→device Z-Wave command emits a `[DRIVER] hub:` TransmitReport line the JSON snapshot never
shows. `hub_radiolog.py` parses it into a `transmit` sub-dict (and `--summary` medians it). Real
line and fields:

```
callback id: 244 transmit status: OK, took 40 ms routing attempts: 2 ... TX power: 14 dBm
  ACK RSSI: -91 dBm            measured noise floor: -92 dBm            ← at the HUB
  measured RSSI of ACK from destination: -85 dBm   measured noise floor by destination: -96 dBm  ← at the DEVICE
```

- `status` (OK / NoAck — the definitive send result), `took_ms` (real latency), `routing_attempts` (retransmits), `tx_power` (max US LR is 14 dBm).
- `hub_noise_floor` + `hub_ack_rssi` → `hub_snr` (device→hub headroom, measured at the controller).
- `dest_noise_floor` + `dest_rssi` → `dest_snr` (hub→device headroom, measured at the device).
- **Read the asymmetry.** A hub noise floor worse than the devices' and a `hub_snr` well below `dest_snr` means the **hub's own receiver** is the bottleneck (its RF environment — co-located radios, USB3, a gear cluster), not the device and not distance. The opposite asymmetry points at the device end. This is the one measurement that localizes a flapping/high-latency link; the snapshot's single `lwrRssi` cannot.
- Invalid RSSI sentinels (a positive dBm such as `+78`) are dropped — a received RSSI on these logs is always negative dBm.
- **Validated live (before/after):** a hub in a server closet (3 hubs + network gear) showed a hub noise floor median −90 dBm with spikes to −85 and `hub_snr` ~1–8 dB while `dest_snr` ~11–15; 8 LR devices flapped OK↔FAILED with a 100 ms latency tail. Moving the hub to a quiet room dropped the noise floor to median −97 (spikes gone, worst −94), eliminated the retransmits and the latency tail (max `took_ms` 100 → 10 ms), and recovered all 8 — even though the devices' own signal got *weaker*. The noise floor, not the signal, was the fault.

## FAILED ≠ ghost

A `nodeState: FAILED` node with a bound `deviceId` is a **real device currently unreachable** (may
be transient — recover it, do not delete). Only a FAILED node with **no** `deviceId` is an orphan
ghost. Verified live: 8 nodes went FAILED during a session of heavy LR-channel activity, all with
`deviceId`s — real devices knocked offline, not ghosts. `hub_mesh.py` tags each `failure_kind`.
