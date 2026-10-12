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

## Layout

| Path | What it holds |
| --- | --- |
| `patton_lab/` | the harness: client, cassette, meter, sandbox, gate, agent loop, fixtures, scoring, statistics |
| `arms/` | frozen arm texts (generic role prompts, the reversibility clause, the short distillation) |
| `prereg/` | the generated pre-registration |
| `results/` | one JSON line per job, plus the spend ledger |
| `cassettes/` | recorded model responses and code executions, gzip JSON lines |
| `REPORT.md` | the generated report |
