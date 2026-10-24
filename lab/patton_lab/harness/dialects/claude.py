"""Claude Code's hook dialect: PreToolUse JSON in, `hookSpecificOutput.permissionDecision` out.

Harnesses that copy it (DeepSeek's `dsh-hooks-claude-code` bridge, Open Interpreter) can use it as is, or a
dialect module of their own can import these and change what differs. An error blocks with exit code 2 and the
reason on stderr, which every Claude-style host reads as a block.
"""

from __future__ import annotations

import json

GUARDED = {"Bash", "Write", "Edit", "MultiEdit", "NotebookEdit"}


def parse(payload: dict) -> dict | None:
    tool = payload.get("tool_name")
    if tool not in GUARDED:
        return None
    return {"tool": tool, "input": payload.get("tool_input") or {}, "cwd": payload["cwd"],
            "session": str(payload.get("session_id") or "session")}


def emit(verdict: str, reason: str) -> tuple[str, int]:
    if verdict == "allow":
        return "", 0
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                              "permissionDecisionReason": reason}}), 0


def emit_error(reason: str) -> tuple[str, int]:
    return "", 2
