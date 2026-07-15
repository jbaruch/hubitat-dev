# Z-Wave Device Lifecycle — grounded wire signatures

Captured live on 2026-07-15 against the "Apps" hub (C-8 Pro, zwaveJS backend, Z-Wave LR) by
tailing `ws://<ip>/zwaveLogsocket` (`scripts/hub_radiolog.py`) through a real inclusion and a real
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

## FAILED ≠ ghost

A `nodeState: FAILED` node with a bound `deviceId` is a **real device currently unreachable** (may
be transient — recover it, do not delete). Only a FAILED node with **no** `deviceId` is an orphan
ghost. Verified live: 8 nodes went FAILED during a session of heavy LR-channel activity, all with
`deviceId`s — real devices knocked offline, not ghosts. `hub_mesh.py` tags each `failure_kind`.
