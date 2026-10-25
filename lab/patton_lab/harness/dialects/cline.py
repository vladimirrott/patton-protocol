"""Cline CLI's dialect (cli-v3.0.73): its `PreToolUse` file hook in, `{"cancel": true, "errorMessage": ...}` out.

Cline runs every file named PreToolUse(.sh/.py/...) in its hook directories before each tool call, blocking, with
the payload on stdin (sdk/packages/core/src/hooks/hook-file-hooks.ts, runToolCallStart):

    {"hookName": "tool_call", "taskId": "...", "workspaceRoots": ["/work"], "agent_id": "...",
     "tool_call": {"id": "...", "name": "run_commands", "input": {...}}, "preToolUse": {...}, ...}

Cline ignores the hook's exit code, a timeout (120 s) and unreadable output: each lets the call run
(runBlockingHookCommands), so the error path prints a block too, and the trace records what Cline does when the
hook dies without printing one.

How a block is said. `{"cancel": true, "errorMessage": "..."}` does not skip the call with a reason: it becomes
`stop` (beforeToolResultFromControl), which aborts the whole run before the tool executes, and the reason never
reaches the model. So a refused `run_commands` is instead rewritten with `overrideInput` to one command that prints
the gate's refusal and exits 1: the original command never runs and the model reads the refusal as the command's
output, as it would from Claude Code. Every other refused tool, and every bridge error, cancels the run.

A reminder from the gate goes out as `context`, which Cline appends for the model (`appendContext`, the parser
around FBe in the shipped binary) unless the answer cancels; a cancel drops it, like the reason.

Tools (sdk/packages/core/src/extensions/tools/schemas.ts): `run_commands` takes shell strings in several shapes
(RunCommandsInputUnionSchema), joined here with " ; " so the gate sees every command; a structured
{command, args} entry runs without a shell and is quoted as one. `editor` {path, old_text?, new_text} edits or
creates a file. `apply_patch` {input} becomes `rm -f -- <every path it names>`, as for OpenCode. Reads, questions
and submit_and_exit pass; any other tool goes to the gate under its own name, and the gate refuses it.
"""

from __future__ import annotations

import json
import os
import shlex

from .opencode import patch_command

_LAST: dict[str, str] = {}  # the tool parse() saw, for emit(); one bridge process handles one call

READS = {"read_files", "search_codebase", "fetch_web_content", "skills", "ask_question", "submit_and_exit"}


def _one(entry: object) -> str:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        command = entry.get("command", entry.get("cmd"))
        args = entry.get("args")
        if isinstance(command, str) and isinstance(args, list) and all(isinstance(a, str) for a in args):
            return shlex.join([command, *args])
        if isinstance(command, str) and args is None:
            return command
    raise ValueError("a run_commands entry is neither a command string nor {command, args}")


def commands(raw: object) -> str:
    """Every command a run_commands input will run, as one shell text."""
    if isinstance(raw, dict) and "commands" in raw:
        raw = raw["commands"]
    entries = raw if isinstance(raw, list) else [raw]
    texts = [_one(e) for e in entries]
    if not texts or not all(t.strip() for t in texts):
        raise ValueError("run_commands has no command")
    return " ; ".join(texts)


def parse(payload: dict) -> dict | None:
    if payload.get("hookName") != "tool_call":
        return None
    call = payload.get("tool_call") or {}
    tool = call.get("name")
    if not isinstance(tool, str) or not tool:
        raise ValueError("the payload names no tool")
    raw = call.get("input")
    if isinstance(raw, str) and tool != "run_commands":
        raw = json.loads(raw)
    roots = payload.get("workspaceRoots") or []
    cwd = roots[0] if roots and isinstance(roots[0], str) and os.path.isabs(roots[0]) else None
    if cwd is None:
        raise ValueError("the payload names no absolute workspace root")
    session = str(payload.get("taskId") or "session")
    _LAST["tool"] = tool
    if tool in READS:
        return None
    if tool == "run_commands":
        return {"tool": "Bash", "input": {"command": commands(raw)}, "cwd": cwd, "session": session}
    args = raw if isinstance(raw, dict) else {}
    if tool == "editor":
        return {"tool": "Edit", "input": {"file_path": args.get("path")}, "cwd": cwd, "session": session}
    if tool == "apply_patch":
        return {"tool": "Bash", "input": {"command": patch_command(args.get("input"), cwd)}, "cwd": cwd,
                "session": session}
    return {"tool": tool, "input": args, "cwd": cwd, "session": session}


def emit(verdict: str, reason: str) -> tuple[str, int]:
    if verdict == "allow":
        return json.dumps({"cancel": False}), 0
    if _LAST.get("tool") == "run_commands":
        refusal = "printf '%s\\n' " + shlex.quote(reason) + "; exit 1"
        return json.dumps({"overrideInput": {"commands": [refusal]}}), 0
    return json.dumps({"cancel": True, "errorMessage": reason}), 0


def emit_context(verdict: str, reason: str, context: str) -> tuple[str, int]:
    text, code = emit(verdict, reason)
    answer = json.loads(text)
    if not answer.get("cancel"):
        answer["context"] = context
    return json.dumps(answer), code


def emit_error(reason: str) -> tuple[str, int]:
    return json.dumps({"cancel": True, "errorMessage": reason}), 2
