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

`skills/test/example.md` has the full template — build.gradle (hubitat_ci 0.17 on the biocomp Azure feed) plus runnable app and driver Spock tests — verified 2026-07-16 by running it green. Use it, including its **JDK 11 toolchain pin**: hubitat_ci 0.17 is binary-locked to Groovy 2.5, which does not run on JDK 16+, so the suite cannot execute on a default modern JDK. `example.md` has the evidence and the failure signatures.

## Step 1 — Separate logic from platform I/O

Identify the pure logic worth testing — value conversions, `parse()` decoding, state transitions, option handling — versus the thin calls into the platform. The logic is testable; the platform calls get mocked on the executor. If the driver is all I/O with no branching logic, say so — the honest path is then live `debug`. Proceed to Step 2.

## Step 2 — Set up the harness

Add hubitat_ci and Spock per the `example.md` build.gradle (hubitat_ci ships on the biocomp Azure feed, not Maven Central), and keep its JDK 11 toolchain pin. Create a test that constructs `HubitatAppSandbox`/`HubitatDeviceSandbox` from the script file. `sandbox.run()` compiles it and validates metadata/preferences/capabilities; `sandbox.run(api: mockExecutor)` gives you a mocked `AppExecutor`/`DeviceExecutor` to assert platform calls against. Proceed to Step 3.

## Step 3 — Write deterministic tests

Per `testing-standards`: fixed inputs (a captured Zigbee/Z-Wave description string is a fine fixture), assert outcomes (`1 * api.sendEvent([name: "switch", value: "on"])`), no wall-clock or random data, each test independent. Cover the capability commands and the `parse()` dispatch against known frames. Proceed to Step 4.

## Step 4 — Run the suite

Run the suite with Gradle. On failure, read the mode:
- a validation error from `sandbox.run()` (bad input type, unsupported API, bad command signature) is usually a **script** bug — fix the driver, it would fail on the hub too. **Confirm before editing the app**: hubitat_ci's validator is stricter than the hub on `definition()` metadata, so `mandatory parameters '[iconX3Url]' not set` is the harness talking, not the platform (`example.md` Notes). Relax it with `validationFlags`; never add junk to a working app to satisfy the harness.
- a build that cannot evaluate, or dies before any spec runs, is a **toolchain** failure, not a code one — check the JDK ceiling first (`example.md`).
- an unmet interaction (`0 * ... sendEvent`) means the code path wasn't reached — check the branch and inputs.
- an `hubitat_ci` stub gap on a newer API — fall back to extracting the logic and testing it with Spock + `groovy.mock.interceptor`.

Iterate until green, then proceed to Step 5.

## Step 5 — Wire into CI

Wire the green suite into the repo's CI so it runs on every change. **Install a JDK 11 in CI** — the toolchain pin selects a JDK, it does not provide one, and a runner without it fails with `No matching toolchains found`. Provision it explicitly (`actions/setup-java` with `java-version: 11`, kept alongside the JDK the Gradle runtime needs), or enable Gradle's toolchain auto-provisioning. Never drop the pin to match whatever JDK the runner ships — that is the JDK ceiling reasserting itself, and the suite will not run.

Document a manual validation procedure for the device-I/O layer that can't run off-hub (what to deploy, what to trigger, what to observe). Finish here.
