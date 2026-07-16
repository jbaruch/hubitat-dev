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

## Measured and not kept

Absence is a result. `plugin-evals` makes lift an **admission gate**, so a candidate is run
before it is committed and a flat one never lands. What was measured and rejected is
recorded here, so the same candidate is not re-proposed as an obvious gap.

- **device-migration, the swap-blocked diagnosis** (#23, measured 2026-07-16 against
  `hubitat-dev@0.1.11`). Candidate: the `device-migration` Step 3 fork — *why* a device is
  absent from Settings → Swap Device, which decides which fallback can work. A child device
  was the case, where the plugin's counterintuitive claim is that a virtual hop **provably
  cannot** help — the last swap of any chain still targets the child.
  **Baseline 100%, with-context 100% — zero lift** (`deepseek-v4-flash`, 3 runs each). Not
  shipped.

  The predicted baseline failure did not occur. The floor model read the `/hub2/devicesList`
  tree, found the device nested in its parent's `children[]`, concluded child devices are not
  offered for swap, and went **straight to the manual path** — never reaching for the virtual
  hop the ladder was supposed to bait it into. It also named the affected apps and both
  dashboards from the usage capture unaided.

  The lesson generalizes past this scenario. The fixture had to make the device's parentage
  *genuinely inferable from the data*, or the diagnosis would have been a guess — and anything
  genuinely inferable, a capable model infers. That is `plugin-evals` cause 1 (coincidence with
  universal competence), and the retire is the prescribed outcome, not a failure of the
  scenario's construction.

  One fix was considered and rejected: tightening the virtual-hop criterion to demand an
  *explicit* rebuttal would have manufactured ~34 points of lift: the with-context run rejects
  the hop by name while the baseline never raises it. That grades **verbosity, not
  correctness** — both answers walk the owner to the same correct migration — and
  `plugin-evals` is explicit: *"do not rewrite toward 'testing reasoning' if baseline already
  reasons to the same outcome."*

  What would still be worth measuring is the **parent-app** child (a Hue/CoCoHue device, which
  is *not* indented anywhere and whose only tell is a `parentApp` row), where the tree
  inference that carried the baseline here is unavailable. That case is **not grounded yet**:
  the live 2.5.1.128 verification covered parent-*device* children only, and inventing the
  grounding to make an eval work is how a fixture starts lying.

## Criteria discipline

Each criterion grades one plugin-prescribed fact or reading, never a technique the
task states (no bleeding) and never universal competence (no baseline-inflation).
Weights sum to 100. The publish pipeline runs the suite; regressions block the
publish.

A scenario needing input files ships them under `<scenario>/inputs/`, declared via
`{"include": ["./inputs"]}` in the scenario's `scenario.json`; the runner copies them
into the agent's working directory. Fixtures carry a date in the filename and are
synthetic — no live hub data.

## Generated fixtures

A fixture that is meant to be a shape one of this plugin's scripts **actually emits** is
generated, never hand-written: synthetic raw hub JSON pushed through the real analyzer
with an injected clock, so the output shape and every computed counter come from the
shipped code rather than from an author's belief about it. `mesh-health-command-path-not-radio`
depends on that property — a *false all-clear* is its premise, so a hand-asserted
`summary {critical:0, warnings:0}` would beg the question the scenario poses.

The generator lives at `<scenario>/generate.py`, beside the fixture it writes:

```
python3 evals/mesh-health-command-path-not-radio/generate.py          # rewrite the fixture
python3 evals/mesh-health-command-path-not-radio/generate.py --check  # exit 1 on drift
```

It is authoring tooling, not a plugin surface — `.tesslignore`d out of the published
plugin, and deliberately not a `scripts/` module (that would pull a production test
burden onto a one-fixture tool). CI runs `--check` over every `evals/*/generate.py`, so a
fixture that stops matching its generator fails the build instead of rotting quietly. When
an analyzer's contract changes, **regenerate — do not hand-patch the JSON.** Hand-patching
forfeits the exact property that makes the fixture worth trusting.
