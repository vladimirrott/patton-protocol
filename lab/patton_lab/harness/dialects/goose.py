"""goose's hook dialect (aaif-goose/goose v1.56.0, `crates/goose/src/hooks/mod.rs`).

goose runs a `PreToolUse` command with `sh -c` and writes {"event", "session_id", "matcher_context", "tool_name",
"tool_input", "working_dir", "tool_call_id"} to its stdin. Its built-in developer tools arrive under bare names:
`shell {command, timeout_secs?}`, `write {path, content}`, `edit {path, before, after}`; a relative path is resolved
against `working_dir` by the gate.

A block is `{"decision": "block", "reason": ...}` on stdout (honored whatever the exit code); an allow is exit 0
with empty stdout. Anything else is "no decision", which goose lets through unless the action sets
`"on_failure": "block"`, so the hook config sets it. An error exits 2, which goose reads as a block with stderr as
the reason (the bridge prints the reason on stderr).
"""

from __future__ import annotations

import json

TOOLS = {"shell": "Bash", "write": "Write", "edit": "Edit"}


def parse(payload: dict) -> dict | None:
    if payload.get("event", "PreToolUse") != "PreToolUse":
        return None
    tool = TOOLS.get(payload.get("tool_name"))
    if tool is None:
        return None
    given = payload.get("tool_input") or {}
    if not isinstance(given, dict):
        raise ValueError("tool_input is not an object")
    if tool == "Bash":
        tool_input = {"command": given.get("command")}
    elif tool == "Write":
        tool_input = {"file_path": given.get("path"), "content": given.get("content")}
    else:
        tool_input = {"file_path": given.get("path"), "old_string": given.get("before"),
                      "new_string": given.get("after")}
    return {"tool": tool, "input": tool_input, "cwd": payload["working_dir"],
            "session": str(payload.get("session_id") or "session")}


def emit(verdict: str, reason: str) -> tuple[str, int]:
    if verdict == "allow":
        return "", 0
    return json.dumps({"decision": "block", "reason": reason}), 0


def emit_error(reason: str) -> tuple[str, int]:
    return "", 2
