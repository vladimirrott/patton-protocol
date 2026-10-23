# Design: a configurable gate that also enforces the mission limits, on Claude Code and Codex

Date 2026-10-23. Status: shipped in patton-gate 0.2.0, with two changes from this design: on Codex the mission limits are not built, and the gate keeps the session-start copy of each protected file before a change it lets through (`files.keep_originals_mb`, `restore`). The two studies point one way: the protocol's prose did not change what a
worker did ([lab findings](../findings/2026-10-13-lab-findings.md)), and the hook did
([gate study findings](../findings/2026-10-22-gate-study-findings.md)). The protocol's limits (worker count, mission
budget, timeout, retries, path overlap, approval for irreversible changes) therefore move out of `SKILL.md` and into
the hook, where the host enforces them, and every number becomes a setting.

`SKILL.md`, `references/` and `gate/patton_gate.py` stay as they are: three pre-registrations hash them.

## What stays the same

With no configuration file the gate behaves exactly as 0.1.1, which is what the study measured: it asks before a
file that existed at session start changes, asks about a command it cannot read, and stays silent otherwise. The
mission limits are off until the person turns them on. The study measured file protection only, so turning on
unmeasured limits by default would change the product the evidence describes.

## Configuration

JSON, read with the standard library. Three layers, later ones win:

1. built-in defaults;
2. the user file, `${XDG_CONFIG_HOME:-~/.config}/patton-gate/config.json`, which may also carry a `projects` map
   from an absolute project path to overrides for that project;
3. the project file, `<project>/.claude/patton-gate.json` (Claude Code) or `<project>/.codex/patton-gate.json`
   (Codex).

`PATTON_GATE_CONFIG=<file>` replaces the user file, for tests and for people who keep dotfiles elsewhere.

**A project file can only tighten.** A repository you clone decides its own project file, and so does the agent
working in it, so a project file may lower a limit, turn a check on or change `ask` to `deny`, and nothing else. A
loosening field in a project file is ignored, and `config` and `log` say so. Loosening a project goes in the user
file's `projects` map.

**The configuration is read once, when the session starts**, and a copy goes into the session's state directory. An
edit during the session, by the person or by the agent, takes effect in the next session. The gate's own files (both
configuration files and the state directory) are always protected: a change to them asks in Claude Code and is
refused in Codex, whether or not the file existed at session start.

**An unreadable or invalid configuration is never replaced by the defaults.** The session start records the error,
and every later call is refused with the file and the error named, until the person fixes it.

`python3 <plugin>/scripts/patton_gate.py config` prints the effective configuration, which layer set each value,
and every ignored field. `config --check FILE` validates a file without a session.

### Fields

```json
{
  "files": {
    "protect": true,
    "on_change": "ask",
    "unreadable_commands": "ask",
    "trusted_commands": [],
    "ignore": [],
    "always_protect": [],
    "skip_dirs": ["node_modules", "target", ".venv", "venv", "__pycache__", ".tox", ".mypy_cache",
                  ".pytest_cache", ".ruff_cache", ".gradle", ".next"],
    "max_refusals": 5,
    "keep_originals_mb": 200
  },
  "missions": {
    "preset": "off",
    "max_workers": null,
    "max_commands": null,
    "max_output_tokens": null,
    "timeout_minutes": null,
    "max_retries": null,
    "exclusive_paths": false
  }
}
```

- `files.on_change`: `ask` or `deny`, for a change to a protected file. Under `bypassPermissions` an `ask` becomes a
  refusal, as in 0.1.1.
- `files.unreadable_commands`: `ask`, `deny`, or `allow` (no decision, so the person's own settings apply).
- `files.trusted_commands`: command prefixes the gate does not ask about when it cannot read them, such as
  `"pytest"` or `"make test"`. A prefix matches whole words. The gate still reads every command it can, so
  `pytest; rm notes.txt` asks about `notes.txt`.
- `files.ignore` and `files.always_protect`: glob patterns relative to the project (`**` crosses directories).
  `ignore` removes files from protection. `always_protect` protects matching paths that were created later too.
- `files.max_refusals`: after this many refusals in a session, the gate tells the worker to stop and report.
- `missions.preset`: `off`, or `patton`, which sets the protocol's numbers: 4 workers, 20 commands, 15 minutes, 2
  retries, exclusive paths. Any field set beside the preset overrides it. `null` turns a limit off.

## Mission limits

A mission is one subagent. The hook sees a subagent's tool calls with its `agent_id`, sees it start and stop
(`SubagentStart`, `SubagentStop`), and sees the main agent dispatch it (`PreToolUse` on `Agent`). Each limit is
checked where the hook already runs, and a limit that is reached refuses the call with a reason that tells the
worker what to do.

| Limit | Counted as | Refused call |
| --- | --- | --- |
| `max_workers` | subagents started and not stopped, plus dispatches allowed in the last 60 s that have not started yet | the `Agent` call that would exceed it |
| `max_commands` | the subagent's Bash, Write, Edit, MultiEdit and NotebookEdit calls | the next such call; the reason says to report |
| `max_output_tokens` | output tokens in the subagent's transcript, each message counted once | the next command |
| `timeout_minutes` | wall clock since `SubagentStart` | the next command |
| `max_retries` | earlier dispatches with the same `description` and `subagent_type` in the session | the dispatch past the limit |
| `exclusive_paths` | a path a running subagent changed belongs to it until it stops | another agent's change to that path |

Limits the hook cannot enforce are named as such: a subagent that only reads (Read, Grep, Glob) is not stopped at
its timeout, because the gate does not run on those tools, and the gate cannot tell which files a command it cannot
read touches, so `exclusive_paths` covers the files a command names.

## Codex

The same plugin directory installs into Codex CLI. Codex reads `.codex-plugin/plugin.json`, which points at
`hooks/codex-hooks.json`; Claude Code reads `.claude-plugin/plugin.json` and `hooks/hooks.json`. One script serves
both and takes `--host codex` from the Codex hook command. On Codex the script:

- reads `apply_patch` calls and patches inside Bash heredocs, and asks about the files a patch touches;
- rewrites every allowed command to `cd <cwd> && <command>`, because Codex does not tell a hook the command's
  `workdir`;
- answers every refusal with a reason, and never crashes into a non-answer, because Codex lets a call run when its
  hook fails;
- has no `ask`. A held change is refused with a short id, and the person approves it by typing `approve <id>` in the
  chat. A `UserPromptSubmit` hook records the approval and tells the model to run the same call again. The model
  cannot type a prompt, and the gate refuses any command that touches its state directory.

Installing is one command line: `codex plugin marketplace add`, `codex plugin add`, then
`patton_gate.py codex-trust`, which trusts the plugin's hooks through `codex app-server` (`hooks/list`, then
`config/batchWrite` of `hooks.state`) and checks that Codex reports them `trusted`. Codex drops trust when a hook
changes, so `codex-trust` runs again after an upgrade. The repository's `.agents/plugins/marketplace.json` lists the
same plugin directory for Codex.

## Fixes found while designing

- `rm -rf /` made the classifier walk the whole file system, and the hook ran into its 30 s timeout. A recursive
  removal of a directory outside the project now names nothing, and one of an ancestor of the project names every
  file under the project, without walking anything else.

## Verification

1. Unit tests for every field, the layering, the tighten-only rule, the error paths, and each mission limit, with a
   test per guard that fails when the guard is removed (mutation run).
2. The bypass corpus against the frozen gate and the plugin, unchanged.
3. End to end on real Claude Code with a scripted model that dispatches subagents ($0): each limit refuses where it
   should, and an unconfigured run matches 0.1.1.
4. End to end on real Codex 0.159 with a scripted model ($0): a patch to a protected file is held, `approve <id>`
   lets it through, a crash refuses.
5. The installer in a scratch `CODEX_HOME`, and both installs in the Ubuntu VMs.
