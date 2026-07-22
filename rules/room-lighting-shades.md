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

## Do not flag as broken

- **`Act position == Off position`** does NOT make a shade RL instance broken — that is a one-position preset.
- **"No path opens the shades in this instance"** does NOT make it broken — opening is done by a different group or rule in the hierarchy.
- Verify intent from the hierarchy and from `position 0 == closed` semantics before calling it a bug. Reading a lighting on/off toggle expectation onto a `windowShade` position-setter is the mistake.
