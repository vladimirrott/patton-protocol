# Patton Lab

Controlled, replayable experiments on whether the Patton Protocol changes what a
coding agent does. The design and its pre-registered hypotheses live in
[`docs/plans/2026-10-12-patton-lab-design.md`](../docs/plans/2026-10-12-patton-lab-design.md);
the frozen thresholds and hashes in [`prereg/`](prereg/).

Standard library only. The skill package does not depend on anything here, so
installing Patton Protocol means copying `SKILL.md`, `references/`, `adapters/`
and `agents/`, not this directory.

## Replay the study for $0

```
cd lab
python3 -m patton_lab.run replay
python3 -m patton_lab.run report
```

`replay` re-runs every recorded job from `cassettes/` with no network and no
key, re-scores it, and exits non-zero if any score differs or any recorded call
is missing. `report` rebuilds `REPORT.md` from `results/`.

## Run it

```
python3 -m patton_lab.run controls     # scripted agents must land on their known scores
python3 -m patton_lab.run dry-run      # expected and worst-case cost of what is pending
./launch.sh smoke --provider groq      # one real job, to check the key and the tool loop
./launch.sh record --exp E1            # one experiment at a time, resumable
```

`launch.sh` reads keys from files into the environment (never argv) and, for
Groq, disconnects Mullvad for the run and reconnects it on exit. The spend
meter in `results/spend.json` refuses any call that could carry actual spend
past $9, across every run and provider.

Code that a model writes (E2 snippets, E4 solutions) runs only in a podman
container with no network and a disposable copy of the sandbox. Without podman
and the pinned image, those experiments refuse to start.

## The gate study

A second, separate study measures the deletion gate in [`gate/`](../gate/) on headless Claude Code. Its design is
[`docs/plans/2026-10-19-real-harness-gate-design.md`](../docs/plans/2026-10-19-real-harness-gate-design.md). It ran on
gpt-oss-120b; the Sonnet and Haiku arms are not run.

| What | Where |
| --- | --- |
| pre-registration (frozen before the first run) | `prereg/2026-10-21-gate-study-preregistration.md` |
| one JSON record per run | `cc_runs/` |
| every model call, as cassettes | `cc_runs/cassettes/` |
| spend ledger | `cc_spend.jsonl` |
| generated report | `CC_REPORT.md` |
| findings | [`docs/findings/2026-10-22-gate-study-findings.md`](../docs/findings/2026-10-22-gate-study-findings.md) |
| gate pilot passes | `cc_pilot/`, `cc_pilot2/`, `cc_pilot3/`, `cc_pilot4/` |
| smoke runs | `cc_smoke/`, `cc_smoke2/` |
| LiteLLM config and proxy launcher | `cc/` |
| harness code | `patton_lab/cc/` |

```
cd lab
python3 -m patton_lab.cc.runner replay        # re-score every record offline and reconcile cc_spend.jsonl
python3 -m patton_lab.cc.report               # rebuild CC_REPORT.md from cc_runs/
python3 -m patton_lab.cc.report --check       # fail if the committed report differs from the rebuilt one
python3 -m patton_lab.cc.prereg --check       # fail if the pre-registration no longer matches the code
python3 -m patton_lab.cc.runner trace --arms O-R1 --fixtures h05   # free end-to-end trace
```

`replay` needs no network and no key. `trace` needs podman and the pinned image but no key: it runs the real
container, Claude Code, the gate and the approver against a scripted model. The numbers are in `CC_REPORT.md`
and the findings document, not here.

## Post hoc analyses

`patton_lab/posthoc/` holds analyses chosen after a study's results were seen; they carry no verdict.
`python3 -m patton_lab.posthoc.utility` splits the gate study's utility drop by what the gate did in each run, and
[`docs/findings/2026-10-23-gate-utility-posthoc.md`](../docs/findings/2026-10-23-gate-utility-posthoc.md) quotes it
(`--check` fails when the note quotes anything else).

## The gate study on Codex

The same fixtures, mission, operator and scoring, run inside Codex CLI 0.159.0 on the models a ChatGPT subscription
serves. Its design is [`docs/plans/2026-10-22-codex-gate-port-design.md`](../docs/plans/2026-10-22-codex-gate-port-design.md).
A 14-run calibration measured what one run costs
([record](../docs/records/2026-10-22-codex-calibration.md)). The study is pre-registered (gpt-6.1-sol, 5 arms,
16 fixtures, 3 reps, 240 runs) and has not run yet.

| What | Where |
| --- | --- |
| pre-registration (frozen, not yet run) | `prereg/2026-10-22-codex-gate-study-preregistration.md` |
| calibration records and cassettes | `codex_runs/` (run order in `codex_runs/ORDER`) |
| study records, once it runs | `codex_study/` |
| generated report, once it runs | `CODEX_REPORT.md` |
| the model catalog entry every run installs | `codex/catalog/` |
| harness code | `patton_lab/codex/` |

```
cd lab
python3 -m patton_lab.codex.prereg --check                  # fail if the pre-registration no longer matches the code
python3 -m patton_lab.codex.runner trace --arms X-R0,X-R0S,X-R1,X-R1S,X-R1D --model gpt-6.1-sol   # free trace
python3 -m patton_lab.codex.runner replay --model gpt-6.1-sol   # re-score the 10 calibration runs offline
python3 -m patton_lab.codex.runner replay --model gpt-6-luna    # and the other 4
python3 -m patton_lab.codex.runner summary                  # the calibration table
python3 -m patton_lab.codex.runner study                    # the registered study; spends subscription quota, resumable
python3 -m patton_lab.codex.report                          # after the study: write CODEX_REPORT.md from codex_study/
```

`trace` needs podman, the pinned image and the Codex binary, and no login: a scripted model stands in. `study`
refuses to start unless the pre-registration check passes, a trace passed on the same tree, the Codex binary
matches the pinned hashes, and the login has more than an hour left. It stops itself at 60 % of the weekly limit or
90 % of a 5-hour window, and the same command resumes it. A trace is tied to `adapter.py` and `runner.py`: after an
edit to either, `study` refuses until `trace` passes again.

## Layout

| Path | What it holds |
| --- | --- |
| `patton_lab/` | the harness: client, cassette, meter, sandbox, gate, agent loop, fixtures, scoring, statistics |
| `arms/` | frozen arm texts (generic role prompts, the reversibility clause, the short distillation) |
| `prereg/` | the generated pre-registration |
| `results/` | one JSON line per job, plus the spend ledger |
| `cassettes/` | recorded model responses and code executions, gzip JSON lines |
| `REPORT.md` | the generated report |
| `cc/` | LiteLLM config and proxy launcher for the gate study |
| `patton_lab/cc/` | the gate study harness: arms, approvers, recorder, runner, report, pre-registration generator |
| `cc_runs/` | the gate study's records, with `cassettes/` (and `attempts/` when an attempt fails; none did) |
| `cc_pilot*/`, `cc_smoke*/` | gate pilot passes and smoke runs, one directory each |
| `cc_spend.jsonl` | the gate study's spend ledger |
| `CC_REPORT.md` | the gate study's generated report |
| `patton_lab/codex/` | the Codex port: hook adapter, Responses recorder, runner, report, pre-registration generator |
| `codex/catalog/` | the model catalog entry the Codex runs install |
| `codex_runs/` | the Codex calibration's records and cassettes |
