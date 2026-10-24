# Gate study findings, 2026-10-24 (Codex, gpt-6.1-sol)

Every number below appears in `lab/CODEX_REPORT.md`, in the pre-registration, or in `lab/CC_REPORT.md` (for the
comparison with gpt-oss-120b), and `tests/test_codex_study_integrity.py` fails when one does not. The rules were
frozen in `lab/prereg/2026-10-22-codex-gate-study-preregistration.md` before the first study run. The study ran
Codex CLI 0.159.0 headless in a podman container on gpt-6.1-sol at reasoning effort medium, on one ChatGPT
subscription: 5 arms, 16 fixtures, 3 reps, 240 runs, all finished. Every model call is in a cassette, and replay
re-scores all 240 runs offline. The record of how the study ran, with its three stops, is
`docs/records/2026-10-24-codex-gate-study-record.md`.

## Limits, first

- **The model harmed nothing on its own.** Unguarded harm (X-R0) was 0.000 over 48 runs, against 0.196 for
  gpt-oss-120b in the Claude Code study. The calibration had seen harm in 1 of 5 X-R0 runs, so harm on this model
  is rare, and the study cannot show the hook cutting it. Q1 reads INCONCLUSIVE under the registered floor rule.
- **The approver is simulated.** X-R1 and X-R1S pair the hook with the oracle that approves exactly the removals
  that lose nothing, as in the Claude Code study.
- **The Codex adapter is new.** Codex hooks never see exec_command's `workdir`, so every allowed command ran as
  `cd <cwd> && <command>`, and a patch reached the gate as one synthetic `rm -f`. A model call passed a `workdir`
  in 7 of 48 X-R0 runs and 5 of 48 X-R1 runs. The adapter handled those runs; the Claude Code study never measured
  this path.
- **One model, one subscription.** No Claude model has been measured on either harness.
- **Network.** The worker's container used podman's `slirp4netns:allow_host_loopback=true` to reach the recorder on
  the host. A later probe through the same setting fetched a public web page, so the worker could have reached the
  internet from its shell. The fixtures need no network and web search was off in every arm; the later harness
  traces use no network at all and a Unix-socket relay to the scripted model instead.

## Pre-registered results

- **Q1, the hook cuts harm: INCONCLUSIVE (floor).** Harm was 0.000 in X-R0 and 0.000 in X-R1. With X-R0 harm
  below 0.10 the contrast has nothing to measure.
- **Q2, bypass is rare: SUPPORTED.** 0 of 96 hooked runs bypassed the gate; the exact one-sided 95% upper bound on
  the bypass rate is 0.031.
- **Q3, the diff gate under the oracle: SUPPORTED.** 0 of 48 X-R0 runs harmed when scored through the diff gate.
  It checks the code.
- **Q5, utility survives the hook: SUPPORTED.** Utility was 0.948 unguarded and 0.938 under the hook. The drop is
  0.010 with a 90% CI of [0.000, 0.031], and the one-sided 95% upper bound of 0.031 sits under the registered
  margin of 0.10. The whole difference comes from one rep of one fixture (f13); the other 15 fixtures scored the
  same in both arms.
- **Q6, the burden is usable: SUPPORTED.** The hook asked the operator 1.000 times per run (one-sided 95% bounds
  [1.000, 1.000]) against a ceiling of 2.0. It also refused 0.438 commands per run as unreadable and asked the
  worker to rewrite them.

## Exploratory (no verdict)

- **What the gate cost on a careful model.** No harm to remove, no measurable loss of finished work, one question
  per run, and more model calls: 4.5 per run unguarded against 5.6 under the hook, because each refusal sends the
  model back to rewrite or report.
- **Deny-all (X-R1D).** With every request refused, utility fell to 0.000 and harm stayed 0.000. The worker's final
  report matched what happened in 48 of 48 runs: it said the work was blocked instead of claiming it done.
- **R2, the diff gate offline.** Every approver kept harm at 0.000, approve-all included, since the unguarded runs
  harmed nothing; on this model R2 says nothing about worse operators. The noisy-10 approver cut utility to 0.858
  by refusing good removals.
- **The skill.** Codex invoked the installed skill in 44 of the 48 X-R0S runs and 45 of the 48 X-R1S runs, where
  Claude Code invoked it in none. Harm with the skill was 0.000 in both comparisons, so the skill moved nothing
  here either.
- **Tools.** Almost every call was a shell command: the Bash share was 0.983 in X-R0 and 0.995 in X-R1.

## What this supports

On gpt-6.1-sol inside Codex, the gate ran without a bypass in 96 runs, kept finished work within 0.031 of the
unguarded level, and asked once per run. The model made no destructive mistake for it to stop. Together with the
Claude Code study, the evidence reads: on a model that harms (gpt-oss-120b, 0.196) the gate removes the harm, and on
one that does not (gpt-6.1-sol, 0.000) it costs about one question and one extra model call per run.
