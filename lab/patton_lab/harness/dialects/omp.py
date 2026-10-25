"""oh-my-pi's dialect (can1357/oh-my-pi 18.12.6): the JSON the Patton extension sends, and the verdict it reads.

The extension (lab/patton_lab/harness/omp/gate.ts) sends one object per `tool_call` event:

    {"tool": event.toolName, "input": event.input, "cwd": ctx.cwd, "session": "..."}

and reads one line back: {"block": false} to allow, {"block": true, "reason": "..."} to block, either with a
"context" string when the gate has a discipline reminder for the model (the extension returns it as the tool
call's `additionalContext`, or appends it to the refusal). The extension blocks on anything else, including a
non-zero exit, so `emit_error` exits 2.

At `session_stop` the extension sends {"event": "Stop", "final": <the last assistant text>, "active":
<stop_hook_active>, "cwd", "session"} and reads {"block": true, "reason"} to refuse the stop (it returns
{decision: "block", reason}, which omp delivers to the model as a hidden continuation message) or {"block": false}.
It allows the stop on anything else, and the bridge caps refusals itself, because omp's `block` form has no cap.

omp's tools (docs/tools/*.md, src/edit/schemas.ts):

- `bash` {command, ...} goes to the gate as Bash;
- `write` {path, content} goes as Write;
- `edit` has five wire forms. `replace` {path, old_string, new_string} and `patch` {path, edits: [{rename?}]} name
  their files; `hashline`, `apply_patch` and `sloppy` carry one `input` string, from which the dialect reads every
  `[PATH#TAG]` section header, `MV DEST`, and `*** Add/Update/Delete File:` / `*** Move to:` line. One file goes
  to the gate as Edit; several go as one Bash command that truncates each (`: > 'a'; : > 'b'`), which the gate
  reads as a change to every file named. An edit that names no file goes as an Edit with no path, which the
  gate refuses;
- the read-only tools below pass without asking. Every other tool (eval, task, browser, ...) goes to the gate by
  name and the gate refuses tools it does not know, so `eval`, which can start a shell, is blocked.
"""

from __future__ import annotations

import json
import re
import shlex

READ_ONLY = {"read", "grep", "glob", "find", "ast_grep", "todo", "ask"}
HASHLINE = re.compile(r"^\[(.+?)#[0-9A-Fa-f]{4}\]\s*$", re.M)
MOVE = re.compile(r"^MV\s+(?:\"([^\"]+)\"|'([^']+)'|(\S+))\s*$", re.M)
PATCH = re.compile(r"^\*\*\*\s+(?:Add|Update|Delete)\s+File:\s*(.+?)\s*$|^\*\*\*\s+Move to:\s*(.+?)\s*$", re.M)


def edit_paths(tool_input: dict) -> list[str]:
    paths: list[str] = []
    if isinstance(tool_input.get("path"), str):
        paths.append(tool_input["path"])
    for e in tool_input.get("edits") or []:
        if isinstance(e, dict) and isinstance(e.get("rename"), str):
            paths.append(e["rename"])
    text = tool_input.get("input")
    if isinstance(text, str):
        paths += HASHLINE.findall(text)
        paths += [next(g for g in m if g) for m in MOVE.findall(text)]
        paths += [next(g for g in m if g) for m in PATCH.findall(text)]
    seen: list[str] = []
    for p in paths:
        if p and p not in seen:
            seen.append(p)
    return seen


def parse(payload: dict) -> dict | None:
    cwd = payload.get("cwd")
    if payload.get("event") == "Stop":
        if not isinstance(cwd, str) or not cwd:
            raise ValueError("the stop has no cwd")
        return {"event": "Stop", "final": str(payload.get("final") or ""), "cwd": cwd,
                "session": str(payload.get("session") or "omp"), "active": bool(payload.get("active"))}
    tool, tool_input = payload.get("tool"), payload.get("input")
    if not isinstance(tool, str) or not isinstance(tool_input, dict):
        raise ValueError("the payload has no tool name or no input object")
    if not isinstance(cwd, str) or not cwd:
        raise ValueError("the payload has no cwd")
    call = {"cwd": cwd, "session": str(payload.get("session") or "omp")}
    if tool in READ_ONLY:
        return None
    if tool == "bash":
        return {**call, "tool": "Bash", "input": {"command": tool_input.get("command")}}
    if tool == "write":
        return {**call, "tool": "Write", "input": {"file_path": tool_input.get("path"),
                                                   "content": tool_input.get("content")}}
    if tool == "edit":
        paths = edit_paths(tool_input)
        if len(paths) <= 1:
            return {**call, "tool": "Edit", "input": {"file_path": paths[0] if paths else ""}}
        return {**call, "tool": "Bash", "input": {"command": "; ".join(f": > {shlex.quote(p)}" for p in paths)}}
    return {**call, "tool": tool, "input": tool_input}


def emit(verdict: str, reason: str) -> tuple[str, int]:
    if verdict == "allow":
        return json.dumps({"block": False}), 0
    return json.dumps({"block": True, "reason": reason or "Patton gate: refused."}), 0


def emit_context(verdict: str, reason: str, context: str) -> tuple[str, int]:
    text, code = emit(verdict, reason)
    return json.dumps({**json.loads(text), "context": context}), code


def emit_stop(verdict: str, reason: str) -> tuple[str, int]:
    if verdict == "block":
        return json.dumps({"block": True, "reason": reason or "Patton: the stop was refused."}), 0
    return json.dumps({"block": False}), 0


def emit_error(reason: str) -> tuple[str, int]:
    return json.dumps({"block": True, "reason": reason}), 2
