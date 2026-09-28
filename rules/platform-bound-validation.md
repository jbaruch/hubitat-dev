---
alwaysApply: true
description: Behavior-scoped authority for testing Hubitat runtime paths that CI cannot host faithfully
---

# Platform-Bound Validation

## Authority

- This rule is the consuming-plugin authority of record for `testing-standards`' Platform-Bound Untestable Carve-Out.
- Exemption attaches to runtime behavior, never to a source filename, directory, app, or driver as a whole.
- A matching code path stays covered after a move or rename.
- A new matching code path enters the same contract without a rule edit.

## Exempt Behavior Classes

- Scheduler registration, persistence, cancellation, restoration, and callback dispatch through Hubitat scheduling APIs.
- App and driver lifecycle dispatch by the hub, including install, update, startup, and state restoration across executions.
- Live device subscription, event delivery, platform-generated event metadata, and physical device or radio interaction.
- Only the smallest invocation layer for a listed behavior is exempt.
- Deterministic decisions in the same source artifact remain subject to CI tests.

## Required CI Coverage

- Extract and test parsing, normalization, state transitions, cutoff calculations, eligibility rules, and result classification.
- Stub platform calls when the test can assert the requested call or resulting state without claiming the hub executed it.
- Use an injected clock for schedule calculations. A live scheduler is not required to test time arithmetic.
- Missing harness support, setup effort, and mixed platform-plus-logic files do not qualify a deterministic path for exemption.

## Live-Hub Procedure

- Keep the validation index at `docs/manual-validation.md` in each consuming repository.
- Name each procedure by stable runtime behavior and user-visible outcome, not by source filename.
- Record setup, trigger, observation surface, pass criteria, and any required cleanup.
- Link the applicable procedure from the pull request test plan.
- Record the observed result in the pull request test plan.
- A code-path move needs no documentation change when the behavior and observable contract stay unchanged.
