---
alwaysApply: true
description: Silent-failure traps in Hubitat Groovy the compiler does not catch
---

# Groovy Gotchas

These pass the sandbox compiler and then misbehave at runtime. They are the bulk of "it saved fine but does nothing" reports.

## Handler names are strings

- `subscribe(dev, "motion", "motionHandler")`, `runIn(300, "checkState")`, `schedule(cron, "poll")` reference the handler by a **bare string**. A typo or a renamed-but-not-updated handler compiles clean and fails silently at dispatch time.
- When you rename a handler, grep every subscribe/`runIn`/`runOnce`/`schedule` string for the old name. The `lint-review` skill checks that each such string resolves to a defined method.

## Groovy truth treats 0 as false

- `if (offAfter)` is false for both `null` **and** `0`. Code that means "was a value provided?" but writes `if (setting)` mistakes a legitimate `0` for unset.
- Test presence explicitly: `if (offAfter != null)`.

## Null device inputs

- A non-`required` device input is `null` until selected. `lights.on()` throws when `lights` is null.
- Use safe-navigation (`lights?.on()`) or mark the input `required: true`.

## Reserved and colliding names

- Don't name an input `hubitatQueryString` (reserved — holds the JSON of URL query params).
- Don't shadow built-in methods/objects (`state`, `device`, `location`, `settings`, `log`, `app`).
- `name` + `namespace` in `definition` must be globally unique on the hub.

## Cross-instance data

- A `@Field static` variable is shared across **all** instances of that app/driver and is lost on reboot or code re-save. It is not per-device storage — use `state` for that (see `rules/state-vs-attributes.md`), or a `ConcurrentHashMap` keyed by device id if you genuinely need shared, rebuild-on-boot data.
