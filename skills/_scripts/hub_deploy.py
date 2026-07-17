#!/usr/bin/env python3
"""Deploy an app or driver's Groovy source to a Hubitat hub (create or update).

Thin CLI over hubclient.HubClient. If --id is given (or --name matches an existing entry),
it updates that entry with optimistic concurrency — the hub's current version is fetched
and sent, and a stale-version rejection surfaces as a conflict to reconcile, never a blind
retry. Otherwise it creates a new entry and reports the assigned id.

Usage:
    hub_deploy.py --kind driver --source d.groovy --hub main --hubs hubs.json
    hub_deploy.py --kind app --source a.groovy --id 143 --ip 192.0.2.10
    hub_deploy.py --kind driver --source d.groovy --name "My Driver" --ip 192.0.2.10

Exit: 0 on success, 2 on a version conflict (re-pull and reconcile), 1 on other errors.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# E402: this import must follow the sys.path insert above so hubclient resolves when run as a script.
from hubclient import HubClient, HubError, DeployConflict, KINDS, resolve_base_from_args  # noqa: E402


def main(argv=None, client_factory=None) -> int:
    p = argparse.ArgumentParser(description="Deploy Hubitat code to a hub.")
    p.add_argument("--kind", required=True, choices=KINDS)
    p.add_argument("--source", required=True, help="path to the .groovy source file")
    p.add_argument("--id", type=int, help="update this existing code id")
    p.add_argument("--name", help="update the existing entry with this name, if present")
    p.add_argument("--ip")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--hub")
    p.add_argument("--hubs")
    args = p.parse_args(argv)

    try:
        source = Path(args.source).read_text()
    except OSError as e:
        print(f"cannot read {args.source}: {e}", file=sys.stderr)
        return 1

    try:
        base = resolve_base_from_args(args.ip, args.port, args.hub, args.hubs)
        client = (client_factory or HubClient)(base)
        code_id = args.id
        if code_id is None and args.name:
            code_id = client.find_id(args.kind, args.name)  # None => create
        result = client.deploy(args.kind, source, code_id=code_id)
    except DeployConflict as e:
        print(str(e), file=sys.stderr)
        return 2
    except HubError as e:
        print(str(e), file=sys.stderr)
        return 1

    json.dump({"kind": args.kind, **result}, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
