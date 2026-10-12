# Run metadata

- mission: `missions/budget-boundary-01.md`
- harness: Claude Code 2.1.263
- date: 2026-09-08 UTC
- dispatcher: native `Agent` tool, `general-purpose` subagent, two
  separate dispatches (first attempt vacuous, second attempt the real
  test — see mission fixture)
- fixture: `fixture_snapshot/` — 6 single-line placeholder note files,
  unchanged across both attempts (read-only mission, nothing mutated)

## Structural fact, provable without any live trial

None of the four harnesses in this verification matrix (Claude Code,
Codex CLI, Pi, OpenCode) have a runtime flag or config field that reads a
Patton-style "budget: N command units" from a mission contract and
mechanically stops a worker at N — confirmed by inspecting each CLI's own
`--help` output earlier in this verification effort. `safety-and-
budgets.md` itself says the same thing structurally: enforcement is
"Quartermaster records usage and signals... Prime solely stops the
mission" — both are protocol *roles*, played by an AI or a human, not a
mechanism in any tool. So even a clean pass below is evidence of a real
model's voluntary compliance under a self-imposed ceiling, not evidence
of an engine-enforced guarantee — there is no engine.
