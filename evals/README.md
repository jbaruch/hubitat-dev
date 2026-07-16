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
**specific fact it would otherwise guess wrong**, or a **specific way of reading
evidence** it would otherwise not invent. A first measurement pass confirmed the
surface is narrow — a capable floor model (`deepseek-v4-flash`) already knows the
common Hubitat idioms (the reinitialize idiom, Switch/SwitchLevel, most of the
FanControl enum), so scenarios testing those showed **zero lift** and were retired.
The scenarios kept here target only the counterintuitive, plugin-specific material a
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
- **mesh-health-command-path-not-radio** — diagnosing a hub whose radio report is a
  false all-clear. The only scenario here that grades a *way of reading evidence*
  rather than a fact. The fixture is engineered so every axis is measured and every
  axis is green (`summary {critical:0, warnings:0}`, no FAILED nodes, no packet
  errors, hub-mesh peers probing clean) while every actuator sits frozen 13.7 h in a
  47-second window and every reporter stays fresh — across *both* radios. Grades the
  actuator-vs-reporter split (`rules/zwave-zigbee-mesh.md` The command path,
  `skills/mesh-health/SKILL.md` Step 4): name the command path, not a radio fault;
  use reporter freshness as proof the radio works; refuse the all-clear the zero
  counters invite; prescribe no repair/repeater/re-pair. This is the grounded 0.1.7
  outage (see CHANGELOG) turned into a measurement.

## Criteria discipline

Each criterion grades one plugin-prescribed fact or reading, never a technique the
task states (no bleeding) and never universal competence (no baseline-inflation).
Weights sum to 100. The publish pipeline runs the suite; regressions block the
publish.

A scenario needing input files ships them under `<scenario>/inputs/`, declared via
`{"include": ["./inputs"]}` in the scenario's `scenario.json`; the runner copies them
into the agent's working directory. Fixtures carry a date in the filename and are
synthetic — no live hub data.
