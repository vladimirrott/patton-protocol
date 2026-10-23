# Design: the Patton gate as a Claude Code plugin

Date 2026-10-22. Status: shipped as 0.1.1; 0.2.0 adds settings, subagent limits, kept copies and Codex ([design](2026-10-23-gate-config-and-missions-design.md)). The study ([findings](../findings/2026-10-22-gate-study-findings.md)) measured
`gate/patton_gate.py` on cleanup missions in a headless harness. This design turns that gate into something a
person installs with one command and keeps on in daily work.

## What the user gets

```sh
claude plugin install patton-gate --marketplace vladimirrott/patton-protocol
```

From the next session on, Claude Code asks before any tool call that would change, move or delete a file that
existed when the session started. Everything else runs as it did before: the gate adds a question where a protected
file is at stake and stays silent otherwise, so the user's own permission settings keep working. Uninstalling takes
two commands (the plugin, then the marketplace); `--keep-data` keeps the per-session ledgers.

Requirements: Claude Code 2.1.295 or newer (checked on 2.1.307 to 2.1.309) and `python3` 3.10 or newer on `PATH`.

## Why the plugin ships a product gate and not the studied file

The studied file is frozen: the pre-registration and replay hash it, so it cannot change. Three of its behaviours
fit a study and fail a person at work:

| Studied behaviour | Problem in daily use | Product behaviour |
| --- | --- | --- |
| Refuses every command it cannot read (`pytest`, `make`, `npm test`) | The worker cannot build or test | Asks the person; a refusal setting keeps the study behaviour |
| Hashes every file under the project at session start | 681 s on a Rust repository with `target/`; crashes on a socket, hangs on a FIFO | Lists paths without hashing, never opens a special file, skips dependency and build directories |
| Answers `allow` when nothing protected changes | Overrides the user's own permission prompts | Gives no decision, so Claude Code's normal flow applies |

The product gate keeps the classifier and the decision rules. A test runs the whole bypass corpus against both
files, so a classifier change in one without the other fails CI.

## Layout

- `.claude-plugin/marketplace.json` at the repository root lists one plugin, `patton-gate`, sourced from
  `plugins/patton-gate`.
- `plugins/patton-gate/.claude-plugin/plugin.json`, `plugins/patton-gate/hooks/hooks.json`,
  `plugins/patton-gate/scripts/patton_gate.py`, `plugins/patton-gate/README.md`.
- `gate/patton_gate.py` stays as the studied file, with a pointer to the plugin.

## Behaviour details

- **Root and state.** The protected root is `CLAUDE_PROJECT_DIR`, or the hook's `cwd`. State and the ledger live in
  `${CLAUDE_PLUGIN_DATA}/sessions/<session id>/`, which Claude Code sets for an installed plugin
  (`~/.claude/plugins/data/patton-gate-patton-protocol/`), with `${XDG_STATE_HOME:-~/.local/state}/patton-gate/` as
  the fallback for `--plugin-dir`. A resumed or compacted session keeps its
  first manifest, so "existed when the session started" keeps its meaning.
- **Skipped directories.** `node_modules`, `target`, `.venv`, `venv`, `__pycache__`, `.tox`, `.mypy_cache`,
  `.pytest_cache`, `.ruff_cache`, `.gradle` and `.next`: their contents are rebuilt by tools. `.git` is listed, so
  deleting the repository's history asks first.
- **Fail closed.** A crash in the gate denies the call with a reason. When `python3` is missing, the hook command
  exits 2, which blocks the call and tells the person why.
- **Settings.** `PATTON_GATE_OPAQUE=deny` gives the studied behaviour; any other value, or none, asks. Study mode
  (`PATTON_GATE_APPROVER` set) behaves as the study measured.

## Verification

1. Unit and corpus tests for the product gate, including the fail-closed paths and a manifest timing test on a large
   tree with a socket and a FIFO in it.
2. A container run of Claude Code 2.1.303 with `--plugin-dir` against the scripted model, as the study's trace did.
3. A clean QEMU VM per Ubuntu LTS (22.04, 24.04, 26.04): install Claude Code with the official installer, add the
   marketplace, install the plugin, run a session against the scripted model on the host, and check that the gate
   asked or stayed silent where it should. The record of each run is committed.

## Not covered by the study

The study measured the frozen gate. The product changes above are new behaviour: the evidence for "harm fell to 0"
applies to the studied rules, and the README says so.
