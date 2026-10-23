# patton-gate

A plugin for Claude Code and Codex CLI that asks before the agent changes, moves or deletes a file that existed
when the session started. When nothing like that is at stake it stays silent, so your own permission settings keep
working. In Claude Code it can also hold subagents to limits you set ([Subagent limits](#subagent-limits)).

![Ubuntu 22.04, 24.04 and 26.04: VM validated](https://img.shields.io/badge/Ubuntu%2022.04%20%7C%2024.04%20%7C%2026.04-VM%20validated-2f855a?style=flat-square&logo=ubuntu&logoColor=white)

## Install

```sh
claude plugin install patton-gate --marketplace vladimirrott/patton-protocol
```

The gate starts with your next session. To check the install, run `claude plugin list`: it shows
`patton-gate@patton-protocol` with `Status: ✔ enabled`.

Needs **Claude Code 2.1.295 or newer**, the first release whose hooks can fail closed (`onFailure: "block"`), and
**`python3` 3.10 or newer** on `PATH`. Nothing else: the gate is one Python file that uses the standard library
only. Version 0.2.0 is checked on Claude Code 2.1.309 and Codex CLI 0.159.0, installed from GitHub in fresh Ubuntu
22.04, 24.04 and 26.04 VMs; 0.1.1 was also checked on 2.1.307 and 2.1.308. macOS and Windows are untested.

For Codex, see [On Codex](#on-codex).

What the install does:

1. Adds this repository as a plugin marketplace named `patton-protocol` in your user settings.
2. Copies the plugin into `~/.claude/plugins/cache/`, and enables it for your user. `--scope project` enables it
   for one repository instead.
3. Registers its hooks: one when a session starts, one before and one after every `Bash`, `Write`, `Edit`,
   `MultiEdit`, `NotebookEdit` and subagent dispatch (`Agent`), and three that only count subagents
   (`SubagentStart`, `SubagentStop`, and a failed dispatch). Those three never block anything.

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
  for the rest of the session. Expect one question each for `git push`, `git pull`, `git checkout`, `git switch`,
  `npm`, `npx`, `cargo`, `make`, `pytest` and `uv`; `git status`, `git diff`, `git log`, `git add`, `git commit`,
  `ls`, `cat` and `grep` run without one.
- **Anything else** gets no answer from the gate, and Claude Code proceeds as your settings say.
- **A file you approved once** is not asked about again in that session.
- **Before a change it lets through or asks about**, it copies each protected file it can name, as it was at session
  start, so you can [undo a wrong approval](#undo-a-wrong-approval).
- **The gate's own files** (its settings, its session state, this plugin's directory, and the Claude Code settings
  files that can switch it off) are always asked about, even when they did not exist at session start.
- **If the gate itself fails**, the call is blocked with the reason. A gate that crashed has checked nothing, so it
  does not let the call through.

### By permission mode

Observed by `tests/tools/gate_plugin_e2e.py` (headless) and `tests/tools/gate_plugin_tui.py` (interactive) on
Claude Code 2.1.307 to 2.1.309:

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

After five refusals in one session, the gate refuses every call that changes a file, new files included, and tells
the agent to report itself blocked: an agent that keeps running into the gate has lost the thread. Only refusals
count (in `bypassPermissions`, or with `PATTON_GATE_OPAQUE=deny`); questions you answer never do. Start a new
session to reset the count.

### What it does not protect

- Files inside `node_modules`, `target`, `.venv`, `venv`, `__pycache__`, `.tox`, `.mypy_cache`, `.pytest_cache`,
  `.ruff_cache`, `.gradle` and `.next`: tools rebuild them. `.git` is protected.
- Anything outside the project directory, apart from the gate's own files.
- An agent that sets out to get around it. The gate reads commands the way an honest agent writes them. An agent
  that writes a script and then runs it, or hides a path in a variable, reaches the gate as a command it cannot
  read, and then you decide; with `files.unreadable_commands` set to `allow` or a matching `trusted_commands`
  entry, nothing asks you. For a boundary against a hostile agent, run Claude Code with its sandbox
  ([`sandbox.enabled`](https://code.claude.com/docs/en/sandboxing)) or Codex without
  `--dangerously-bypass-approvals-and-sandbox`, and keep the gate on inside it.

## See what it did

```sh
python3 ~/.claude/plugins/cache/patton-protocol/patton-gate/*/scripts/patton_gate.py log
```

It prints each call the gate saw in your latest session, with what it did. Every session keeps its own ledger
under `~/.claude/plugins/data/patton-gate-patton-protocol/sessions/`; `log <session id>` shows an earlier one. With
`--plugin-dir` the ledgers go to `~/.claude/plugins/data/patton-gate-inline/`, and `log` finds those too.

## Settings

With no settings file the gate behaves as the study measured, with the subagent limits off. Settings are JSON, in
two places:

- **Your user file**, `${XDG_CONFIG_HOME:-~/.config}/patton-gate/config.json`. It may also hold a `projects` map from
  an absolute project path to overrides for that project. `PATTON_GATE_CONFIG=<file>` reads another file instead.
- **A project file**, `.claude/patton-gate.json` in the project (`.codex/patton-gate.json` on Codex). It can only
  make the gate stricter: lower a limit, turn a check on, change `ask` to `deny`, protect more files, keep more
  copies. Anyone who commits to the repository, the agent included, can write it, so a field that would loosen the
  gate is ignored, and `config` says which. Loosen a project in your user file's `projects` map.

A session reads its settings once, when it starts. An edit takes effect in the next session. If a file has an
error, the gate refuses every call and names the file and the field, rather than run on defaults.

```sh
python3 ~/.claude/plugins/cache/patton-protocol/patton-gate/*/scripts/patton_gate.py config            # what a new session here gets
python3 ~/.claude/plugins/cache/patton-protocol/patton-gate/*/scripts/patton_gate.py config --check FILE
```

| Field | Default | Meaning |
| --- | --- | --- |
| `files.protect` | `true` | `false` leaves file changes to your own settings; the gate's own files stay guarded |
| `files.on_change` | `"ask"` | `"deny"` refuses a change to a protected file without asking |
| `files.unreadable_commands` | `"ask"` | `"deny"` refuses a command the gate cannot read; `"allow"` leaves it to your settings |
| `files.trusted_commands` | `[]` | command prefixes that run without a question, such as `"pytest"` or `"make test"`; only for one plain command, never for `pytest; rm x`, a redirect or a `$(...)` |
| `files.ignore` | `[]` | glob patterns that are not protected; a pattern without `/` matches at any depth, `**` crosses directories |
| `files.always_protect` | `[]` | glob patterns that are protected even when created during the session |
| `files.skip_dirs` | the list below | directories the session's file list skips |
| `files.max_refusals` | `5` | refusals in one session before the gate tells the agent to stop and report |
| `files.keep_originals_mb` | `200` | megabytes of original copies kept per session; `0` keeps none |
| `missions.*` | off | see [Subagent limits](#subagent-limits) |

`PATTON_GATE_OPAQUE=deny`, exported before you start `claude`, still refuses commands the gate cannot read, as in
the study. `claude plugin disable patton-gate@patton-protocol` turns the gate off without uninstalling it, and
`claude plugin enable patton-gate@patton-protocol` turns it back on; both take effect in the next session.

## Subagent limits

A mission is one subagent. Each limit is off unless you set it; `"preset": "patton"` sets the protocol's numbers,
and a field beside the preset overrides it. `null` turns one limit off.

```json
{"missions": {"preset": "patton", "max_commands": 40}}
```

| Field | `patton` | The gate refuses |
| --- | --- | --- |
| `missions.max_workers` | 4 | a dispatch while that many subagents run or are starting (a dispatch that has not started a subagent within 60 s stops counting) |
| `missions.max_dispatches` | 12 | a dispatch past that many in one session, however the mission is worded (4 workers, each dispatched once and retried twice) |
| `missions.max_commands` | 20 | the subagent's shell commands and file edits past the budget |
| `missions.max_output_tokens` | off | the subagent's next command once its transcript holds that many output tokens |
| `missions.timeout_minutes` | 15 | the subagent's next command after that many minutes |
| `missions.max_retries` | 2 | a dispatch of a mission with the same description and type, past 1 plus retries |
| `missions.exclusive_paths` | `true` | a change to a file another running subagent changed; the file opens when that subagent stops |

Every refusal names its setting and tells the agent what to do: report, wait, or do the work itself. The main
session has no budget and never claims a file. The gate only runs on shell commands, file edits and dispatches, so a
subagent that only reads is not stopped at its timeout, and `exclusive_paths` covers the files a command names. Two
subagents that reach for the same file at once are decided under one lock: one gets it, the other is refused. A file
opens again when its owner stops, so a later subagent can still overwrite the earlier one's change without having
read it. On
Codex the limits are not built.

These limits pass `tests/tools/gate_missions_e2e.py`, a scripted run on real Claude Code for $0, and ran in a 16-run
pilot on gpt-oss-120b ([record](../../docs/records/2026-10-23-missions-pilot.md)). No study has measured whether
they help.

## Undo a wrong approval

```sh
python3 ~/.claude/plugins/cache/patton-protocol/patton-gate/*/scripts/patton_gate.py restore             # list
python3 ~/.claude/plugins/cache/patton-protocol/patton-gate/*/scripts/patton_gate.py restore --all       # put all back
python3 ~/.claude/plugins/cache/patton-protocol/patton-gate/*/scripts/patton_gate.py restore notes.txt   # one file
```

The first time a call the gate lets through or asks about would change a protected file, the gate copies that file
into the session's state. `restore` lists the copies of the latest session (`--session ID` for another) and whether
each file is now unchanged, changed or missing. Putting a file back moves the current one aside to
`<name>.before-restore` first, so nothing newer is lost. A question says when the gate holds a copy of every file it
names. It keeps none past `files.keep_originals_mb`, and none for a command it cannot read, because it cannot tell
which files that command touches.

## On Codex

Codex CLI 0.159 or newer, and `python3` 3.10 or newer:

```sh
codex plugin marketplace add vladimirrott/patton-protocol && codex plugin add patton-gate@patton-protocol && python3 "${CODEX_HOME:-$HOME/.codex}"/plugins/cache/patton-protocol/patton-gate/*/scripts/patton_gate.py codex-trust
```

Codex runs a plugin's hooks only after you trust them. The last command does what Codex's `/hooks` review does: it
asks `codex app-server` for this plugin's hooks, records each one's current hash as trusted in `hooks.state`, and
checks that Codex now reports all three as trusted. Codex drops trust when a hook changes, so run it again after
`codex plugin marketplace upgrade`. You can review and trust them in `/hooks` yourself instead.

Codex hooks cannot ask a question, and Codex lets a call run when its hook fails, so the gate works differently
there:

- A shell command or patch that would change a protected file, or a command the gate cannot read, is held with a
  short id. The agent is told to tell you what it changes; you approve by typing `approve <id>` in the chat, and
  the agent makes the same call again. Only a prompt you type can approve, and the gate guards its own state.
- Every allowed command is rewritten to `cd <session directory> && <command>`, because Codex does not tell a hook
  the directory a command runs in.
- Patches are read for every file they add, update, delete or move.
- Any error inside the gate refuses the call with the reason.

Logs, settings and `restore` work as in Claude Code; add `--codex` to `log` and `restore`, and run the script from
`${CODEX_HOME:-~/.codex}/plugins/cache/patton-protocol/patton-gate/*/scripts/`. To remove it:
`codex plugin remove patton-gate@patton-protocol`. `tests/tools/gate_codex_e2e.py` installs, trusts and drives it
in real Codex with a scripted model, for $0.

## Uninstall

```sh
claude plugin uninstall patton-gate@patton-protocol --keep-data
claude plugin marketplace remove patton-protocol
```

The first line removes the plugin and its hooks. `--keep-data` keeps the ledgers of past sessions in
`~/.claude/plugins/data/patton-gate-patton-protocol/`; without it, Claude Code deletes that directory too. The
second line removes the marketplace entry from your user settings. The plugin writes nothing else. If you
installed with `--scope project`, uninstall with `--scope project` too.

## Troubleshooting

- **"python3 3.10 or newer is not on PATH"**: install Python 3.10 or newer, or disable the plugin. The gate blocks
  calls rather than let them run unchecked.
- **"the manifest is missing or unreadable in …"**: the session started before the plugin was enabled, or the gate
  could not write to the directory the message names. Start a new session; if the message comes back, make that
  directory writable.
- **"the manifest belongs to a different work directory"**: the session moved to another project. Start a new
  session there.
- **"this mission has been refused too many times"**: five refusals in this session; see
  [By permission mode](#by-permission-mode). Start a new session.
- **"the gate's configuration has a problem: …"**: fix the file and field it names (`config --check FILE`), then
  start a new session.
- **"… (missions.max_commands in the gate's settings)"** and the other mission refusals: a subagent limit you set.
  Raise it in your user file, or set it to `null`.
- **On Codex, nothing is ever held**: the hooks are not trusted. Run `codex-trust` again, or trust them in `/hooks`.
- **"the gate failed (…)"**: a bug in the gate. It refuses the call rather than let it run unchecked. Disable the
  plugin to keep working, and open an issue with the error text and the output of `log`.
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
changes are new behaviour that the study did not measure, and so are the settings, the subagent limits, the kept
copies and the Codex support added in 0.2.0.

Each Ubuntu LTS install is checked in a fresh VM by `tests/vm/plugin-vm.sh`, installing from GitHub as a user
would; the transcripts are in [`docs/records/gate-plugin-vm/`](../../docs/records/gate-plugin-vm/), with `host.log`
for the development machine.
