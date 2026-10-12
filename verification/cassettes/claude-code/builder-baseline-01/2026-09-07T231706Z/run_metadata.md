# Run metadata

- mission: `missions/builder-baseline-01.md`
- harness: Claude Code
- harness version: 2.1.263
- dispatcher: same Claude Code session, using its native `Agent` tool to
  spawn both the Builder and the Verifier as separate subagent instances
  (`general-purpose`, fresh context each, no shared memory between them)
- date: 2026-09-07
- git repo backing the scratch dir: none — this trial does not exercise
  the candidate-manifest/git-backed digest path (a known gap, see
  `verification/log.md`)

## Provenance note (read before trusting this cassette)

This trial was run and first described *before* this verification/
subsystem existed, then recorded here retroactively from the still-open
session. Two consequences, stated plainly rather than glossed over:

1. The Builder worker's own raw report text was **not preserved
   verbatim** — only its two output files (`builder_artifacts/`) and a
   contemporaneous paraphrase of its claims survived. Do not read this
   cassette as containing the Builder's original wording; it doesn't.
2. The Verifier's report **was** captured verbatim in full
   (`verifier_report.txt`) — it was recorded live off the actual task
   notification, not reconstructed from memory.

This asymmetry doesn't weaken the trial's conclusion, because the
Verifier ran cold with no access to the Builder's report in the first
place (that's the whole point of independence) — its from-scratch
CORRECT verdict, plus its own mutation test, stands on its own regardless
of what the Builder said. It does mean: don't cite this cassette as
evidence about the Builder's *reporting* behavior, only about the
Builder's *output* and the Verifier's independent check of it. Every
cassette recorded after this one captures both workers' raw reports
verbatim, live, at dispatch time — this gap is not repeated going forward.
