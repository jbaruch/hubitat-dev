# scripts

Deterministic mechanisms the skills call. **Python 3.9+, standard library only — no external
dependencies**, so nothing to install and nothing to pin. Each script is JSON-producing,
self-error-handling (non-zero exit + stderr diagnostic on failure), and has an entry-point guard.

The floor is 3.9 for `zoneinfo` (stdlib from 3.9), which `hub_mesh.py` needs to read a hub's
naive `lastTime` stamps in the hub's own zone. Guarding the import and degrading to UTC was the
alternative and is worse than a clear ImportError: it silently overstates every zwaveJS node's
age by the hub's UTC offset, which is the bug the zone lookup exists to fix. Python 3.8 reached
end-of-life on 2024-10-07; CI runs 3.11.

| Script | Does | Network |
|--------|------|---------|
| `hub_lint.py` | Flag sandbox violations and silent-failure traps in Groovy source | none (offline) |
| `hubclient.py` | Shared: hub config resolution + code enumerate/pull/deploy (imported, not a CLI) | — |
| `hub_pull.py` | Pull an app or driver's source + version from a hub | HTTP |
| `hub_deploy.py` | Deploy source to a hub (create/update, version optimistic-concurrency) | HTTP |
| `hub_logtail.py` | Tail the `/logsocket` or `/eventsocket` websocket, filtered | WebSocket |
| `hub_mesh.py` | Fetch Z-Wave/Zigbee mesh detail **and hub-mesh peer health**; flag failed/ghost nodes, PER, weak routes, unreachable peers; rank staleness | HTTP |
| `hub_radiolog.py` | Tail the `zwaveLogsocket`/`zigbeeLogsocket` for per-frame radio traffic | WebSocket |
| `hub_device_usage.py` | Report where a device is used (blast radius) before removing it | HTTP |
| `hubs_config.py` | Owner of `hubs.json` — init/add/set-default/remove/list hubs (imported + CLI) | — |

The hub endpoints these drive are undocumented and version-sensitive — see `../_reference/endpoints.md`
for what was verified and against which platform. The deterministic cores are unit-tested; only the
socket/HTTP loops touch the network.

## Tests

```
python3 -m unittest discover -s tests -p 'test_*.py'
```

Tests are stdlib `unittest`, deterministic, and network-free — HTTP and websocket layers are
exercised through injected fakes. The live-hub end-to-end checks live in the `deploy`/`debug` skills.
