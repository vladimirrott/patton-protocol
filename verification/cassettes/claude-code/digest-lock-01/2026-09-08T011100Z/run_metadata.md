# Run metadata

- mission: `missions/digest-lock-01.md`
- harness: Claude Code 2.1.263 (real subagent for the honest baseline;
  the adversarial and staleness steps are direct mechanism tests run by
  the dispatching session itself, not by a dispatched worker — see
  `run_metadata` note below on why)
- date: 2026-09-08 UTC
- git repo: yes — this trial specifically closes the "no-git-repo host"
  gap's *complement* (a real git repo, exercising the actual algorithm)
  though it does not test a no-git host itself (see `verification/log.md`)

## Why steps 3–4 are mechanism tests, not dispatched-agent trials

Every real worker across this entire verification matrix has been
scrupulously honest about scope (see `builder-baseline-01` and
`approval-boundary-01` cassettes). That's good news about model behavior,
but it means a real adversarial report can't be manufactured by asking a
model to lie convincingly — and one honest model's good behavior
wouldn't prove the *mechanism* catches a bad report anyway, only that
this particular run didn't need to. So steps 3 and 4 test the mechanism
directly: given a worst-case constructed claim (or a worst-case timing of
a second mutation), does the independent, mechanical computation — which
depends on git state, not on trusting anyone's prose — correctly expose
the problem? That is a stronger and more decisive test of the
mechanism's soundness than hoping to catch a model misbehaving.

## `patton_digest.py`

Included in this cassette as the exact code that was run (`sha256`-covered
by the manifest like every other artifact here). It's a from-scratch
implementation of safety-and-budgets.md's digest algorithm, self-tested
against the spec's own worked example (the empty-snapshot digest) before
being trusted for this trial. A copy also lives at
`verification/patton_digest.py` for reuse in future trials.
