"""Gemini CLI's hook dialect (v0.65.0): `BeforeTool` JSON in, `{"decision": "deny", "reason": ...}` out.

What the CLI sends (packages/core/src/scheduler/hook-utils.ts, tools/definitions/base-declarations.ts):
tool_name is the tool's own name and tool_input its arguments.

- `run_shell_command` {command, dir_path?, is_background?} goes to the gate as Bash, with the cwd moved to
  dir_path when one is given (shell.ts resolves it against the project directory);
- `write_file` {file_path, content} goes as Write, `replace` {file_path, old_string, new_string} as Edit;
- every other tool reaches this hook only if the matcher lets it (the lab's matcher does not), and is treated
  as a read.

The CLI reads a hook's stdout, or its stderr when stdout is empty, and lets the call run on exit code 1, on a
timeout, and on any exit without output (hooks/hookRunner.ts), so every refusal here is a deny JSON on stdout
with exit code 0, and the lab runs the bridge through the fail-closed `shim/patton-hook.sh`.

Stops: `AfterAgent` {prompt, prompt_response, stop_hook_active} is the agent's stop, with `prompt_response` as its
final message. A deny there makes the CLI send the reason to the model as a new prompt and run the turn again
(core/client.ts), with stop_hook_active true; it never limits that itself, so the bridge's cap is what ends it.

Reminders: a BeforeTool answer cannot carry text to the model (scheduler/hook-utils.ts reads only the decision and
tool_input), but an AfterTool answer's `hookSpecificOutput.additionalContext` is appended to the tool's result as
`<hook_context>...</hook_context>` (core/coreToolHookTriggers.ts). So `emit_context` puts a refused call's reminder
after its reason, and keeps an allowed call's reminder in a file keyed by the tool's name and input, under
PATTON_GEMINI_CONTEXT_DIR (default /tmp/patton-gemini-context, the container's own tmpfs); the AfterTool hook
`harness/gemini-cli/context_hook.py` takes it back with `take` and answers with it.
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import uuid

TOOLS = {"run_shell_command": "Bash", "write_file": "Write", "replace": "Edit"}


CONTEXT_DIR = "/tmp/patton-gemini-context"
_last: dict = {}  # the tool call this process parsed, for emit_context (the bridge handles one event per process)


def _context_dir() -> str:
    return os.environ.get("PATTON_GEMINI_CONTEXT_DIR") or CONTEXT_DIR


def context_key(tool_name: str, tool_input: dict) -> str:
    return hashlib.sha256(json.dumps([tool_name, tool_input], sort_keys=True, default=str).encode()).hexdigest()[:32]


def stash(tool_name: str, tool_input: dict, text: str) -> None:
    """Keep an allowed call's reminder until its AfterTool hook takes it."""
    where = os.path.join(_context_dir(), tool_name)
    os.makedirs(where, exist_ok=True)
    tmp = os.path.join(where, f".{uuid.uuid4().hex}")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, os.path.join(where, f"{context_key(tool_name, tool_input)}.{uuid.uuid4().hex[:8]}.txt"))


def take(tool_name: str, tool_input: dict) -> str:
    """The reminders kept for this call, removed as they are read: an exact match on the tool's input, else the
    only one kept for that tool (when the CLI changed the input in between). "" when there is none."""
    where = os.path.join(_context_dir(), str(tool_name))
    try:
        names = sorted(n for n in os.listdir(where) if n.endswith(".txt"))
    except OSError:
        return ""
    key = context_key(tool_name, tool_input)
    mine = [n for n in names if n.startswith(key + ".")] or (names if len(names) == 1 else [])
    out = []
    for name in mine:
        path = os.path.join(where, name)
        try:
            with open(path, encoding="utf-8") as f:
                out.append(f.read())
            os.remove(path)
        except OSError:
            continue
    return "\n\n".join(t for t in out if t)


def parse(payload: dict) -> dict | None:
    if payload.get("hook_event_name") == "AfterAgent":
        cwd = payload.get("cwd")
        if not isinstance(cwd, str) or not cwd.startswith("/"):
            raise ValueError("the hook input names no absolute working directory")
        return {"event": "Stop", "final": str(payload.get("prompt_response") or ""), "cwd": cwd,
                "session": str(payload.get("session_id") or "session"),
                "active": bool(payload.get("stop_hook_active"))}
    _last.clear()
    tool = TOOLS.get(payload.get("tool_name"))
    if tool is None:
        return None
    tool_input = payload.get("tool_input") or {}
    _last.update(name=payload.get("tool_name"), input=tool_input)
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd.startswith("/"):
        raise ValueError("the hook input names no absolute working directory")
    session = str(payload.get("session_id") or "session")
    if tool == "Bash":
        command = tool_input.get("command")
        if not isinstance(command, str):
            raise ValueError("the shell call carries no command")
        where = tool_input.get("dir_path")
        if isinstance(where, str) and where:
            cwd = posixpath.normpath(posixpath.join(cwd, where))
        return {"tool": "Bash", "input": {"command": command}, "cwd": cwd, "session": session}
    path = tool_input.get("file_path")
    if not isinstance(path, str) or not path:
        raise ValueError(f"the {payload.get('tool_name')} call names no file")
    if tool == "Write":
        call_input = {"file_path": path, "content": tool_input.get("content", "")}
    else:
        call_input = {"file_path": path, "old_string": tool_input.get("old_string", ""),
                      "new_string": tool_input.get("new_string", "")}
    return {"tool": tool, "input": call_input, "cwd": cwd, "session": session}


def emit(verdict: str, reason: str) -> tuple[str, int]:
    if verdict == "allow":
        return "", 0
    return json.dumps({"decision": "deny", "reason": reason or "Patton gate: refused."}), 0


def emit_error(reason: str) -> tuple[str, int]:
    return json.dumps({"decision": "deny", "reason": reason}), 0


def emit_stop(verdict: str, reason: str) -> tuple[str, int]:
    if verdict == "allow":
        return "", 0
    return json.dumps({"decision": "deny", "reason": reason or "Patton: the stop was refused."}), 0


def emit_context(verdict: str, reason: str, context: str) -> tuple[str, int]:
    """A refusal carries the reminder after its reason; an allowed call's reminder waits for its AfterTool hook. A
    reminder that cannot be kept is dropped, never turned into a refusal."""
    if verdict != "allow":
        return emit("block", f"{reason or 'Patton gate: refused.'}\n\n{context}")
    if _last.get("name"):
        try:
            stash(str(_last["name"]), _last.get("input") or {}, context)
        except OSError:
            pass
    return emit("allow", "")
