---
name: deploy
description: Deploy a Hubitat app or driver's Groovy source to a hub and confirm it saved and runs by watching the log stream. Use when the user wants to deploy, push, upload, or install app/driver code onto a Hubitat hub, or iterate the edit-deploy-check loop.
---

# Deploy Skill

Process steps in order. Do not skip ahead.

This replaces the copy-paste-into-the-browser loop. Code operations are per-hub by IP (`multi-hub-topology` rule). Hub connection details come from `hubs.json` (see `Skill(skill: "hub-config")`); pass `--ip` directly if there is no config yet.

## Step 1 — Identify the target

Confirm: the source file, the `kind` (`app`, `driver`, or `library`), the target hub (a `hubs.json` name via `--hub`, or an `--ip`), and whether this is a new entry or an update to an existing one. If updating an existing entry whose id is unknown, it is matched by the `name` in the source's `definition`.

Proceed to Step 2.

## Step 2 — Lint before deploy

Run `Skill(skill: "lint-review")` on the source first. A deploy of code with a `missing-command` or bad import wastes a round-trip — the hub rejects malformed code on save. If lint is clean or the user accepts the findings, proceed to Step 3.

## Step 3 — Deploy

```
python3 .tessl/plugins/jbaruch/hubitat-dev/scripts/hub_deploy.py \
    --kind <kind> --source <file.groovy> [--name "<name>" | --id <id>] [--hub <name> | --ip <addr>]
```

Argument and output contract, and the create-vs-update decision: `scripts/hub_deploy.py` module docstring; the shared logic is in `scripts/hubclient.py`. The script prints `{action, id, ...}` on success.

- **Exit 2 is a version conflict**, not a failure to retry blindly: the hub has a newer version than the local copy. Pull the hub's current source (`scripts/hub_pull.py`), reconcile the difference with the user, then deploy again. Do not loop the deploy.
- Exit 1 is another error — report the stderr message.

Proceed to Step 4.

## Step 4 — Confirm via the log stream

A successful save means the code compiled, not that it behaves. Confirm runtime behavior by watching the hub while exercising the code — hand off to `Skill(skill: "debug")` filtered to this app/driver's name. For a driver, have the user (or Maker API) trigger a device command; for an app, press Done / fire the trigger. Finish here.
