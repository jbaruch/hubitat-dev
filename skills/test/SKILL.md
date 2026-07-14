---
name: test
description: Set up and run offline unit tests for a Hubitat app or driver, so its logic is exercised off-hub before deploying. Use when the user wants to test, unit-test, or add CI for Hubitat Groovy code.
---

# Test Skill

Process steps in order. Do not skip ahead.

Hubitat code cannot run on a CI runner — the hub injects a large implicit runtime (`metadata` DSL, `sendEvent`, `runIn`, `httpGet`, device wrappers). Testing off-hub means stubbing that runtime around the deterministic logic. This is the `testing-standards` Platform-Bound carve-out applied to Hubitat: extract and test the CI-runnable logic; document manual validation for the genuinely-unhostable device I/O.

## Step 1 — Separate logic from platform I/O

Identify the pure logic worth testing — value conversions, `parse()` decoding of a fixed frame, state transitions, option handling — versus the thin calls into the platform (`sendEvent`, `zigbee.*`, HTTP). The logic is testable; the platform calls get stubbed. If the driver is all platform I/O with no branching logic, say so — there may be little to unit-test, and the honest answer is the `debug`/live path.

Proceed to Step 2.

## Step 2 — Set up the harness

Use **`biocomp/hubitat_ci`** (Groovy + Gradle) — it loads a script via `GroovyShell`, stubs the Hubitat API surface, and validates metadata/preferences/capabilities plus your own assertions. Add it as a Gradle test dependency and create a Spock/JUnit test that loads the driver and drives the extracted logic with fixed inputs.

Note its maturity: last release is old (v0.16, 2021) and it may lag newer platform APIs. Where it cannot stub a call, fall back to plain Groovy testing (Spock + `groovy.mock.interceptor` `MockFor`/`StubFor`) around the extracted method.

Reference: https://github.com/biocomp/hubitat_ci

Proceed to Step 3.

## Step 3 — Write deterministic tests

Follow `testing-standards`: fixed inputs (a captured Zigbee/Z-Wave description string is a fine fixture), assert outcomes (the `sendEvent` name/value produced), no wall-clock or random data, each test independent. Test the capability commands and the `parse()` dispatch against known frames.

Proceed to Step 4.

## Step 4 — Run and wire CI

Run the suite with Gradle. Once green, wire it into the repo's CI so it runs on every change. Document a manual validation procedure for the device-I/O layer that cannot run off-hub (what to deploy, what to trigger, what to observe). Finish here.
