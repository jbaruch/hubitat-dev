---
alwaysApply: true
description: How code vs. devices differ across multiple Hubitat hubs, and the deploy concurrency stamp
---

# Multi-Hub Topology

Hubitat's two planes behave differently across a multi-hub setup. Conflating them is why "I deployed to the hub" sometimes hits the wrong one.

## Code is per-hub; devices can mesh

- **Code** (apps, drivers, libraries) lives in each hub's own editor. There is no mesh for code — deploy, pull, and log-tail target one hub **by IP**. A driver installed on hub A does not exist on hub B until deployed there too.
- **Devices** can mesh: with hub meshing, one Maker API instance can expose devices from secondary hubs. Device *control* can be centralized; code *deployment* cannot.
- Hub connection details are keyed by hub, one IP each; the `hub-config` skill owns that config.

## The owning hub exports to HomeKit

- **The hub that physically owns a device exports it.**
- **Never export a hub-mesh mirror.** It lands the accessory on the wrong bridge.
- Exporting a mirror succeeds, which is what makes it a trap. The mirror sits on the consuming hub already, looking exportable.
- Read both bridges' export lists before exporting (`skills/_reference/endpoints.md`).
- Never read the device-level `homeKitEnabled` field as export state. It is `null` on correctly-exported sensors.
- HomeKit caps a bridge at **149 accessories + the bridge**. On a multi-hub setup that decides the layout, not a preference (`skills/_reference/homekit-mdns-network.md`).

## Local network, no Hub Security (assumed default)

- These rules and the deploy/log-tail mechanisms assume hubs on the local network with Hub Security off — no login, no cookie, direct IP access (verified baseline in `skills/_reference/endpoints.md`).
- If a hub enables Hub Security, every hub HTTP/WS call needs a session cookie from `POST /login` first; the mechanisms must switch to the authenticated path.

## Deploy is optimistically concurrent

- The `version` integer returned by `/app/ajax/code` and `/driver/ajax/code` is bumped on every save and is the concurrency stamp. An update must send the **current** version.
- If the hub rejects the version, a newer edit exists on the hub — re-pull and reconcile. Never blindly retry with an incremented number; that is how you clobber a change made in the web editor.
- The endpoints are undocumented and version-sensitive — re-verify after a platform update (`skills/_reference/endpoints.md`).
- The same `version` optimistic-concurrency pattern is **not code-only**. `POST /device/update` (device rename/edit) carries a per-device `version` stamp with the identical read-fresh-then-send rule.
- Do not hand-build that POST.
- `skills/_scripts/hub_device_update.py` owns the fresh-`version` read, the field set and the boolean encoding (`rules/device-lifecycle.md`).
