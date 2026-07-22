#!/usr/bin/env python3
"""Batch-flash Z-Wave device firmware via Hubitat's native zwaveJS updater — safely.

The hub's built-in "Device firmware updater" (Settings -> Z-Wave Details -> Maintenance)
drives OTA over the same radio the devices already use — no driver swap, no self-hosting,
and it handles LR + S2 that the community driver-swap updaters stall on. Its HTTP surface
(verified live 2026-07-22 on 2.5.1.132, C-8 Pro, zwaveJS backend, Hub Security off — see
../_reference/endpoints.md):

    POST /hub/fileManager/upload/firmware              multipart .gbl/.otz/.ota (persists on hub, reusable per model)
    GET  /hub/zwave/deviceFirmware/files               uploaded firmware files
    GET  /hub/zwave/deviceFirmware/details?nodeId=N     {targets:[{target,version,firmwareIdHex}]}  (verify source)
    POST /hub/zwave/deviceFirmware/start               {"nodeId":N,"target":0,"fileName":"X.gbl"}   begins OTA
    GET  /hub/zwave/deviceFirmware/progress?nodeId=N    {progress:{percent,stage,status}}  stage PROCESS->SENDING->DONE

Flow per node: verify current version (skip if already at target) -> start -> poll to DONE ->
wait for NVM-commit+reboot -> poll /details until targets[0].version == target (the hub caches
the old version until the post-reboot re-interview). Firmware for a model is reusable across every
node of that model (upload once). Match the file to the device's HARDWARE REVISION (700 vs 800LR
share a model name but need different images) — the wrong image bricks; the hub rejects a mismatch
at /start, but do not rely on that.

THE HAZARD THIS SCRIPT EXISTS FOR (grounded the hard way, 2026-07-22):
A FAILED **or STALLED** OTA can hang the entire zwaveJS controller — not just skip that node.
Symptom: the hub keeps returning success:true to commands but TRANSMITS NOTHING; every Z-Wave
node freezes at once (Zigbee is unaffected — Zigbee sensors keep reporting, which is the tell that
it is the Z-Wave *controller*, not the mesh or the device). The Hub-Mesh peer still shows
active/reachable. Observed: a mid-transfer stall on a -99 dBm LR node hung the main hub's radio and,
downstream, staleness-poisoned every lux/temperature-gated automation until a reboot.

Two independent guards, both required (one is not enough — learned when a canary-only guard missed
a mid-transfer stall):
  1. NO-PROGRESS WATCHDOG. Abort a flash if `progress.percent` stops advancing for --stall-secs at
     ANY percent (not only at 0%). A frozen transfer never emits FAILED/DONE, so a plain
     start->wait-for-DONE loop hangs forever and takes the radio with it.
  2. CANARY radio-health probe between flashes. Refresh a known-healthy MAINS node and confirm its
     Z-Wave `lastTime` advances. If it does not, the controller is hung -> reboot and re-probe;
     abort the batch if it does not come back.

Plus an RSSI FLOOR: devices at/below --rssi-floor dBm (default -95; the Silicon Labs 700-series RX
floor is -97, 800-series LR -110) are hang-prone and rarely flash — skip them unless --flash-weak.
Not worth risking a whole-hub Z-Wave blackout to update one bathroom plug.

Reboot recovery: GET /hub/advanced/getManagementToken -> GET /management/reboot?token=<token>
(~2-3 min; zwaveJS re-interviews all nodes; verify a fresh systemStart in /hub/eventsJson).

Worklist JSON (--worklist): [{"nodeId":N,"fileName":"X.gbl","target":"2.6","name":"..."}, ...]
Progress goes to stderr; a JSON summary {ok,skipped,skipped_weak,failed,rebooted} to stdout.
Idempotent (skips nodes already at target); MAX_CONSEC_FAIL circuit breaker; optional --wait-pid
chains this run after another batch on the same radio finishes.
"""
import sys
import os
import json
import time
import argparse
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# E402: import must follow the sys.path insert above so hubclient resolves when run as a script.
from hubclient import HubError, resolve_base_from_args  # noqa: E402

POLL_SECS = 15
STALL_SECS = 240          # abort a flash if percent has not advanced for this long (at ANY %)
XFER_TIMEOUT = 20 * 60    # hard ceiling per flash
VERIFY_TIMEOUT = 4 * 60   # post-reboot re-interview ceiling
CANARY_ADVANCE = 30       # seconds to wait for a canary lastTime to advance
REBOOT_SETTLE = 120       # seconds for zwaveJS to re-init after a reboot comes back
RSSI_FLOOR_DEFAULT = -95
MAX_CONSEC_FAIL = 3


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=20) as r:
        return json.load(r)


def _post(base, path, body):
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def cur_version(base, node):
    tg = _get(base, f"/hub/zwave/deviceFirmware/details?nodeId={node}").get("targets") or []
    return tg[0].get("version") if tg else None


def node_rssi(base, node):
    """lwrRssi in dBm for a zwaveJS node (negative). None if unknown/legacy-scale."""
    for n in _get(base, "/hub/zwaveDetails/json").get("nodes", []):
        if n.get("nodeId") == node:
            raw = (n.get("lwrRssi") or "").strip()
            m = "".join(c for c in raw if c in "-0123456789")
            try:
                v = int(m)
                return v if v < 0 else None   # only trust the zwaveJS absolute-dBm scale here
            except ValueError:
                return None
    return None


def node_lasttime(base, node):
    for n in _get(base, "/hub/zwaveDetails/json").get("nodes", []):
        if str(n.get("nodeId")) == str(node):
            return n.get("lastTime")
    return None


def canary_transmits(base, canary_dev, canary_node):
    """True if the controller actually transmits: refresh the canary, confirm lastTime advances."""
    if not canary_dev:
        return True
    before = node_lasttime(base, canary_node)
    try:
        _post(base, "/device/runmethod", {"id": int(canary_dev), "method": "refresh"})
    except Exception as e:
        log(f"  canary refresh error: {e}")
    for _ in range(CANARY_ADVANCE // 5):
        time.sleep(5)
        if node_lasttime(base, canary_node) != before:
            return True
    return False


def reboot_hub(base):
    log(f"!!! Z-Wave controller appears HUNG on {base} — rebooting to recover")
    try:
        tok = _get(base, "/hub/advanced/getManagementToken")
        tok = tok if isinstance(tok, str) else tok.get("token", tok)
        urllib.request.urlopen(f"{base}/management/reboot?token={tok}", timeout=15).read()
    except Exception as e:
        log(f"  reboot request error: {e}")
    time.sleep(30)
    for _ in range(40):  # wait for the web server to come back
        try:
            urllib.request.urlopen(f"{base}/hub/details/json", timeout=4).read()
            break
        except Exception:
            time.sleep(5)
    time.sleep(REBOOT_SETTLE)  # let zwaveJS finish interviewing
    log("  hub back up; re-checking radio")


def ensure_radio_ok(base, canary_dev, canary_node, allow_reboot):
    """After each flash: if the radio is hung, reboot once and re-check. True if healthy."""
    if not canary_dev:
        return True
    if canary_transmits(base, canary_dev, canary_node):
        return True
    if not allow_reboot:
        log("  radio hung and --no-reboot set — aborting")
        return False
    reboot_hub(base)
    if canary_transmits(base, canary_dev, canary_node):
        log("  radio recovered after reboot")
        return True
    log("  radio STILL hung after reboot — aborting")
    return False


def flash(base, node, name, fname, target, rssi_floor, flash_weak):
    v = cur_version(base, node)
    if v == target:
        log(f"SKIP node {node} ({name}) already {target}")
        return "skipped"
    rssi = node_rssi(base, node)
    if not flash_weak and rssi is not None and rssi <= rssi_floor:
        log(f"SKIP-WEAK node {node} ({name}) rssi {rssi}dBm <= floor {rssi_floor} — hang-prone; use --flash-weak to force")
        return "skipped_weak"
    log(f"START node {node} ({name}) {v} -> {target} [{fname}]" + (f" rssi {rssi}dBm" if rssi is not None else ""))
    resp = _post(base, "/hub/zwave/deviceFirmware/start",
                 {"nodeId": node, "target": 0, "fileName": fname})
    if not resp.get("success", False):
        log(f"ERROR node {node}: start rejected: {resp}")
        return "failed"
    t0 = time.time()
    last_pct = None
    last_change = time.time()
    while True:
        now = time.time()
        if now - t0 > XFER_TIMEOUT:
            log(f"ERROR node {node}: transfer exceeded {XFER_TIMEOUT}s ceiling")
            return "failed"
        # NO-PROGRESS WATCHDOG: a frozen transfer at ANY % hangs the radio and never emits DONE/FAILED.
        if now - last_change > STALL_SECS:
            log(f"ERROR node {node}: no progress for {STALL_SECS}s (stuck at {last_pct}%) — aborting (radio-hang risk)")
            return "failed"
        try:
            p = _get(base, f"/hub/zwave/deviceFirmware/progress?nodeId={node}").get("progress", {})
        except urllib.error.URLError as e:
            log(f"  node {node}: progress read hiccup ({e})")
            time.sleep(POLL_SECS)
            continue
        stage, pct = p.get("stage"), p.get("percent")
        if pct != last_pct:
            log(f"  node {node}: {pct}% {stage}")
            last_pct = pct
            last_change = now
        if stage == "DONE":
            break
        if stage in ("ERROR", "FAILED", "ABORTED"):
            log(f"ERROR node {node}: stage={stage} status={p.get('status')}")
            return "failed"
        time.sleep(POLL_SECS)
    log(f"  node {node}: DONE, awaiting reboot/re-interview")
    t1 = time.time()
    while time.time() - t1 < VERIFY_TIMEOUT:
        time.sleep(20)
        try:
            v = cur_version(base, node)
        except urllib.error.URLError:
            continue
        if v == target:
            log(f"OK node {node} ({name}) verified {target}")
            return "ok"
    log(f"ERROR node {node} ({name}): not verified, still {v}")
    return "failed"


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def run(base, work, canary_dev, canary_node, rssi_floor, flash_weak, allow_reboot, wait_pid):
    if wait_pid:
        log(f"waiting for pid {wait_pid} (prior batch on same radio) to finish…")
        while pid_alive(wait_pid):
            time.sleep(30)
    log(f"=== fw batch: {len(work)} nodes on {base} "
        f"(canary {canary_dev}:{canary_node}, rssi_floor {rssi_floor}) ===")
    results = {"ok": [], "skipped": [], "skipped_weak": [], "failed": [], "rebooted": 0}
    consec = 0
    for w in work:
        node, name = w["nodeId"], w.get("name", "?")
        try:
            r = flash(base, node, name, w["fileName"], w["target"], rssi_floor, flash_weak)
        except Exception as e:
            log(f"ERROR node {node} ({name}): unhandled {type(e).__name__}: {e}")
            r = "failed"
        results[r].append(f"{node} {name}")
        consec = consec + 1 if r == "failed" else 0
        if consec >= MAX_CONSEC_FAIL:
            log(f"ABORT: {consec} consecutive failures — stopping to avoid cascading damage")
            break
        if not ensure_radio_ok(base, canary_dev, canary_node, allow_reboot):
            log("ABORT: Z-Wave controller hung and did not recover")
            break
        time.sleep(5)
    return results


def main(argv=None):
    p = argparse.ArgumentParser(description="Safely batch-flash Z-Wave firmware via the native zwaveJS updater.")
    p.add_argument("--worklist", required=True,
                   help='JSON: [{"nodeId":N,"fileName":"X.gbl","target":"2.6","name":"…"}, …]')
    p.add_argument("--ip")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--hub", help="named hub from hubs.json (when no --ip)")
    p.add_argument("--hubs", help="path to hubs.json (default ./hubs.json when --hub is given)")
    p.add_argument("--canary", help="devId:nodeId of a known-healthy MAINS node for the radio-health check")
    p.add_argument("--rssi-floor", type=int, default=RSSI_FLOOR_DEFAULT,
                   help=f"skip nodes with lwrRssi <= this dBm (default {RSSI_FLOOR_DEFAULT}); hang-prone")
    p.add_argument("--flash-weak", action="store_true", help="flash even below the RSSI floor (attended only)")
    p.add_argument("--no-reboot", action="store_true", help="do not auto-reboot on a hung radio (abort instead)")
    p.add_argument("--wait-pid", type=int, help="block until this pid exits (chain after another batch)")
    args = p.parse_args(argv)

    try:
        base = resolve_base_from_args(ip=args.ip, port=args.port, hub=args.hub, hubs_path=args.hubs)
    except HubError as e:
        print(str(e), file=sys.stderr)
        return 2
    try:
        work = json.load(open(args.worklist))
    except (OSError, ValueError) as e:
        print(f"cannot read worklist: {e}", file=sys.stderr)
        return 2
    canary_dev, canary_node = (args.canary.split(":") if args.canary else (None, None))

    results = run(base, work, canary_dev, canary_node, args.rssi_floor,
                  args.flash_weak, not args.no_reboot, args.wait_pid)

    log("=== SUMMARY ===")
    for k in ("ok", "skipped", "skipped_weak", "failed"):
        log(f"{k}: {len(results[k])}")
        for x in results[k]:
            log(f"   {x}")
    print(json.dumps(results, indent=1))
    return 1 if results["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
