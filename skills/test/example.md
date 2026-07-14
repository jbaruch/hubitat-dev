# hubitat_ci — worked example

Verified against `biocomp/hubitat_ci` **0.17** and its `hubitat_ci_example` repo (2026-07-14).
`HubitatAppSandbox`/`HubitatDeviceSandbox` load a script with GroovyShell, validate its
metadata/preferences/capabilities, and let you drive its methods with a mocked executor.

## build.gradle

hubitat_ci is published on the biocomp Azure feed, not Maven Central or jitpack:

```groovy
plugins { id 'groovy' }

sourceSets { test { groovy { srcDirs = ['src'] } } }   // non-standard layout

repositories {
    mavenCentral()
    maven { url 'https://biocomp.pkgs.visualstudio.com/HubitatCiRelease/_packaging/hubitat_ci_feed@Release/maven/v1' }
}

dependencies {
    testCompile 'org.codehaus.groovy:groovy-all:2.5.4'
    testCompile 'org.spockframework:spock-core:1.2-groovy-2.5'
    testCompile 'me.biocomp.hubitat_ci:hubitat_ci:0.17'
}
```

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
- The executor mock (`AppExecutor` / `DeviceExecutor`) is where platform calls land — assert on
  `sendEvent`, `getLog()`, HTTP, scheduling as Spock interactions.
- Where hubitat_ci can't stub a newer API, extract the logic into a plain method and test it with
  Spock + `groovy.mock.interceptor` (`MockFor`/`StubFor`) instead.
