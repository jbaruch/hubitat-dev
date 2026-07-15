# Changelog

## 0.1.3 — 2026-07-15

### Added

- Grounded the Z-Wave **device lifecycle** from live experiments (a real SmartStart LR inclusion and a real graceful exclusion, captured on `zwaveLogsocket`): new `reference/zwave-lifecycle.md` documents the inclusion interview signature (S2-mandatory, id ≥ 256, the CC interview + SPAN nonce resync) and the graceful-exclusion signature (`RemoveNodeFromNetwork` → status `0x06` on the node id → `Node N was removed`, then DB teardown). Force-remove (`RemoveFailedNode`) is noted as not-yet-captured rather than asserted.
- `hub_radiolog.py` now parses the Z-Wave **TransmitReport** frame — the richest RF diagnostic, absent from the snapshot: per-direction noise floor + signal (`hub_snr`/`dest_snr`), real latency (`took_ms`), retransmits, TX power. `--summary` adds a `transmit_report` rollup. The hub-vs-device SNR asymmetry localizes a flapping/high-latency link to the hub's receiver vs the device (`reference/zwave-lifecycle.md`). Also drops invalid RSSI sentinels (a positive dBm such as `+78`) instead of reporting them as real readings.

### Changed

- `hub_mesh.py` now splits FAILED nodes into `orphan_ghosts` (no `deviceId` — safe to remove) vs `unreachable_devices` (a bound `deviceId` — a real device currently unreachable, possibly transient; recover, don't delete), tagging each `failure_kind`. The `zwave-zigbee-mesh` rule and `mesh-health` skill stop calling every FAILED node a "ghost" and no longer advise removing a real unreachable device. Grounded when 8 real devices went FAILED (all with `deviceId`s) during heavy LR-channel activity — not ghosts. Removal is documented as a hub-UI + physical action the tooling confirms (snapshot diff + wire signature) but never triggers; no groundable zwaveJS action endpoint exists.
- The rule and skill now frame the **unreliable Long Range device at distance** case as a tradeoff, not a prescription: improving the direct link (hub antenna/placement, LR channel) keeps LR's reliability-when-the-link-holds, while re-including as classic mesh gains repeater routing at the cost of mesh's routing flakiness. Explicitly does not default to mesh — many networks find LR more reliable. Grounded diagnosing a real fleet where 8 identical Zooz LR switches were unreliable with high latency while 65 identical siblings were fine.

## 0.1.2 — 2026-07-15

### Added

- Live **radio-traffic** debugging: `scripts/hub_radiolog.py` tails the dedicated Z-Wave and Zigbee log websockets (`ws://<ip>/zwaveLogsocket`, `ws://<ip>/zigbeeLogsocket` — distinct from the driver `/logsocket`) and reads the per-frame decoded traffic. A new `mesh-health` step ties it to the snapshot: triage says which device is weak, the radio log shows it happening. Zigbee frames carry per-device `lastHopLqi`/`lastHopRssi` (the last hop into the hub) and a `sequence` counter, so `--summary` produces a live per-device signal rollup (LQI/RSSI min+avg, soft dropped-frame gaps, decoded ZCL cluster) the snapshot cannot — worst signal first. Z-Wave frames surface the controller/driver decode with per-frame RSSI. Cluster classification follows the ZCL spec (manufacturer-specific `0xFC00–0xFFFE`; `0xE000–0xEFFF` is reserved space vendors use off-spec). Log sockets and frame shapes verified live on 2.5.1.128.

## 0.1.1 — 2026-07-15

### Added

- Z-Wave and Zigbee **mesh-health** diagnostics: the `mesh-health` skill and `scripts/hub_mesh.py` fetch `/hub/zwaveDetails/json` and `/hub/zigbeeDetails/json` and flag failed/ghost nodes, nonzero PER, dead/incomplete Zigbee joins, and an unhealthy network, ranking nodes worst-first by PER/RTT/RSSI rather than against absolute cutoffs Hubitat does not publish. The new always-on `zwave-zigbee-mesh` rule carries the grounded field meanings and two load-bearing distinctions: `lwrRssi` is reported on two scales by the Z-Wave *backend* (zwaveJS absolute dBm vs legacy dB-above-noise), while `neighbors`/routing are set by *topology* — the tool tags each node `lr` (id ≥ 256, a star: no neighbors, no routes, no repeaters) vs `mesh` (id ≤ 232), so a weak Long Range node is never mis-advised toward a repeater or Z-Wave repair. Endpoints and field shapes verified live on 2.5.1.128 across a zwaveJS ("Apps") and a legacy ("Devices") hub; metric meanings and protocol constants grounded in Hubitat docs, Z-Wave Alliance / Silicon Labs (LR star topology, 700/800 sensitivity per modulation), and IEEE 802.15.4.

## 0.1.0 — 2026-07-14

### Added

- Initial plugin skeleton for `jbaruch/hubitat-dev`: manifest, README with registry badge, and the `sandbox-constraints` rule and `scaffold` skill seeds.
- Grounded the Hubitat code-editor and logging endpoints against live C-8 Pro hubs on platform 2.5.1.125 (Hub Security off): `/hub2/userAppTypes`, `/hub2/userDeviceTypes`, `/hub2/userLibraries` for code enumeration; `/app/ajax/code` and `/driver/ajax/code` for source+version round-trip; `ws://<hub>/logsocket` confirmed streaming structured JSON frames. These shapes seed `reference/endpoints.md`.
- Bundled `reference/` data: `capabilities.json` (all 102 capabilities → attributes/commands/params, parsed from the authoritative capability list), `allowed-imports.txt` (the 197-class sandbox allow-list), `endpoints.md` (the grounded hub endpoint catalog), and `input-types.md` (app + driver preference input types).
- Seven always-on rules: `sandbox-constraints`, `app-lifecycle`, `driver-lifecycle`, `logging-conventions`, `state-vs-attributes`, `groovy-gotchas`, `multi-hub-topology`.
- Deterministic scripts (Python, stdlib-only, unit-tested): `hub_lint.py` (sandbox + silent-failure linter, validated against real community drivers with zero false positives), `hubclient.py` (shared config + code enumerate/pull/deploy with version optimistic-concurrency), `hub_pull.py`, `hub_deploy.py`, and `hub_logtail.py` (stdlib websocket tail of `/logsocket` and `/eventsocket`, smoke-tested live). Pyright gate config (`pyrightconfig.json`) at zero findings.
- Six skills: `scaffold`, `deploy`, `debug`, `lint-review`, `test`, and the `hub-config` action router. The `hubs.json` stateful artifact with its owner script `hubs_config.py`, schema doc, and committed `hubs.example.json` (IPs only, no secrets — Maker API credentials stay in the environment).
- Two lift-scoped eval scenarios (`driver-fancontrol-speeds`, `sandbox-import-allowlist`) grading only counterintuitive plugin-specific facts. A measurement pass retired the zero-lift scenarios (the floor model already knows the common idioms) and reshaped criteria to drop universal-competence checks that inflated the baseline; `evals/README.md` documents the honest scope — the plugin's core value is the deterministic layer, which `plugin-evals` says not to eval.
- CI workflow (`.github/workflows/ci.yml`) gating the pyright zero-findings check, unit tests, `tessl plugin lint`, and the changed-skills `tessl skill review` loop; SHA-pinned actions with a Dependabot config renewing the pip and github-actions pins.
- Publish-on-merge (`.github/workflows/publish.yml`): every merge to `main` stamps the CHANGELOG version and publishes to the Tessl registry via `tesslio/patch-version-publish` (which runs the eval suite). Manifest set public (`private: false`).
- Hardened every script failure path to the exit-non-zero + stderr contract (non-JSON hub responses, unwritable output, missing/malformed `hubs.json`, socket resets); shaped through a 12-round cross-family policy review.
