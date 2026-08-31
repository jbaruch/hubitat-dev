---
alwaysApply: true
description: Room Lighting activator devices used to group shades — Act==Off is a valid one-position preset, not a broken toggle
---

# Room Lighting Shade Groups

Room Lighting drives `capability.windowShade` devices, not only lights. Repurposing an RL **activator device** as a **shade group** is a deliberate, Hubitat-staff-endorsed pattern (bravenel, community thread `t/new-app-room-lighting/93098`).

## Reading a shade group's capture table

- The per-device **Position (Act)** column is the shade position sent on activation: **100 = open, 0 = closed**.
- The **Off** column is the position sent on deactivation.
- An RL shade group is a **position preset, not an open/close toggle**.
- **Act == Off is a valid, common configuration** — a one-position group: `0/0` = "always close these", `100/100` = "always open these".

## Nested activator hierarchy

- Per-room shade presets compose into a nested activator hierarchy (`All Shades`, `Dark Shades`, `Light Shades`).
- Some children are open groups (`100/100`), some are close groups (`0/0`); the aggregate plus dedicated open/close rules orchestrate them.
- A child activator whose `Act == Off` is normal. The parent's Act/Off match the children's — no "fight."

## The scheduled off is gated on state.active, not on the lights

- Room Lighting gates its **scheduled off** on its own `state.active` flag, not on what the members are doing.
- When `active == false` the off fires, logs `Already Turned Off`, and sends **no `off()` command**. The light stays on and nothing reports a failure.
- **Any external off sets `active = false`.** Physical switch, Alexa, HomeKit, a dashboard, a `runmethod` during maintenance.
- RL subscribes `allOffHandler` to `switch.off` on its members.
- A subsequent external **on** does not clear it. Only RL's own activation path does.
- One off→on outside RL latches "the room is off" while the room is lit, and every scheduled off until the next real activation is a no-op.
- Diagnose it as `appState.active == false` while a member reads `switch: on`, read from `GET /installedapp/statusJson/<id>`. The confirming log line is `Already Turned Off`.
- Fix it with the bool **`doTurnOff`** ("Turn Off even if already partially off") on `offMeansPage → optionsOffPage`, which bypasses the `active` gate.
- **`prevRunTime` advancing is not evidence the off happened.** The cron fires and the handler runs. The no-op is downstream of both.
- Verify at the member device's event log (`rules/ui-automation.md`).
- **`switch turns on` in `onMeans` is not a self-heal.** It subscribes `switch.on → actHandler` on the separately-picked `switches` list, not on the room's own lights.
- Read `eventSubscriptions` to see which device an option actually watches before reasoning from its name (`rules/app-lifecycle.md`).

## Do not flag as broken

- **`Act position == Off position`** does NOT make a shade RL instance broken — that is a one-position preset.
- **"No path opens the shades in this instance"** does NOT make it broken — opening is done by a different group or rule in the hierarchy.
- Verify intent from the hierarchy and from `position 0 == closed` semantics before calling it a bug. Reading a lighting on/off toggle expectation onto a `windowShade` position-setter is the mistake.
