"""Claude Code's hook dialect: PreToolUse JSON in, `hookSpecificOutput.permissionDecision` out.

Harnesses that copy it (DeepSeek's `dsh-hooks-claude-code` bridge, Open Interpreter) can use it as is, or a
dialect module of their own can import these and change what differs. An error blocks with exit code 2 and the
reason on stderr, which every Claude-style host reads as a block. A `Stop` event becomes a stop the gate may
refuse with `{"decision": "block", "reason"}`, and `UserPromptSubmit` records the person's request.
"""

from __future__ import annotations

import json

GUARDED = {"Bash", "Write", "Edit", "MultiEdit", "NotebookEdit"}


def parse(payload: dict) -> dict | None:
    name = payload.get("hook_event_name")
    session = str(payload.get("session_id") or "session")
    if name == "Stop":
        return {"event": "Stop", "final": str(payload.get("last_assistant_message") or ""), "cwd": payload["cwd"],
                "session": session, "active": bool(payload.get("stop_hook_active"))}
    if name == "UserPromptSubmit":
        return {"event": "UserPromptSubmit", "prompt": str(payload.get("prompt") or ""), "cwd": payload["cwd"],
                "session": session}
    tool = payload.get("tool_name")
    if tool not in GUARDED:
        return None
    return {"tool": tool, "input": payload.get("tool_input") or {}, "cwd": payload["cwd"], "session": session,
            "id": str(payload.get("tool_use_id") or "")}


def emit(verdict: str, reason: str) -> tuple[str, int]:
    if verdict == "allow":
        return "", 0
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                              "permissionDecisionReason": reason}}), 0


def emit_context(verdict: str, reason: str, context: str) -> tuple[str, int]:
    out = {"hookEventName": "PreToolUse", "additionalContext": context}
    if verdict != "allow":
        out.update(permissionDecision="deny", permissionDecisionReason=reason)
    return json.dumps({"hookSpecificOutput": out}), 0


def emit_stop(verdict: str, reason: str) -> tuple[str, int]:
    return (json.dumps({"decision": "block", "reason": reason}), 0) if verdict == "block" else ("", 0)


def emit_error(reason: str) -> tuple[str, int]:
    return "", 2
