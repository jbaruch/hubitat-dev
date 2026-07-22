---
alwaysApply: true
description: Z-Wave device firmware (OTA) updates on Hubitat — a failed OR stalled flash can hang the whole controller; prerequisites, vendor-latest sourcing, and the mandatory watchdog/canary/RSSI-floor guardrails
---

# Z-Wave Firmware Updates

Updating device firmware is one of the few genuinely dangerous operations on a hub: a bad flash is closer to a brick than a `git revert`, and — the trap that surprised us — a flash that merely **stalls** can take the whole radio down with it. Flash deliberately, one radio at a time, guarded.

## Use the hub's native zwaveJS updater, not a driver swap

- Settings → Z-Wave Details → Maintenance → **Device firmware updater** (UI page `/hub/zwaveInfo`) flashes OTA over the radio the device already uses. It handles **Long Range + S2**; the community driver-swap updaters (swap to a firmware-updater driver, call `updateFirmware(url)`) **stall on LR/S2** ("Please wake up your sleepy device" on a mains device = wrong tool, not "wait longer").
- HTTP surface (no auth, Hub Security off): `POST /hub/fileManager/upload/firmware` (multipart), `GET /hub/zwave/deviceFirmware/files`, `GET …/details?nodeId=N`, `POST …/start {nodeId,target:0,fileName}`, `GET …/progress?nodeId=N` → `{progress:{percent,stage}}` (`PROCESS`→`SENDING`→`DONE`). It flashes **in place** — no driver change, the device keeps its real driver. `skills/_scripts/hub_fw_update.py` drives it; `skills/firmware-update/SKILL.md` is the procedure.

## Prerequisites (check before flashing)

- **Backend is zwaveJS** (`zwaveJS:true` in `/hub/zwaveDetails/json`). This skill/API is the zwaveJS updater.
- **Right image for the hardware revision.** 700-series and 800LR share a model name but need **different** files — pick by the installed major version (ZEN76 3.x → 3.60 / 800LR; 2.x/10.x → 700-series). The wrong image bricks; the hub rejects a mismatch at `/start`, but do not rely on that.
- **Mains vs battery/FLiRS.** Mains devices flash unattended. Battery/sleepy devices (locks, most sensors) must be **awake** for the multi-minute transfer — USB power, fresh batteries, or operate them to wake (a lock via lock/unlock, a motion sensor via motion). A lock also has a *second* firmware plane (main/Wi-Fi over Bluetooth in the vendor app) distinct from the Z-Wave module image the hub flashes.
- **One batch per radio.** A hub's Z-Wave is single-threaded for OTA — never run two flashes into the same radio at once; chain them (`--wait-pid`) or run different hubs in parallel.

## Source vendor-latest — NOT the hub's auto-discovery

- `GET /hub/zwave/deviceFirmware/available?nodeId=N` (the Z-Wave JS firmware service) **lags and mis-matches**: it offered ZEN04 **2.30** when the vendor shipped **2.60** (missing the 2.40 redundant-report fix and the 2.50 SDK 7.19→7.24 S2/SPAN fix), and it offered a Springs shade a **bogus downgrade** to "1.5". Use it only as a rough "is there anything", never as the version/file.
- Get the real latest from the vendor, and **read the change log** to justify the update: Zooz — free at `getzooz.com/firmware/<MODEL>_V<MM>R<mm>.zip` (`.gbl` 700/800, `.otz` 500); Leviton — free `.ota` on `leviton.com/content/dam/leviton/support/` (never cross model files); Ultraloq — free `.gbl` on `file.u-tec.com`. Some vendors publish **no** downloadable firmware (Springs/Somfy shades refuse "old motor" updates and aren't in the service) — then there is nothing to do; say so.
- Newer is not always better: verify the change log addresses the problem, and heed community reports (Ultraloq 1.5 on 1.01 units often **fails outright** and can worsen lock-state reporting with no downgrade path; a Springs 13.3 motor is reported more reliable than 14.7).

## The load-bearing hazard: a failed OR stalled OTA hangs the whole controller

Grounded live twice (2026-07-22). A flash that **fails mid-transfer**, or **stalls** (percent freezes and never emits `DONE`/`FAILED`), hangs the entire zwaveJS controller — **not just that node**:

- The hub keeps returning `success:true` to every command but **transmits nothing**; every Z-Wave node freezes at once.
- **Zigbee is unaffected** — Zigbee sensors keep reporting. That asymmetry is the diagnostic tell: it's the Z-Wave *controller*, not the mesh, not the device.
- The Hub-Mesh peer still shows `active/reachable` — invisible to the peer table (same class as `zwave-zigbee-mesh.md`'s "peer looks fine but drops commands"). Even a **local** command on the owning hub returns success but never reaches the node (its `lastTime` doesn't advance).
- **Downstream blast radius:** frozen sensors go stale, which **staleness-poisons every lux/temperature-gated automation** — a "Motion at Dark" rule fired lights in daylight because the illuminance sensor's radio was hung at last night's value. A hung radio is not just frozen devices.

**Why a big fleet hides it and a small one doesn't:** on a hub with many devices to flash, successes *after* a failure keep re-kicking the radio so a transient hang self-clears; on a hub with few devices, or when the **last/only** flash fails, nothing follows to shake it loose and the radio stays wedged silently. The trailing/only failure is the dangerous one.

## Guardrails (all required — one alone is not enough)

1. **No-progress watchdog.** Abort a flash if `percent` stops advancing for a few minutes at **ANY** level, not only at 0%. A frozen transfer never emits `DONE`/`FAILED`, so a plain start→wait-for-DONE loop hangs forever and takes the radio with it. (A canary-only guard *missed* a mid-transfer stall — that's why this is separate.)
2. **Canary radio-health probe**, run **only after a failed flash**. A hung controller freezes **every** node's `lastTime` (it transmits nothing); a healthy hub always has some node reporting, and a rebooting hub advances `lastTime`s as it re-interviews. So nudge a known-healthy **mains** node and, over a **wide window**, check whether **ANY** node's `lastTime` advances — *not* whether one specific node answers inside a tight window. If nothing advances, the controller is hung → **reboot and re-check**; abort if it stays hung. Two false-positive traps this avoids, both learned live on a 75-LR-node hub: (a) do **not** probe after a *successful* flash — the success already proved the radio transmits, and the flashed device's own re-interview busies the radio → needless reboot; (b) a single-node/30 s check false-reads "hung" (and post-reboot "still hung," while zwaveJS is still interviewing) even though the radio is fine — checking **any** node over a wide window fixes both directions.
3. **RSSI floor.** Skip nodes at/below ~−95 dBm `lwrRssi` (SiLabs RX floor: −97 dBm 700-series, −110 dBm 800-LR) — floor-signal nodes are hang-prone and rarely flash. Not worth risking a whole-hub Z-Wave blackout to update one bathroom plug; force only when attended.

Never fire-and-forget across marginal devices. Flash strong-signal devices; leave floor-RSSI ones on their current firmware.

## Recovery and verification

- **Reboot clears the hang**: `GET /hub/advanced/getManagementToken` → `GET /management/reboot?token=<token>` (~2–3 min; zwaveJS re-interviews every node; confirm a fresh `systemStart` in `/hub/eventsJson`). On the automation hub a reboot is a ~3-min blackout, so **prevention (the guardrails) beats recovery** here.
- **Nothing bricks from a hang** — after recovery all nodes read `nodeState OK`; a stalled node sometimes even completes to target once the reboot frees the queue.
- **Verify** each node against its target: the hub caches the old version in `device.data.firmwareVersion` until the post-reboot re-interview, so poll `/hub/zwave/deviceFirmware/details?nodeId=N` → `targets[0].version` until it flips. Report per device — updated / already-current / skipped-weak / failed — and name any failure with its current version (on old firmware, not bricked, retryable).
