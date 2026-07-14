---
name: lint-review
description: Run the Hubitat sandbox linter on app or driver Groovy and judge each finding — real defect vs. false positive — before the code goes near a hub. Use when the user wants to lint, check, or validate Hubitat code, or automatically before deploying.
---

# Lint-Review Skill

Process steps in order. Do not skip ahead.

The linter is deterministic and offline; it emits **candidates**, not verdicts (the checks are heuristic — `sandbox-constraints` and `groovy-gotchas` rules describe what they target). This skill runs it and applies judgment to each finding.

## Step 1 — Run the linter

```
python3 .tessl/plugins/jbaruch/hubitat-dev/scripts/hub_lint.py <file.groovy>
```

Check list, severity meanings, and output shape: `scripts/hub_lint.py` module docstring. Output is JSON `{kind, finding_count, findings:[...]}`. If `finding_count` is 0, report the code lints clean and finish.

## Step 2 — Judge each finding

For each finding, decide real vs. false positive:
- `missing-command` / `unresolved-handler` (**error**) — almost always real: a required command has no method, or a handler string resolves to nothing (the silent-failure trap). Exception: a method provided by an included library (`#include`), which the offline linter cannot see — verify before dismissing.
- `forbidden-construct` (**error**) — real: the sandbox blocks it. Apply the named fix (`pauseExecution` for `sleep`, `log.debug` for `println`, `getObjectClassName` for `getClass`).
- `disallowed-import` (**warn**) — a candidate only: the documented allow-list is incomplete, so judge whether the class is genuinely unavailable. Platform (`hubitat.*`) imports are already excluded by the linter.
- `install-trap` (**warn**) — real when the app's subscriptions live only in `updated()`; confirm `installed()` calls `updated()`.

Proceed to Step 3.

## Step 3 — Report or fix

Report the real findings with file:line and the fix for each; note any dismissed as false positives with the reason. If invoked as a pre-deploy gate, return control to the caller with the verdict. Otherwise offer to apply the fixes. Finish here.
