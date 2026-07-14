# Changelog

## 0.1.0 — 2026-07-14

### Added

- Initial plugin skeleton for `jbaruch/hubitat-dev`: manifest, README with registry badge, and the `sandbox-constraints` rule and `scaffold` skill seeds.
- Grounded the Hubitat code-editor and logging endpoints against live C-8 Pro hubs on platform 2.5.1.125 (Hub Security off): `/hub2/userAppTypes`, `/hub2/userDeviceTypes`, `/hub2/userLibraries` for code enumeration; `/app/ajax/code` and `/driver/ajax/code` for source+version round-trip; `ws://<hub>/logsocket` confirmed streaming structured JSON frames. These shapes seed `reference/endpoints.md`.
- Bundled `reference/` data: `capabilities.json` (all 102 capabilities → attributes/commands/params, parsed from the authoritative capability list), `allowed-imports.txt` (the 197-class sandbox allow-list), `endpoints.md` (the grounded hub endpoint catalog), and `input-types.md` (app + driver preference input types).
- Seven always-on rules: `sandbox-constraints`, `app-lifecycle`, `driver-lifecycle`, `logging-conventions`, `state-vs-attributes`, `groovy-gotchas`, `multi-hub-topology`.
