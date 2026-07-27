---
alwaysApply: true
description: A cloud integration's attributes are not all measurements — some are model output computed from hand-entered config; tell measured from computed before trusting or correcting them
---

# Self-Reported vs. Measured

A cloud integration's child-device attributes arrive through one `sendEvent` path and render identically in the Current States table, yet some are sensor readings and some are model output computed from configuration a human typed once and never validated. Reading a computed value as evidence launders a guess into a fact.

## Measured or computed

- Before trusting any attribute from a cloud integration, ask whether the upstream service **measures** it or **computes** it. Both look identical from Hubitat.
- A computed attribute is model output; its inputs are hand-entered config (crop type, nozzle, slope, area), each entered once with no validation.
- Plausibility is not evidence. An identical value across many units with differing inputs is evidence the model echoes config, not the ground.
- When an app writes a derived attribute a rule might subscribe to, name it so a future reader can tell it is derived.

## Detect it by timestamp

- Config echoed back freezes at the **install timestamp**; live state carries a **recent** timestamp. Both arrive via `sendEvent` and render identically.
- Before trusting a cloud attribute, check whether its `currentStates[<attr>].date` has moved since install (`skills/_reference/endpoints.md`). A round value, identical across units, unchanged since install is a default, not a measurement.

## Rank suspicion by observability

- The error rate tracks how hard the underlying fact is to **observe**, not whether a human typed it.
- Look-and-see fields (nozzle type, soil type) stay reliable even when hand-entered. Instrument-required fields (slope, area) do not, and their errors are **directional**, not random.
- The person who entered the data is itself a source. Ask which fields they measured, which they eyeballed, and which they left at the default. That ranks the whole set in one question.

## Never impeach one guess with another

- Using one unverified field to argue a second is wrong produces a confident conclusion from two guesses.
- Verify a suspect config field against an independent measurement, never against a neighboring self-reported field.

## Map the model empirically

- The integration publishes both the inputs and the outputs. The input→output wiring is measurable from the hub without vendor docs.
- Change exactly one field in the vendor's own app.
- `POST /device/runmethod {"method":"refresh"}` to force the integration's read immediately (`skills/_reference/endpoints.md`).
- Diff `fullJson.currentStates` before and after to see which outputs that field drove.
- Establish which inputs matter **before** correcting any. Field names do not state the wiring.
- Two units with matching inputs producing matching outputs confirms the model is deterministic and the full input set is identified; a mismatch means a hidden input.

## Publishing gaps

- A cloud integration publishes the subset its author chose, not the subset that matters.
- Enumerate what it actually publishes against the vendor's own app before designing around it.
- The absent fields may be the consequential ones.
