"""DeepSeek Harness (dsh 0.2.1-rc.3) through its Claude-Code-compatible bridge, `@deepseek-ai/dsh-hooks-claude-code`.

The bridge speaks Claude Code's output dialect (exit 0 with `hookSpecificOutput.permissionDecision: "deny"`, or exit 2
with the reason on stderr), so `emit`, `emit_error` and `emit_stop` are the `claude` dialect's. The payload differs in
one way that matters: `tool_name` is dsh's own tool name, lowercase (`bash`, `write`, `edit`), not Claude Code's, so
the `claude` dialect would pass every call through as "not guarded". The inputs already use Claude Code's keys
(`command`, `file_path`, `content`, `old_string`, `new_string`). The `bash` tool takes a `workdir` that replaces the
session directory for that one command, so it becomes the cwd the gate resolves relative paths against.
(`packages/experimental/hooks-claude-code/src/index.ts` `preToolPayload`; `docs/tool-catalog.md`.)

Stop: dsh's `Stop` payload is {session_id, transcript_path: "", hook_event_name, stop_hook_active: false, cwd} and
carries no last assistant message (`stopPayload`), and the transcript path is always empty. The session's own log is
the only record of it: `$DSH_HOME/sessions/<cwd key>/<session_id>/session.v4.jsonl.zstd` (DSH_HOME defaults to
~/.dsh; one zstd frame per flush), and `final_text` reads the last `assistant/message` text from it through
`harness/dsh/read_session.mjs` on dsh's own Node. A `{"decision": "block", "reason"}` answer steers the agent with the reason
as a user message; dsh has no cap of its own, so the bridge's PATTON_BRIDGE_STOP_CAP is the only one.

Reminders: dsh drops `additionalContext` from a PreToolUse answer. A refused call carries the reminder in its reason;
an allowed call's reminder is left in the gate's state dir under the call's id, for the PostToolUse hook
`harness/dsh/context_hook.py`, which dsh does inject before the next model request.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from patton_lab.harness.dialects.claude import emit, emit_error, emit_stop  # noqa: F401 - re-exported for the bridge

TOOLS = {"bash": "Bash", "write": "Write", "edit": "Edit"}
READER = Path(__file__).resolve().parents[1] / "dsh" / "read_session.mjs"
_LAST: dict[str, str] = {}  # the call this process parsed, for emit_context (one hook process, one call)


def parse(payload: dict) -> dict | None:
    session = str(payload.get("session_id") or "session")
    if payload.get("hook_event_name") == "Stop":
        return {"event": "Stop", "final": final_text(session), "cwd": payload["cwd"], "session": session,
                "active": bool(payload.get("stop_hook_active"))}
    if payload.get("hook_event_name") not in (None, "PreToolUse"):
        return None
    tool = TOOLS.get(payload.get("tool_name"))
    if tool is None:
        return None
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        raise ValueError("tool_input is not an object")
    cwd = payload["cwd"]
    workdir = tool_input.get("workdir")
    if tool == "Bash" and isinstance(workdir, str) and workdir:
        cwd = workdir if os.path.isabs(workdir) else os.path.join(cwd, workdir)
    call_id = str(payload.get("tool_use_id") or "")
    _LAST.clear()
    _LAST["id"] = call_id
    return {"tool": tool, "input": tool_input, "cwd": cwd, "session": session, "id": call_id}


def emit_context(verdict: str, reason: str, context: str) -> tuple[str, int]:
    if verdict != "allow":
        return emit(verdict, f"{reason}\n{context}")
    state_dir, call_id = os.environ.get("PATTON_GATE_STATE"), _LAST.get("id")
    if state_dir and call_id:
        path = stash_path(state_dir, call_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(context)
    return emit("allow", "")


def stash_path(state_dir: str, call_id: str) -> str:
    """Where an allowed call's reminder waits for the PostToolUse hook (the same path `context_hook.py` reads)."""
    return os.path.join(state_dir, "dsh-context", hashlib.sha256(call_id.encode()).hexdigest()[:32] + ".txt")


def _session_logs(session: str, environ) -> list[str]:
    home = environ.get("DSH_HOME") or os.path.join(environ.get("HOME") or os.path.expanduser("~"), ".dsh")
    if not session or any(c in session for c in "/*?[]") or session.startswith("."):
        return []
    found = []
    for name in ("session.v4.jsonl.zstd", "session.v4.jsonl"):
        found += glob.glob(os.path.join(glob.escape(home), "sessions", "*", glob.escape(session), name))
    return sorted(found)


def _read_log(path: str, environ) -> list[str]:
    """The log's lines; a zstd log is read by `harness/dsh/read_session.mjs` on dsh's own Node, since the image's
    Python has no zstd."""
    if not path.endswith(".zstd"):
        with open(path, encoding="utf-8") as handle:
            return handle.read().splitlines()
    node = shutil.which("node", path=environ.get("PATH"))
    if node is None:
        return []
    done = subprocess.run([node, str(READER), path], capture_output=True, text=True, errors="replace", timeout=15,
                          stdin=subprocess.DEVNULL)
    return done.stdout.splitlines() if done.returncode == 0 else []


def final_text(session: str, environ=os.environ) -> str:
    """The last assistant text in the session's log, or "" when there is none to read (a stop is then checked
    without it: the report rule sees an empty message)."""
    found = ""
    for path in _session_logs(session, environ):
        try:
            lines = _read_log(path, environ)
        except (OSError, subprocess.SubprocessError):
            continue
        for line in lines:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            text = _assistant_text(record)
            if text:
                found = text
    return found


def _assistant_text(record) -> str:
    """Assistant text in one log record, whichever of the shapes dsh writes: a message, or an event wrapping one."""
    if not isinstance(record, dict):
        return ""
    for key in ("message", "payload", "data", "entry"):
        inner = record.get(key)
        if isinstance(inner, dict) and inner is not record:
            text = _assistant_text(inner)
            if text:
                return text
    if record.get("role") != "assistant":
        return ""
    content = record.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""
