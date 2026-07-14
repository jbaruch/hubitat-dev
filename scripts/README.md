# scripts

Deterministic mechanisms the skills call. **Python 3.8+, standard library only — no external
dependencies**, so nothing to install and nothing to pin. Each script is JSON-producing,
self-error-handling (non-zero exit + stderr diagnostic on failure), and has an entry-point guard.

| Script | Does | Network |
|--------|------|---------|
| `hub_lint.py` | Flag sandbox violations and silent-failure traps in Groovy source | none (offline) |
| `hubclient.py` | Shared: hub config resolution + code enumerate/pull/deploy (imported, not a CLI) | — |
| `hub_pull.py` | Pull an app or driver's source + version from a hub | HTTP |
| `hub_deploy.py` | Deploy source to a hub (create/update, version optimistic-concurrency) | HTTP |
| `hub_logtail.py` | Tail the `/logsocket` or `/eventsocket` websocket, filtered | WebSocket |

The hub endpoints these drive are undocumented and version-sensitive — see `../reference/endpoints.md`
for what was verified and against which platform. The deterministic cores are unit-tested; only the
socket/HTTP loops touch the network.

## Tests

```
python3 -m unittest discover -s scripts/tests -p 'test_*.py'
```

Tests are stdlib `unittest`, deterministic, and network-free — HTTP and websocket layers are
exercised through injected fakes. The live-hub end-to-end checks live in the `deploy`/`debug` skills.
