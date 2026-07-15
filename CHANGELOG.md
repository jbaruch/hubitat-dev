# Changelog

### Added

- Z-Wave and Zigbee **mesh-health** diagnostics: the `mesh-health` skill and `scripts/hub_mesh.py` fetch `/hub/zwaveDetails/json` and `/hub/zigbeeDetails/json` and flag failed/ghost nodes, nonzero PER, dead/incomplete Zigbee joins, and an unhealthy network, ranking nodes worst-first by PER/RTT/RSSI rather than against absolute cutoffs Hubitat does not publish. The new always-on `zwave-zigbee-mesh` rule carries the grounded field meanings and the load-bearing gotcha that `lwrRssi` is reported on two different scales (zwaveJS absolute dBm vs legacy dB-above-noise), so a fixed numeric cutoff is wrong across backends. Endpoints and field shapes verified live on 2.5.1.125 across a zwaveJS and a legacy hub; metric meanings and protocol constants grounded in Hubitat docs, Silicon Labs Z-Wave 700/800 sensitivity, and IEEE 802.15.4.

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
