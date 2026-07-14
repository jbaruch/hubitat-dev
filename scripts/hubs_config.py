#!/usr/bin/env python3
"""Owner script for the hubs.json stateful artifact — the only writer of its shape.

hubs.json records how to reach each hub for CODE operations (per-hub by IP; devices can
mesh but code cannot — see the multi-hub-topology rule). It holds IPs only, never secrets:
Maker API credentials come from the environment, not this file.

Schema (schema_version 1):
    {"schema_version": 1,
     "default": "main" | null,
     "hubs": {"<name>": {"ip": "192.168.30.2", "port": 8080}, ...}}

Actions (deterministic JSON mutation): init, add, set-default, remove, list, validate.
On read, an older/absent schema_version is migrated up and rewritten (this script owns
migration; readers treat an unrecognized version as no-usable-state).

Usage:
    hubs_config.py init [--path hubs.json] [--force]
    hubs_config.py add --name main --ip 192.168.30.2 [--port 8080] [--default] [--path ...]
    hubs_config.py set-default --name main [--path ...]
    hubs_config.py remove --name main [--path ...]
    hubs_config.py list [--path ...]
    hubs_config.py validate [--path ...]
Output: the resulting config as JSON on stdout. Exit non-zero with a stderr message on error.
"""
import argparse
import json
import sys
from pathlib import Path

SCHEMA_VERSION = 1


def migrate(cfg: dict) -> dict:
    """Upgrade an older/absent-version config to the current schema. Owner-only."""
    if "schema_version" not in cfg:
        cfg["schema_version"] = SCHEMA_VERSION
    cfg.setdefault("hubs", {})
    cfg.setdefault("default", None)
    if cfg["schema_version"] > SCHEMA_VERSION:
        raise ValueError(
            f"hubs.json schema_version {cfg['schema_version']} is newer than this script "
            f"supports ({SCHEMA_VERSION}); upgrade the plugin.")
    cfg["schema_version"] = SCHEMA_VERSION
    return cfg


def load(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist — run `hubs_config.py init` first")
    return migrate(json.loads(path.read_text()))


def save(path: Path, cfg: dict) -> None:
    path.write_text(json.dumps(cfg, indent=2) + "\n")


def empty_config() -> dict:
    return {"schema_version": SCHEMA_VERSION, "default": None, "hubs": {}}


def add_hub(cfg: dict, name: str, ip: str, port: int = 8080, make_default: bool = False) -> dict:
    cfg["hubs"][name] = {"ip": ip, "port": port}
    if make_default or cfg.get("default") is None:
        cfg["default"] = name
    return cfg


def set_default(cfg: dict, name: str) -> dict:
    if name not in cfg["hubs"]:
        raise ValueError(f"no hub named {name!r} (have: {', '.join(sorted(cfg['hubs'])) or 'none'})")
    cfg["default"] = name
    return cfg


def remove_hub(cfg: dict, name: str) -> dict:
    if name not in cfg["hubs"]:
        raise ValueError(f"no hub named {name!r}")
    del cfg["hubs"][name]
    if cfg.get("default") == name:
        cfg["default"] = next(iter(cfg["hubs"]), None)
    return cfg


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Manage the hubs.json config.")
    p.add_argument("action", choices=["init", "add", "set-default", "remove", "list", "validate"])
    p.add_argument("--path", default="hubs.json")
    p.add_argument("--name")
    p.add_argument("--ip")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--default", action="store_true")
    p.add_argument("--force", action="store_true")
    args = p.parse_args(argv)
    path = Path(args.path)

    try:
        if args.action == "init":
            if path.exists() and not args.force:
                raise ValueError(f"{path} already exists — pass --force to overwrite")
            cfg = empty_config()
        elif args.action == "add":
            if not (args.name and args.ip):
                raise ValueError("add requires --name and --ip")
            cfg = migrate(json.loads(path.read_text())) if path.exists() else empty_config()
            cfg = add_hub(cfg, args.name, args.ip, args.port, args.default)
        elif args.action == "set-default":
            if not args.name:
                raise ValueError("set-default requires --name")
            cfg = set_default(load(path), args.name)
        elif args.action == "remove":
            if not args.name:
                raise ValueError("remove requires --name")
            cfg = remove_hub(load(path), args.name)
        else:  # list / validate
            cfg = load(path)
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as e:
        print(str(e), file=sys.stderr)
        return 1

    if args.action not in ("list", "validate"):
        save(path, cfg)
    json.dump(cfg, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
