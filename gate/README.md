# Patton gate (the studied version)

A Claude Code hook that asks before the agent changes a file that existed when the session started.
One file, standard library only.

**To use the gate, install the plugin** ([`plugins/patton-gate`](../plugins/patton-gate/README.md)):
`claude plugin install patton-gate --marketplace vladimirrott/patton-protocol`. This directory holds the exact
file the 2026-10-21 pre-registration froze, and it never changes, so the study can be replayed against it. The
plugin keeps its classifier and decision rules, passes the same bypass corpus, and adapts it for daily use (it asks
about commands it cannot read, stays silent when nothing protected is at stake, and lists the session's files
without hashing them). This README describes the studied file.

The measurements behind this gate are in [`lab/CC_REPORT.md`](../lab/CC_REPORT.md) (generated from the study's
records) and [`docs/findings/2026-10-22-gate-study-findings.md`](../docs/findings/2026-10-22-gate-study-findings.md).
The findings open with the limits: one backend, a simulated approver, and an unmeasured Claude baseline.

## What it does

- **Protected means "existed at session start".** A `SessionStart` entry records every file under the work
  directory with its sha256. Files the agent creates later are free to change.
- **Writes and edits** (`Write`, `Edit`, `MultiEdit`, `NotebookEdit`) to a protected file need approval.
- **Bash** goes through a reader that works out which files a command changes: `rm`, `mv`, `cp`, `truncate`,
  `tee`, `dd`, `sed -i`, `find -delete`, redirections, `git rm` and the like. A command that changes only
  new files, or only reads, passes. `git add`, `git commit` and `git branch` count as read-only. `git clean`
  and `git reset` are refused as opaque.
- **A command the reader cannot read is refused.** That covers command substitution, `eval`, `bash -c`,
  interpreters such as `python -c`, shell variables in a path, and anything that is not a known program.
  `xargs`, `find -exec` and `git -C` pass only in front of a read-only command (such as `du`, `ls`, `wc`, or
  `git status`). The refusal asks the agent to rewrite the command as a plain one that names each file; since the gate
  pilot's first pass it no longer tells the agent to stop, because a rewrite passes through the gate again.
- **A granted path is never asked about again.** When the approver answers part of a request, the approved
  paths are granted for the rest of the session (`granted.json` in the state directory).
- **After five refusals the gate denies every gated call that would change something**, even one aimed at a
  new file, as `stalled`, and tells the agent to report the mission blocked. Read-only Bash still passes.

## Install

Save the file, then add this to `.claude/settings.json` (replace the path):

```json
{
  "hooks": {
    "SessionStart": [{"hooks": [{"type": "command", "command": "PATTON_GATE_ROOT=\"$CLAUDE_PROJECT_DIR\" PATTON_GATE_STATE=\"$HOME/.patton-gate/$(basename \"$CLAUDE_PROJECT_DIR\")\" python3 /path/to/patton_gate.py"}]}],
    "PreToolUse": [{"matcher": "Bash|Write|Edit|MultiEdit|NotebookEdit", "hooks": [{"type": "command", "command": "PATTON_GATE_ROOT=\"$CLAUDE_PROJECT_DIR\" PATTON_GATE_STATE=\"$HOME/.patton-gate/$(basename \"$CLAUDE_PROJECT_DIR\")\" python3 /path/to/patton_gate.py"}]}],
    "PostToolUse": [{"matcher": "Bash|Write|Edit|MultiEdit|NotebookEdit", "hooks": [{"type": "command", "command": "PATTON_GATE_ROOT=\"$CLAUDE_PROJECT_DIR\" PATTON_GATE_STATE=\"$HOME/.patton-gate/$(basename \"$CLAUDE_PROJECT_DIR\")\" python3 /path/to/patton_gate.py"}]}]
  }
}
```

Keep the state directory outside the project, as above. A state directory inside the work directory is
left out of the manifest, but the agent can edit it.

## Two ways to answer

- **Interactive (default).** The hook returns `ask` and Claude Code shows its permission prompt with the
  paths. When the command then runs, the `PostToolUse` entry records the approval. In `-p` runs nobody
  can answer and Claude Code denies the call.
- **Study mode.** Set `PATTON_GATE_APPROVER` to a command (the request arrives as JSON on stdin and the
  answer leaves as JSON on stdout) or to an `http://` URL (a POST). The request is
  `{"run": ..., "paths": [...], "tool": ..., "command": ...}`. The answer is
  `{"approved": [...], "denied": [...]}`. A crash, a timeout (`PATTON_GATE_TIMEOUT`, default 10 s), a
  refused connection or any other answer shape counts as a refusal.

Both modes run the same decision function. They differ only in who answers.

## The ledger

Every decision appends one line to `$PATTON_GATE_STATE/ledger.jsonl`:

```json
{"t": 1760000000.0, "event": "PreToolUse", "tool": "Bash", "paths": ["a.csv"], "granted": ["a.csv"],
 "denied": [], "verdict": "approved", "approver": "operator", "run": "", "command": "rm a.csv",
 "tool_use_id": "toolu_01"}
```

`verdict` is `allow`, `deny`, `asked`, `approved`, `refused` or `stalled`. In study mode an `approved` or
`refused` entry carries `paths` (everything the request asked about), `granted` (the paths the approver approved) and
`denied` (the rest). Set `PATTON_GATE_OPERATOR` to name the person who approves; the `approver` field holds
that name, or `operator` when it is unset. The ledger records the decision. It does not prove the operator
was a person.

## Limits

- It reads shell commands. It does not sandbox them. The threat is an honest agent that makes a
  destructive mistake, not one that is trying to defeat the gate.
- A refusal makes an agent try something else. The corpus in `tests/test_gate_corpus.py` lists the forms
  it covers, and `tests/tools/mutate_gate.py` proves each rule is needed.
- `find -delete` is treated as touching every file under its start paths, whatever the `-name` says.
- The hook does not stop writes made by programs it allows to run, such as a build tool. Those programs are
  unknown commands, and unknown commands are refused.
