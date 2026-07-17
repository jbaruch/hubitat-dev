# hubs.json — schema

The stateful artifact recording how to reach each Hubitat hub for **code** operations
(deploy, pull, log-tail). Owner: `skills/_scripts/hubs_config.py` (sole writer; owns migration).
Readers: `skills/_scripts/hubclient.py` (`load_hubs`, `resolve_hub`) via `hub_pull.py` / `hub_deploy.py`.

## Shape (schema_version 1)

```json
{
  "schema_version": 1,
  "default": "main",
  "hubs": {
    "main":     { "ip": "192.0.2.10", "port": 8080 },
    "upstairs": { "ip": "192.0.2.11", "port": 8080 },
    "garage":   { "ip": "192.0.2.12", "port": 8080 }
  }
}
```

- `schema_version` (int, required) — stamped on every write; readers that see a version they
  don't accept treat the file as no-usable-state (they never migrate — the owner script does).
- `default` (string or null) — the hub used when a command names none. The first hub added
  becomes the default automatically.
- `hubs` (object) — name → `{ip, port}`. `port` defaults to `8080`.

## Contract

- **Secrets never live here.** Only IPs and ports. Maker API `app_id`/`token`, if used, come
  from the environment (`MAKER_API_APP_ID`, `MAKER_API_TOKEN`), matching the tg-hubitat-bot
  convention — so a committed `hubs.json` leaks nothing.
- **Hints, not authority** (`stateful-artifacts` rule): an IP here is a last-seen value. If a
  hub call fails to connect, the IP may have changed — reconcile against the network, don't
  trust the file blindly.
- **Migration**: only `hubs_config.py` migrates. On reading an older/absent `schema_version`
  it upgrades and rewrites; a newer version than it supports is an error telling the user to
  upgrade the plugin.
