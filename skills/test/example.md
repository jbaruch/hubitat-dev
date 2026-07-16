# hubitat_ci — worked example

Verified 2026-07-16 by running it: Gradle 8.4, this build.gradle, the app test below — `tests=2
skipped=0 failures=0`. `HubitatAppSandbox`/`HubitatDeviceSandbox` load a script with GroovyShell,
validate its metadata/preferences/capabilities, and let you drive its methods with a mocked executor.

## build.gradle

hubitat_ci is published on the biocomp Azure feed, not Maven Central or jitpack:

```groovy
plugins { id 'groovy' }

sourceSets { test { groovy { srcDirs = ['src'] } } }   // non-standard layout

// Not optional — see "The JDK 11 ceiling". Gradle itself runs on a modern JDK;
// compile and test fork to the toolchain, so Gradle 9 (needs JDK 17+) still works.
java { toolchain { languageVersion = JavaLanguageVersion.of(11) } }

repositories {
    mavenCentral()
    maven { url 'https://biocomp.pkgs.visualstudio.com/HubitatCiRelease/_packaging/hubitat_ci_feed@Release/maven/v1' }
}

dependencies {
    testImplementation 'org.codehaus.groovy:groovy-all:2.5.4'
    testImplementation 'org.spockframework:spock-core:1.2-groovy-2.5'
    testImplementation 'me.biocomp.hubitat_ci:hubitat_ci:0.17'
}
```

`testCompile` was removed in Gradle 7.0 — a build using it cannot evaluate at all (`Could not find
method testCompile()`), which is a configuration failure, not a test failure.

## The JDK 11 ceiling

**Run this suite on JDK 11.** Verified 2026-07-16 across the JDKs a current machine actually has:

| Toolchain | Result |
|---|---|
| JDK 11 | `tests=2 failures=0` — the suite runs |
| JDK 16 / 17 / 21 | `Could not initialize class org.codehaus.groovy.vmplugin.v7.Java7` |
| JDK 25 | `Unsupported class file major version 69` |

The chain: **hubitat_ci 0.17 is binary-locked to Groovy 2.5, and Groovy 2.5 does not run on JDK 16+.**
hubitat_ci is not merely untested past Groovy 2.5 — it is incompatible, in two independent ways:

- **Groovy 4** removed `groovy.util.slurpersupport.GPathResult`, which hubitat_ci loads →
  `NoClassDefFoundError`. (Groovy 3 still ships the deprecated alias; Groovy 4 does not.)
- **Groovy 3** clears that and then fails on `NoSuchMethodError: ...DefaultGroovyMethods...` — a
  signature hubitat_ci was compiled against and Groovy 3 changed.

On JDK 16+ hubitat_ci additionally reaches into JDK internals it can no longer see
(`sun.util.calendar.ZoneInfo`, then `com.sun.org.apache.xerces.internal.dom.DocumentImpl`), each
needing its own `--add-exports`. Not worth chasing: Groovy 2.5 cannot run there regardless.

This ceiling is hubitat_ci's, not Hubitat's — the hub runs Groovy 2.4, so the harness is still newer
than production and the pin costs nothing in fidelity.

**Renewal:** the three pins move together (Groovy 2.5 ↔ Spock 1.2 ↔ hubitat_ci 0.17), and the
coupling lives in Spock's artifact *name* — `spock-core:1.2-groovy-2.5` — where no scanner can read
it. The Azure feed has no dependency scanner; check the feed's own metadata, which is authoritative
and machine-readable:

```
curl -s 'https://biocomp.pkgs.visualstudio.com/HubitatCiRelease/_packaging/hubitat_ci_feed@Release/maven/v1/me/biocomp/hubitat_ci/hubitat_ci/maven-metadata.xml'
```

As of 2026-07-16 it reports `<release>0.17</release>`; 0.18 returns HTTP 404. Do **not** renew against
the [GitHub releases page](https://github.com/biocomp/hubitat_ci/releases) alone — it tops out at
**v0.16**, older than the pinned 0.17, so it under-reports what the feed actually serves. If a newer
hubitat_ci ever ships, bump all three pins and re-check the JDK ceiling as one focused change.

## App test — validation + a mocked callback

```groovy
import me.biocomp.hubitat_ci.api.app_api.AppExecutor
import me.biocomp.hubitat_ci.api.common_api.Log
import me.biocomp.hubitat_ci.app.HubitatAppSandbox
import spock.lang.Specification

class MyAppTest extends Specification {
    HubitatAppSandbox sandbox = new HubitatAppSandbox(new File("appscript.groovy"))

    def "metadata and preferences validate"() {
        expect: sandbox.run()          // compiles, builds the object, validates definition()/preferences()
    }

    def "installed() logs the configured number"() {
        setup:
            def log = Mock(Log)
            AppExecutor api = Mock { _ * getLog() >> log }
            def script = sandbox.run(api: api, userSettingValues: [Num: 42])
        when:  script.installed()
        then:  1 * log.info("Installed with number = 42")
    }
}
```

## Driver test — assert on sendEvent and parse()

```groovy
import me.biocomp.hubitat_ci.api.device_api.DeviceExecutor
import me.biocomp.hubitat_ci.device.HubitatDeviceSandbox
import spock.lang.Specification

class MyDriverTest extends Specification {
    HubitatDeviceSandbox sandbox = new HubitatDeviceSandbox(new File("device.groovy"))

    def "on() emits switch=on"() {
        setup:
            DeviceExecutor api = Mock()
            def script = sandbox.run(api: api)
        when:  script.on()
        then:  1 * api.sendEvent([name: "switch", value: "on"])
    }

    def "parse() decodes a fixed frame"() {
        setup:
            def script = new HubitatDeviceSandbox(new File("device.groovy")).run()
        expect:
            // fixed input string as fixture — never generated, never time-relative
            script.parse("catchall: 0104 0006 01 ...") == [name: "switch", value: "off"]
    }
}
```

## Notes

- `sandbox.run()` validates by default; pass `validationFlags: [...]` to relax specific checks.
- **The validator is stricter than the hub.** hubitat_ci 0.17 treats `description`, `iconUrl`,
  `iconX2Url` and `iconX3Url` as *mandatory* in `definition()` and fails `sandbox.run()` with
  `mandatory parameters '[iconX3Url]' not set`. The hub does not agree: **14 apps across two live
  hubs run today with no `iconX3Url`** — HubiThings Replica, Life360+ and OwnTracks among them
  (measured 2026-07-16 by reading `/app/ajax/code` for every app in `/hub2/userAppTypes`). Reach for
  `validationFlags` before editing a working app to satisfy a 2019 harness.
- The executor mock (`AppExecutor` / `DeviceExecutor`) is where platform calls land — assert on
  `sendEvent`, `getLog()`, HTTP, scheduling as Spock interactions.
- Where hubitat_ci can't stub a newer API, extract the logic into a plain method and test it with
  Spock + `groovy.mock.interceptor` (`MockFor`/`StubFor`) instead.
