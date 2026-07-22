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

## Changing an input's type strands the stored value

- Redeclaring an existing input under a new `type` changes the picker's form, not the value already stored. An instance first saved with `type: "capability.thermostat"` still hands back a `DeviceWrapper` from `settings.thermostat` after the code switches that input to `type: "enum"` (String id) — the new code throws `No signature of method … DeviceWrapper`.
- Compile-clean, and invisible until an **in-place upgrade**. A fresh install stores the new type, and the offline test harness builds `settings` as a plain map with the author's type — both correct. Only a real instance *carried across the type change* holds the old type, so CI and a re-install stay green while the user who upgraded in place is the one who hits it. It took down the config page and would take down `initialize()` and every handler while the instance stayed subscribed.
- Prefer remove-and-recreate over in-place reuse when an input's type changes. Reusing the instance to spare the user re-selecting devices is the trap.
- If in-place must survive, guard at the point of use by type, not just null. For a scalar String id, branch on the type — `String id = (settings.thermostat instanceof String) ? settings.thermostat : null` — never `settings.thermostat.findAll { … }`, which iterates a **String's characters** and yields a list of one-char strings, mangling the id. Filter with `findAll { it instanceof String }` only when the value is a multi-select List. A device value assigned to `String id = settings.foo` coerces via `toString()` (wrong id, no throw); passed to `someMethod(String)` it throws MissingMethod.
- Detect the stale type with `getObjectClassName(v)` — not `getClass()` (sandbox-banned, `rules/sandbox-constraints.md`) — and log it so the cause is visible. Sibling of the `state`-side DeviceWrapper trap (`rules/state-vs-attributes.md`), reappearing from `settings` where newer code expects a scalar. Observed on 2.5.1.128.

## `removeSetting` is deferred — not a same-execution guard

- `app.removeSetting(name)` does not update the in-memory `settings` map within the same execution. A `dropIncompatibleSettings()` that removes the setting then reads it again later in the same render still reads the OLD value and still throws.
- It is correct **cleanup** — the value is gone by the next execution, so the picker re-renders empty for re-selection — but the crash-guard has to be the defensive read above. Use both. `updateSetting`/`removeSetting`/`clearSetting` are otherwise undocumented (`rules/app-lifecycle.md`).

## Reserved and colliding names

- Don't name an input `hubitatQueryString` (reserved — holds the JSON of URL query params).
- Don't shadow built-in methods/objects (`state`, `device`, `location`, `settings`, `log`, `app`).
- `name` + `namespace` in `definition` must be globally unique on the hub.

## `e.statusCode` throws from inside its own getter

- On **2.5.1.x**, a failed `httpGet`/`httpPost` can raise a `groovyx.net.http.HttpResponseException` whose internal `response` is null. `getStatusCode()` dereferences it, so **reading `e.statusCode` throws NPE**.
- Safe navigation does not protect you — `e` is not null, and `e?.statusCode` still enters the getter. The NPE escapes the `catch`, so every recovery below it is dead code and the real error is never logged.
- `e.response?.data` is null for the same reason. A `(statusCode == 500 && e.response?.data?.status?.code == 14)` test cannot match on this platform **even after** the NPE is stopped — fixing only the crash leaves the bug.
- Read it through a guarded helper that try/catches the read itself, falls back to `e?.response?.status`, and returns null when both are unreadable. Branch on a null status with a check that does not need it (compare `atomicState.authTokenExpires` to `now()`).
- Observed on 2.5.1.125 and 2.5.1.128; not observed on 2.5.0.159. That is correlation across one hub, not a bisect — the introducing build is unconfirmed.

## GString keys in `state[...]` are safe

- `state["pending${zone}"] = true` is **not** a bug. The subscript operator normalizes the key to `String` on write, so the entry survives `state`'s JSON round-trip and a later GString lookup hits.
- The Groovy hash-code warning applies to `map.put("pending${zone}", v)` and to GString keys in map literals — not to the subscript assignment path. After an explicit `put`, `map.get("pendingKitchen")` returns **null**.

## Cross-instance data

- A `@Field static` variable is shared across **all** instances of that app/driver and is lost on reboot or code re-save. It is not per-device storage — use `state` for that (see `rules/state-vs-attributes.md`), or a `ConcurrentHashMap` keyed by device id if you genuinely need shared, rebuild-on-boot data.
