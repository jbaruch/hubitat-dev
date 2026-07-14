#!/usr/bin/env python3
"""Pull an app or driver's source and version from a Hubitat hub.

Thin CLI over hubclient.HubClient (which carries the tested logic). Resolves the target
by --id or by --name (enumerated match). With --out, writes the Groovy there and prints
metadata JSON to stdout; without it, prints metadata JSON with the source included.

Usage:
    hub_pull.py --kind driver --name "My Driver" --hub main --hubs hubs.json [--out d.groovy]
    hub_pull.py --kind app --id 143 --ip 192.168.30.2
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hubclient import HubClient, HubError, KINDS, resolve_base_from_args  # noqa: E402


def main(argv=None, client_factory=None) -> int:
    p = argparse.ArgumentParser(description="Pull Hubitat code from a hub.")
    p.add_argument("--kind", required=True, choices=KINDS)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--id", type=int)
    g.add_argument("--name")
    p.add_argument("--ip")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--hub")
    p.add_argument("--hubs")
    p.add_argument("--out")
    args = p.parse_args(argv)

    try:
        base = resolve_base_from_args(args.ip, args.port, args.hub, args.hubs)
        client = (client_factory or HubClient)(base)
        code_id = args.id if args.id is not None else client.find_id(args.kind, args.name)
        if code_id is None:
            print(f"no {args.kind} named {args.name!r} on the hub", file=sys.stderr)
            return 1
        result = client.pull(args.kind, code_id)
    except HubError as e:
        print(str(e), file=sys.stderr)
        return 1

    meta = {"kind": args.kind, "id": result["id"], "name": result["name"],
            "version": result["version"]}
    if args.out:
        try:
            Path(args.out).write_text(result["source"])
        except OSError as e:
            print(f"cannot write {args.out}: {e}", file=sys.stderr)
            return 1
        meta["out"] = args.out
    else:
        meta["source"] = result["source"]
    json.dump(meta, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
