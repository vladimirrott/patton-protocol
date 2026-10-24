"""OpenCode's dialect, spoken by our own plugin (harness/opencode/plugin/patton-gate.js), not by OpenCode itself.

OpenCode has no command hooks: a plugin's `tool.execute.before(input, output)` runs in-process and blocks a call by
throwing. The plugin pipes this JSON to the bridge:

    {"tool": "<opencode tool id>", "args": {...}, "sessionID": "...", "callID": "...", "directory": "/work"}

and reads back one line, `{"allow": true}` or `{"allow": false, "reason": "..."}`. It throws unless the bridge
exited 0 with `allow` exactly true, so a crash, a timeout or unreadable output blocks.

Tool ids and arguments are OpenCode v1.19.4's (packages/opencode/src/tool/*.ts): `bash` {command, workdir?},
`edit` {filePath, oldString, newString}, `write` {filePath, content}, `apply_patch` {patchText}. A patch becomes a
shell `rm -f -- <every path it names>`, the same stand-in the gate uses for Codex patches, so each named file is
checked as a change. A patch that names no file raises, and the bridge blocks it. Tools that only read, ask or
plan pass; any other tool goes to the gate under its own name, and the gate refuses a tool it does not know.
"""

from __future__ import annotations

import json
import os
import shlex

READS = {"read", "glob", "grep", "list", "webfetch", "websearch", "codesearch", "todowrite", "todoread", "skill",
         "question", "invalid", "lsp", "plan_enter", "plan_exit",
         # A subagent's own tool calls go through the same plugin, so starting one changes nothing by itself.
         "task"}
EDITS = {"edit": "Edit", "write": "Write"}
PATCH_OPERATIONS = ("*** Add File:", "*** Update File:", "*** Delete File:", "*** Move to:")


def patch_command(patch: object, directory: str) -> str:
    """`rm -f -- <paths>` for every file a `*** Begin Patch` patch adds, updates, deletes or moves to."""
    if not isinstance(patch, str):
        raise ValueError("the patch is not text")
    lines = [line.strip() for line in patch.strip().splitlines()]
    if not lines or lines[0] != "*** Begin Patch":
        raise ValueError("the patch has no '*** Begin Patch' line")
    paths: list[str] = []
    for line in lines:
        for op in PATCH_OPERATIONS:
            if line.startswith(op):
                path = line[len(op):].strip()
                if path and path not in paths:
                    paths.append(path)
    if not paths:
        raise ValueError("the patch names no file")
    return "rm -f -- " + " ".join(shlex.quote(p if os.path.isabs(p) else os.path.join(directory, p)) for p in paths)


def parse(payload: dict) -> dict | None:
    tool = payload.get("tool")
    if not isinstance(tool, str) or not tool:
        raise ValueError("the payload names no tool")
    args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
    directory = payload.get("directory")
    if not (isinstance(directory, str) and os.path.isabs(directory)):
        raise ValueError("the payload names no absolute directory")
    session = str(payload.get("sessionID") or "session")
    if tool in READS:
        return None
    if tool == "bash":
        workdir = args.get("workdir")
        cwd = os.path.join(directory, workdir) if isinstance(workdir, str) and workdir else directory
        return {"tool": "Bash", "input": {"command": args.get("command")}, "cwd": cwd, "session": session}
    if tool in EDITS:
        return {"tool": EDITS[tool], "input": {"file_path": args.get("filePath")}, "cwd": directory, "session": session}
    if tool == "apply_patch":
        return {"tool": "Bash", "input": {"command": patch_command(args.get("patchText"), directory)},
                "cwd": directory, "session": session}
    return {"tool": tool, "input": args, "cwd": directory, "session": session}


def emit(verdict: str, reason: str) -> tuple[str, int]:
    if verdict == "allow":
        return json.dumps({"allow": True}), 0
    return json.dumps({"allow": False, "reason": reason}), 0


def emit_error(reason: str) -> tuple[str, int]:
    return json.dumps({"allow": False, "reason": reason}), 2
