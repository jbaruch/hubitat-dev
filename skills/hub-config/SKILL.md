---
name: hub-config
description: Manage the hubs.json config that records how to reach each Hubitat hub by IP for code operations. Actions — init a config, add a hub, set the default hub, remove a hub, list hubs. Use when the user wants to configure, register, add, or list Hubitat hubs for deploy/pull/debug.
argument-hint: "[init|add|set-default|remove|list] [hub-name]"
---

# Hub-Config Skill

This skill is an action router — pick the step that matches the user's intent and execute only that step. Do not run other steps; do not parallelize.

`hubs.json` is the stateful artifact that tells the deploy/pull/debug flows how to reach each hub. Code operations are per-hub by IP (`multi-hub-topology` rule). The file holds **IPs only, never secrets** — Maker API credentials come from the environment. `skills/_scripts/hubs_config.py` is the sole writer of its shape and owns schema migration; the schema lives in `skills/hub-config/state-schema.md`. All actions below call it; its argument and output contract is in its module docstring.

## Step 1 — Init

Create a fresh, empty config (refuses to overwrite an existing one without `--force`):

```
python3 .tessl/plugins/jbaruch/hubitat-dev/skills/_scripts/hubs_config.py init [--path hubs.json]
```

Then chain to **Add** to register the first hub.

## Step 2 — Add a hub

```
python3 .tessl/plugins/jbaruch/hubitat-dev/skills/_scripts/hubs_config.py add --name <name> --ip <addr> [--port 8080] [--default]
```

The first hub added becomes the default automatically. Creates the file if it does not exist. Finish here.

## Step 3 — Set the default hub

```
python3 .tessl/plugins/jbaruch/hubitat-dev/skills/_scripts/hubs_config.py set-default --name <name>
```

Finish here.

## Step 4 — Remove a hub

```
python3 .tessl/plugins/jbaruch/hubitat-dev/skills/_scripts/hubs_config.py remove --name <name>
```

If the removed hub was the default, another becomes default automatically. Finish here.

## Step 5 — List hubs

```
python3 .tessl/plugins/jbaruch/hubitat-dev/skills/_scripts/hubs_config.py list
```

Print the configured hubs and which is default. Finish here.
