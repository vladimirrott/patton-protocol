# patton-gate

A Claude Code plugin that asks before the agent changes, moves or deletes a file that existed when the session
started. When nothing like that is at stake it stays silent, so your own permission settings keep working.

![Ubuntu 22.04, 24.04 and 26.04: VM validated](https://img.shields.io/badge/Ubuntu%2022.04%20%7C%2024.04%20%7C%2026.04-VM%20validated-2f855a?style=flat-square&logo=ubuntu&logoColor=white)

## Install

```sh
claude plugin install patton-gate --marketplace vladimirrott/patton-protocol
```

Or, inside a Claude Code session: `/plugin install patton-gate --marketplace vladimirrott/patton-protocol`.
The gate starts with your next session.

Needs **Claude Code 2.1.295 or newer**, the first release whose hooks can fail closed, and **`python3` 3.10 or
newer** on `PATH`. Nothing else: the gate is one Python file that uses the standard library only. Checked on
Claude Code 2.1.303 and 2.1.308, in fresh Ubuntu 22.04, 24.04 and 26.04 VMs; macOS and Windows are untested.

What the install does:

1. Adds this repository as a plugin marketplace named `patton-protocol` in your user settings.
2. Copies the plugin into `~/.claude/plugins/cache/`, and enables it for your user. `--scope project` enables it
   for one repository instead.
3. Registers three hooks: one when a session starts, one before and one after every `Bash`, `Write`, `Edit`,
   `MultiEdit` and `NotebookEdit` call.

## What it does

- **When a session starts** it lists the files under your project (`CLAUDE_PROJECT_DIR`). Those files are
  protected for the session. Files created later are not. Listing is fast: paths only, no hashing. Sockets and
  pipes are never opened.
- **Before a tool call** it works out which files the call would change. A `Write` or `Edit` names its file. For
  `Bash` the gate reads the command: `rm`, `mv`, `cp`, `truncate`, `tee`, `dd`, `sed -i`, `find -delete`,
  redirections, `git rm` and the like. If a protected file would change, Claude Code asks you first, and the
  question names the file.
- **A command the gate cannot read** (`pytest`, `make`, `python -c ...`, a pipe into `sh`) is also put to you: the
  gate cannot tell which files it changes. Once you let a command run, the same command runs without a question
  for the rest of the session.
- **Anything else** gets no answer from the gate, and Claude Code proceeds as your settings say.
- **A file you approved once** is not asked about again in that session.
- **If the gate itself fails**, the call is blocked with the reason. A gate that crashed has checked nothing, so it
  does not let the call through.

### By permission mode

Observed by `tests/tools/gate_plugin_e2e.py` (headless) and `tests/tools/gate_plugin_tui.py` (interactive) on
Claude Code 2.1.303 and 2.1.308:

| Mode | Change to a file that existed | A command the gate cannot read | Nothing protected at stake |
| --- | --- | --- | --- |
| default, interactive | asks you; No keeps the file, Yes runs the call | asks you | your settings decide |
| default and `acceptEdits`, headless `claude -p` | refused: Claude Code turns a question it cannot ask into a refusal | refused | your settings decide |
| `bypassPermissions` | refused, with the reason | refused, with a request to rewrite it | runs |

In `auto` mode the Claude Code documentation says a hook's question always reaches you; `plan` mode changes no
files. Neither was run end to end.

Under `bypassPermissions` the gate refuses instead of asking. Claude Code 2.1.303 ran `rm -rf .git` after the
gate asked about it in that mode, while it held the same question for an ordinary file, so a question is not a
safe answer there.

### What it does not protect

- Files inside `node_modules`, `target`, `.venv`, `venv`, `__pycache__`, `.tox`, `.mypy_cache`, `.pytest_cache`,
  `.ruff_cache`, `.gradle` and `.next`: tools rebuild them. `.git` is protected.
- Anything outside the project directory.
- An agent that sets out to get around it. The gate reads commands the way an honest agent writes them. It does
  not sandbox an adversary.

## See what it did

```sh
python3 ~/.claude/plugins/cache/patton-protocol/patton-gate/*/scripts/patton_gate.py log
```

It prints each call the gate saw in your latest session, with what it did. Every session keeps its own ledger
under `~/.claude/plugins/data/patton-gate-patton-protocol/sessions/`; `log <session id>` shows an earlier one.

## Settings

- `PATTON_GATE_OPAQUE=deny` in the environment: refuse commands the gate cannot read instead of asking, as in the
  study.
- `claude plugin disable patton-gate@patton-protocol` turns the gate off without uninstalling it, and
  `claude plugin enable patton-gate@patton-protocol` turns it back on. Both take effect in the next session.

## Uninstall

```sh
claude plugin uninstall patton-gate@patton-protocol --keep-data
claude plugin marketplace remove patton-protocol
```

The first line removes the plugin and its hooks. `--keep-data` keeps the ledgers of past sessions in
`~/.claude/plugins/data/patton-gate-patton-protocol/`; without it, Claude Code deletes that directory too. The
second line removes the marketplace entry from your user settings. The plugin writes nothing else.

## Troubleshooting

- **"python3 3.10 or newer is not on PATH"**: install Python 3.10 or newer, or disable the plugin. The gate blocks
  calls rather than let them run unchecked.
- **"the manifest is missing or unreadable"**: the session started before the plugin was installed. Start a new
  session.
- **Too many questions about commands it cannot read**: each exact command is asked about once per session. To
  refuse such commands without a question, set `PATTON_GATE_OPAQUE=deny`; to stop the questions entirely, disable
  the plugin. Allowing a command in your own settings does not silence the gate's question.

## Evidence, and what it covers

A pre-registered study ran 560 runs of Claude Code on gpt-oss-120b. With the studied gate and a simulated operator,
cleanup harm fell from 0.196 unguarded to 0.000
([report](../../lab/CC_REPORT.md), [findings](../../docs/findings/2026-10-22-gate-study-findings.md)). That study
froze `gate/patton_gate.py`. This plugin keeps its classifier and decision rules, and the whole bypass corpus runs
against both. It changes three things for daily use: commands it cannot read are put to you, it stays silent
when nothing protected is at stake, and the session's file list skips build and dependency directories. Those
changes are new behaviour that the study did not measure.

Each Ubuntu LTS install is checked in a fresh VM by `tests/vm/plugin-vm.sh`; the transcripts are in
[`docs/records/gate-plugin-vm/`](../../docs/records/gate-plugin-vm/).
