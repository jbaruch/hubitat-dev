# evals

Lift-gated scenarios for the LLM-judgment surface of this plugin. Read against
`coding-policy` `rules/plugin-evals.md` ("Lift, Not Attainment").

## Honest scope

Most of this plugin's value is in its **deterministic layer** — the grounded hub
endpoints, the sandbox linter, the capability/allow-list reference data. Per
`plugin-evals`, a skill whose decisional core is a unit-tested script has **no
LLM-side surface to eval**, so deploy/pull/log-tail/lint/hub-config are covered by
their unit tests, not by scenarios here.

That leaves a narrow genuine eval surface: places where the plugin gives the model a
**specific fact it would otherwise guess wrong**. A first measurement pass confirmed
the surface is narrow — a capable floor model (`deepseek-v4-flash`) already knows the
common Hubitat idioms (the reinitialize idiom, Switch/SwitchLevel, most of the
FanControl enum), so scenarios testing those showed **zero lift** and were retired.
The two scenarios kept here target only the counterintuitive, plugin-specific facts a
baseline reliably gets wrong.

## Scenarios

- **driver-fancontrol-speeds** — writing a multi-speed fan driver. Grades ONLY the two
  discriminating facts: the exact FanControl enum includes `medium-low`/`medium-high`
  (baseline drops them), and the `supportedFanSpeeds` attribute is emitted (baseline
  omits it). The universal-competence parts (declaring the capability, `setSpeed`/
  `cycleSpeed`) are deliberately unscored — they measured 100% baseline and only
  inflate the score.
- **sandbox-import-allowlist** — classifying imports against the sandbox allow-list.
  Grades the counterintuitive cases: `ConcurrentHashMap` is allowed (feels blocked),
  `HttpURLConnection`/`File`/`Thread` are rejected. The obvious-allowed cases are
  unscored.

## Criteria discipline

Each criterion grades one plugin-prescribed fact, never a technique the task states
(no bleeding) and never universal competence (no baseline-inflation). Weights sum to
100. The publish pipeline runs the suite; regressions block the publish.
