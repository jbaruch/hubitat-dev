---
alwaysApply: true
description: What the Hubitat Groovy 2.4 sandbox forbids and the allowed-imports allow-list
---

# Sandbox Constraints

Hubitat apps and drivers are single Groovy 2.4 files run in a locked-down sandbox on the hub. The language looks like Groovy but the environment is not a general JVM. Save compiles; a compile error is returned inline and the code does not save.

## Forbidden

- No user-defined classes. One script per app/driver; `@Field` gives script-level variables when you need them.
- No `new Thread(...)`, executors, or any thread creation.
- No `sleep()` — use `pauseExecution(ms)`. No `println()` — use `log.debug`.
- No `obj.getClass()` — use `getObjectClassName(obj)`.
- No arbitrary Java classes. Only the import allow-list resolves; anything else fails to compile.

## Allowed Imports

- The allow-list is `skills/_reference/allowed-imports.txt` (197 fully-qualified classes, verified against 2.5.1.125). An import outside it is a compile error, not a runtime one.
- Highlights present: `java.util.*` collections, `java.math.BigDecimal/BigInteger`, `java.time.*`, `groovy.json.*` (`JsonSlurper`, `JsonOutput`, `JsonBuilder`), `groovy.transform.Field`/`CompileStatic`, `java.security.MessageDigest`, `javax.crypto.*`, `org.apache.commons.codec.binary.Base64`, `com.hubitat.app.*` wrappers, `hubitat.scheduling.AsyncResponse`, Nimbus JOSE/JWT, `org.json.*`, `org.quartz.CronExpression`.
- Absent by design: file I/O, sockets, reflection, `java.lang.Thread`, arbitrary `org.*`.

## Consequences

- Groovy 2.4 only — no language features from later versions.
- `name` + `namespace` in `definition` must be globally unique on the hub or the save collides.
- Deterministic sandbox checks (imports, `sleep`/`println`/thread usage, `getClass`) are what the `lint-review` skill runs — see `rules/groovy-gotchas.md` for the silent-failure traps the compiler does *not* catch.
