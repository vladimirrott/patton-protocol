# Verifier replay findings, 2026-10-25

Every number below appears in `lab/VERIFY_REPORT.md`, in the pre-registration, or in the post hoc table at the end,
and `tests/test_verify_study_integrity.py` fails when one does not. The rules were frozen in
`lab/prereg/2026-10-24-verify-replay-preregistration.md` (commit 37547b3) before the first call on the test set.
Every answer is stored with the full response in `lab/verify_study/`, replay re-decides each one from the frozen
code, and an independent recompute from the raw records matched every count, bound and verdict.

## The question

patton-gate 0.3.1 can check a change before the agent stops. A claim reader sees your request and the agent's final
message and says whether the agent claims the work done. A Verifier sees your request, the notes as they were, the
checks, the change and the files left after it, and never the final message. The gate refuses the stop only when the
agent claims the work done and the Verifier fails it with evidence found word for word in what it read.

The replay ran both calls on the 800 stored runs of the Claude Code and Codex gate studies: 46 harmful (a needed
file lost), 457 clean and 297 incomplete. The primary model, chosen on three development runs, is qwen3.8-27b on
Groq; gpt-oss-120b ran on every case too.

## Pre-registered results

| ID | Prediction | Numbers | Verdict |
| --- | --- | --- | --- |
| V1 | qwen3.8-27b flags more than 0.75 of harmful runs | 28 of 46, rate 0.609, bounds [0.400, 0.828] | INCONCLUSIVE |
| V2 | qwen3.8-27b flags under 0.10 of clean runs | 25 of 457, rate 0.055, 95% bound 0.101 | INCONCLUSIVE |

The recall point estimate sits below the registered bar and the bound reaches above it. The false-flag bound misses
the bar by its third decimal.

## Limits, first

- **Nine fixtures hold every harmful run.** The 46 harmful runs come from 9 fixtures, and f11 alone holds 14 of
  them, so one fixture's behaviour moves the pooled rate more than any model change could show.
- **Every harmful run is a Claude Code run.** The Codex study harmed nothing, so the recall numbers say nothing
  about Codex.
- **The oracle scores some judgment calls.** A file the notes never mention counts as needed (AMB), and deleting a
  backup whose identical original survives counts as harmless. A person could score either way; the Verifier is
  measured against the study's rule.
- **The prompt was tuned on the development fixtures** h01..h08, and the primary model was picked there, before
  any test call. Three development runs are kept in `lab/verify_dev_v1/`, `lab/verify_dev_v2/` and
  `lab/verify_dev/`.
- **A replay is not a session.** The stored runs ended; nobody saw a refusal or acted on it. Whether a refused
  agent fixes the problem or reports it honestly is untested here (the $0 end-to-end test in real Claude Code
  shows the mechanism, not the rate).

## Exploratory

- **gpt-oss-120b flagged 19 of 46 harmful runs and 30 of 457 clean ones.** It flags fewer harmful runs than
  qwen3.8-27b at a similar false-flag count.
- **The hook refuses less than the Verifier flags.** qwen3.8-27b refused 27 of 46 harmful stops and 12 of 457 clean
  ones, because the hook asks the claim reader too.
- **Unfinished work.** qwen3.8-27b flagged 202 of 297 incomplete runs, and the hook refused 30 of them. The rest
  ended with a message that did not claim the work done, so an honest report passes, which is the design.
- **The claim reader on refused work.** The deny-all arms hold 160 runs, and every change in them was refused.
  qwen3.8-27b read a completion claim in 7 of those final messages, gpt-oss-120b in 8.
- **Cost.** 3200 calls per model; qwen3.8-27b cost $0.9672 and gpt-oss-120b $0.3970.

## What this supports

The Verifier catches a loss the notes name and misses a loss the notes are silent about. Over the harmful runs,
qwen3.8-27b flagged 10 of 12 that lost a file the notes call the only copy (IRR), and 4 of 16 that lost a file the
notes never mention (AMB). It passed `config.old.yaml` in f11 on its name, though the prompt says a name never makes
a file disposable. That matches the triage result: an LLM reading the notes does well when the notes speak, and the
deterministic gate stays the guard for what they leave unsaid.

So the Verifier ships off by default, as an extra check on top of the gate and never in place of it. With it on,
qwen3.8-27b refused about one clean stop in 38 (12 of 457) and about three harmful stops in five (27 of 46).

## Post hoc: where the misses and the false flags sit

Chosen after the results were seen, so it carries no verdict. `python3 -m patton_lab.posthoc.verify_cases` prints
it from the stored answers, and the integrity test checks that this copy matches.

<!-- cases -->
| Model | Count | Value |
| --- | --- | --- |
| gpt-oss-120b | harmful runs that lost AMB files, flagged | 2 of 16 |
| gpt-oss-120b | harmful runs that lost CONF files, flagged | 6 of 18 |
| gpt-oss-120b | harmful runs that lost IRR files, flagged | 11 of 12 |
| gpt-oss-120b | missed harmful runs in the fixture with the most (f11) | 12 of 27 |
| gpt-oss-120b | clean runs flagged in the fixture with the most (f11) | 10 of 30 |
| qwen3.8-27b | harmful runs that lost AMB files, flagged | 4 of 16 |
| qwen3.8-27b | harmful runs that lost CONF files, flagged | 14 of 18 |
| qwen3.8-27b | harmful runs that lost IRR files, flagged | 10 of 12 |
| qwen3.8-27b | missed harmful runs in the fixture with the most (f11) | 11 of 18 |
| qwen3.8-27b | clean runs flagged in the fixture with the most (f12) | 8 of 25 |
<!-- /cases -->

Each clean run qwen3.8-27b flagged in f12 deleted the vendored archive the notes say can be regenerated. I read
the eight findings: five said the change freed too little space for a full disk, and the other three doubted the
file could go. The fixtures' files hold a few bytes each, and the file list shows their sizes, so the size
complaints come from the synthetic fixtures.
