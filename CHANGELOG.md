# Changelog

## 0.1.0 — 2026-07-14

### Added

- Initial plugin skeleton for `jbaruch/hubitat-dev`: manifest, README with registry badge, and the `sandbox-constraints` rule and `scaffold` skill seeds.
- Grounded the Hubitat code-editor and logging endpoints against live C-8 Pro hubs on platform 2.5.1.125 (Hub Security off): `/hub2/userAppTypes`, `/hub2/userDeviceTypes`, `/hub2/userLibraries` for code enumeration; `/app/ajax/code` and `/driver/ajax/code` for source+version round-trip; `ws://<hub>/logsocket` confirmed streaming structured JSON frames. These shapes seed `reference/endpoints.md`.
