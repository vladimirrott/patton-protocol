"""Pi's dialect (earendil-works/pi 1.4.0): the JSON the Patton extension sends, and the verdict it reads back.

The extension (lab/patton_lab/harness/pi/gate.ts) sends one object per `tool_call` event:

    {"tool": event.toolName, "input": event.input, "cwd": ctx.cwd, "session": "..."}

and reads one line back: {"block": false} to allow, {"block": true, "reason": "..."} to block. The extension
blocks on anything else, including a non-zero exit, so `emit_error` exits 2.

Pi's built-in tools (src/core/tools/*.ts): `bash` {command, timeout}, `write` {path, content}, `edit` {path,
edits: [{oldText, newText}]} (a legacy single {oldText, newText} is normalised by pi after the event), and the
read-only `read`, `grep`, `find`, `ls`. Any other tool name goes to the gate as is, and the gate refuses tools it
does not know, so a new or custom tool is blocked rather than waved through.
"""

from __future__ import annotations

import json

READ_ONLY = {"read", "grep", "find", "ls"}


def parse(payload: dict) -> dict | None:
    tool, tool_input = payload.get("tool"), payload.get("input")
    if not isinstance(tool, str) or not isinstance(tool_input, dict):
        raise ValueError("the payload has no tool name or no input object")
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        raise ValueError("the payload has no cwd")
    call = {"cwd": cwd, "session": str(payload.get("session") or "pi")}
    if tool in READ_ONLY:
        return None
    if tool == "bash":
        return {**call, "tool": "Bash", "input": {"command": tool_input.get("command")}}
    if tool == "write":
        return {**call, "tool": "Write", "input": {"file_path": tool_input.get("path"),
                                                   "content": tool_input.get("content")}}
    if tool == "edit":
        return {**call, "tool": "Edit", "input": {"file_path": tool_input.get("path")}}
    return {**call, "tool": tool, "input": tool_input}


def emit(verdict: str, reason: str) -> tuple[str, int]:
    if verdict == "allow":
        return json.dumps({"block": False}), 0
    return json.dumps({"block": True, "reason": reason or "Patton gate: refused."}), 0


def emit_error(reason: str) -> tuple[str, int]:
    return json.dumps({"block": True, "reason": reason}), 2
