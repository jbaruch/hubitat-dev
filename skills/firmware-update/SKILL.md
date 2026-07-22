---
name: firmware-update
description: Update Z-Wave device firmware on a Hubitat hub via the native zwaveJS updater — discover installed versions, find vendor-latest firmware, stage it, and batch-flash safely with a radio-hang watchdog. Use when the user wants to update/flash device firmware, check which devices are behind on firmware, or fix a device whose issue a firmware update addresses.
---

# Firmware-Update Skill

Process steps in order. Do not skip ahead.

Hubitat's built-in **Device firmware updater** (Settings → Z-Wave Details → Maintenance) flashes OTA
over the same radio the device already uses — no driver swap, no self-hosting, and it handles **LR +
S2** that the community driver-swap updaters stall on. Its HTTP surface and every field are grounded
in `hub_fw_update.py` and `../_reference/endpoints.md`.

**The load-bearing hazard:** a **failed OR stalled** OTA can hang the entire zwaveJS controller — the
hub keeps returning `success:true` but transmits nothing, freezing every Z-Wave node at once (Zigbee
is unaffected — that asymmetry is the tell). A mid-transfer stall on a weak node did exactly this to a
main/automation hub and staleness-poisoned every lux/temperature-gated rule downstream until a reboot.
Steps 5–6 exist to prevent and recover from that; never fire-and-forget a flash.

## Step 1 — Frame the job

Establish the hub (`--ip` or `--hub`) and confirm the backend is **zwaveJS** (`zwaveJS:true` in
`/hub/zwaveDetails/json`) — this skill drives the zwaveJS updater. Identify the target device(s) and
whether they are **mains or battery/FLiRS**: battery/sleepy devices (locks, most sensors) must be
awake for the multi-minute transfer (USB power, fresh batteries, or operate them to wake). Proceed to Step 2.

## Step 2 — Discover installed firmware

Read installed versions over HTTP — no UI:
- Bulk: `GET /device/fullJson/<deviceId>` → `device.data.{deviceModel, firmwareVersion, protocolVersion, manufacturer}`.
- Per node (driver-independent): `GET /hub/zwave/deviceFirmware/details?nodeId=<n>` → `targets[0].version` + `firmwareIdHex`.

Build a model + version + node/device-id map. Proceed to Step 3.

## Step 3 — Find vendor-latest (NOT auto-discovery)

`GET /hub/zwave/deviceFirmware/available?nodeId=<n>` exists but **must not be the source of truth** —
it lags (offered ZEN04 2.30 when 2.60 shipped, missing the SDK/S2 fixes) and mis-matches (offered a
Springs shade a bogus downgrade). Get the real latest from the vendor:
- **Zooz** — the OTA files page lists every model/version; direct free download `getzooz.com/firmware/<MODEL>_V<MM>R<mm>.zip` (`.gbl` 700/800, `.otz` 500). Read the per-model **change log** to justify the update.
- **Leviton** — free `.ota` on `leviton.com/content/dam/leviton/support/`. Never cross model files.
- Others vary; some (e.g. Springs shades) publish **no** downloadable firmware — say so and stop.

**Hardware revision decides the image.** 700-series and 800LR share a model name but need different
files; pick by the installed major version. The wrong image can brick — the hub rejects a mismatch at
`/start`, but do not rely on that. Unzip to the raw `.gbl`/`.otz`. Proceed to Step 4.

## Step 4 — Stage the firmware on the hub

Upload once per model — it persists and is reusable across every node of that model:

```
curl -F "uploadFile=@<MODEL>_V<MM>R<mm>.gbl" http://<hub>:8080/hub/fileManager/upload/firmware
```

Confirm via `GET /hub/zwave/deviceFirmware/files`. Proceed to Step 5.

## Step 5 — Flash with the safeguards

Write a worklist `[{"nodeId":N,"fileName":"X.gbl","target":"2.6","name":"…"}, …]` and run:

```
python3 skills/_scripts/hub_fw_update.py --ip <addr> --worklist work.json --canary <devId>:<nodeId>
```

Full argument/flow/hazard contract: the `hub_fw_update.py` module docstring. It is idempotent (skips
nodes already at target) and carries **two required guards plus a floor**:
- **No-progress watchdog** — aborts a flash if `percent` stops advancing at **any** level (a frozen
  transfer never emits DONE/FAILED and would hang the radio forever).
- **Canary** (`--canary devId:nodeId`, a known-healthy **mains** node) — after each flash, confirms the
  controller still transmits; if not, it **reboots the hub and re-checks**, aborting if it stays hung.
- **RSSI floor** (`--rssi-floor`, default −95 dBm) — skips hang-prone floor-signal nodes; override only
  when attended with `--flash-weak`.

**One batch per radio** (a hub's Z-Wave is single-threaded for OTA); use `--wait-pid` to chain a second
batch after the first, or run different hubs in parallel. Never run two flashes into the same radio at
once. Proceed to Step 6.

## Step 6 — Verify and account

The script verifies each node against its target (the hub caches the old version until the post-reboot
re-interview, so `/details` is polled until it flips). Re-read `device.data.firmwareVersion` for a
second confirmation. Report per device: updated / skipped (already current) / skipped-weak / failed —
and for any failure, that the device is **on old firmware, not bricked** (`nodeState OK`) and retryable.
Do not silently drop the weak/failed nodes; name them and their current version.
