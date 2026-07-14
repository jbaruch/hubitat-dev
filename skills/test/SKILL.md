---
name: test
description: Set up and run offline unit tests for a Hubitat app or driver — load the script under biocomp/hubitat_ci, mock the platform executor, and assert on sendEvent/log/parse output off-hub. Use when the user wants to test, unit-test, mock, or add CI for Hubitat Groovy code.
---

# Test Skill

Process steps in order. Do not skip ahead.

Hubitat code can't run on a CI runner, so testing means stubbing the hub runtime around the logic — the `testing-standards` Platform-Bound carve-out. The shape:

```groovy
def script = new HubitatDeviceSandbox(new File("device.groovy")).run(api: Mock(DeviceExecutor))
script.on()
then: 1 * api.sendEvent([name: "switch", value: "on"])
```

`skills/test/example.md` has the full, verified template: the complete build.gradle (hubitat_ci 0.17 on the biocomp Azure feed) plus runnable app and driver Spock tests. Use it.

## Step 1 — Separate logic from platform I/O

Identify the pure logic worth testing — value conversions, `parse()` decoding, state transitions, option handling — versus the thin calls into the platform. The logic is testable; the platform calls get mocked on the executor. If the driver is all I/O with no branching logic, say so — the honest path is then live `debug`. Proceed to Step 2.

## Step 2 — Set up the harness

Add hubitat_ci and Spock per the `example.md` build.gradle (hubitat_ci ships on the biocomp Azure feed, not Maven Central). Create a test that constructs `HubitatAppSandbox`/`HubitatDeviceSandbox` from the script file. `sandbox.run()` compiles it and validates metadata/preferences/capabilities; `sandbox.run(api: mockExecutor)` gives you a mocked `AppExecutor`/`DeviceExecutor` to assert platform calls against. Proceed to Step 3.

## Step 3 — Write deterministic tests

Per `testing-standards`: fixed inputs (a captured Zigbee/Z-Wave description string is a fine fixture), assert outcomes (`1 * api.sendEvent([name: "switch", value: "on"])`), no wall-clock or random data, each test independent. Cover the capability commands and the `parse()` dispatch against known frames. Proceed to Step 4.

## Step 4 — Run, diagnose, wire CI

Run the suite with Gradle. On failure, read the mode:
- a validation error from `sandbox.run()` (bad input type, unsupported API, bad command signature) is a **script** bug — fix the driver, it would fail on the hub too.
- an unmet interaction (`0 * ... sendEvent`) means the code path wasn't reached — check the branch and inputs.
- an `hubitat_ci` stub gap on a newer API — fall back to extracting the logic and testing it with Spock + `groovy.mock.interceptor`.

Once green, wire the suite into the repo's CI so it runs on every change, and document a manual validation procedure for the device-I/O layer that can't run off-hub (what to deploy, what to trigger, what to observe). Finish here.
