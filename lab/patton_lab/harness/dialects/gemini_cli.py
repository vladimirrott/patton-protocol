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
with exit code 0.
"""

from __future__ import annotations

import json
import posixpath

TOOLS = {"run_shell_command": "Bash", "write_file": "Write", "replace": "Edit"}


def parse(payload: dict) -> dict | None:
    tool = TOOLS.get(payload.get("tool_name"))
    if tool is None:
        return None
    tool_input = payload.get("tool_input") or {}
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
