"""The Codex hook: the frozen gate's decisions, in the only forms Codex 0.159 acts on.

Codex runs this file as its PreToolUse, PostToolUse and SessionStart hook in the worker's container. It loads
`patton_gate.py` from PATTON_GATE_DIR (default /gate) and asks it about each call:

- A Bash call goes to the gate as it is. A refusal passes through. An allow comes back as an allow that rewrites
  the command to `cd <cwd> && <command>`: the hook never sees exec_command's `workdir`, so without the rewrite a
  command could run in a directory the gate did not check.
- A patch (the apply_patch tool, or a shell heredoc that runs apply_patch) goes to the gate as one synthetic
  `rm -f -- <every path it touches>`, so the operator answers once for all its files. A patch the adapter cannot
  read is refused.
- Codex lets a call run when its hook crashes, exits 1 or prints an answer it does not support, so every path
  through this file ends in a refusal or a supported answer, and the exit code is always 0.

Standard library only: it runs on the container's python3.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import posixpath
import re
import shlex
import sys
from types import ModuleType
from typing import Any

DEFAULT_GATE_DIR = "/gate"
TOOLS = ("Bash", "apply_patch")
OPERATIONS = ("*** Add File:", "*** Update File:", "*** Delete File:", "*** Move to:")
HEREDOC = re.compile(r"\A\s*(?:cd\s+(?P<dir>[^\s;&|<>]+)\s*&&\s*)?apply_patch\s*<<-?\s*(?P<q>['\"]?)(?P<tag>\w+)(?P=q)"
                     r"[ \t]*\n(?P<body>.*?)\n(?P=tag)\s*\Z", re.S)
UNREADABLE_PATCH = ("Patton gate: this patch cannot be checked: it has no '*** Begin Patch' line or names no file. "
                    "Rewrite it as a patch that names each file it adds, updates, deletes or moves.")


def _deny(reason: str) -> dict[str, Any]:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def patch_paths(patch: str) -> list[str] | None:
    """Every path a patch adds, updates, deletes or moves to, in order; None when it is not a readable patch."""
    lines = [line.strip() for line in patch.strip().splitlines()]
    if not lines or lines[0] != "*** Begin Patch":
        return None
    paths: list[str] = []
    for line in lines:
        for op in OPERATIONS:
            if line.startswith(op):
                path = line[len(op):].strip()
                if path and path not in paths:
                    paths.append(path)
    return paths or None


def heredoc_patch(command: str) -> tuple[str, str] | None:
    """(patch, directory) for a shell command that runs apply_patch on a heredoc, as Codex intercepts it."""
    match = HEREDOC.match(command)
    if not match:
        return None
    directory = match.group("dir") or ""
    if directory:
        parts = shlex.split(directory)
        if len(parts) != 1:
            return None
        directory = parts[0]
    return match.group("body"), directory


def synthetic_command(paths: list[str], directory: str = "") -> str:
    joined = [p if posixpath.isabs(p) or not directory else posixpath.normpath(posixpath.join(directory, p)) for p in paths]
    return "rm -f -- " + " ".join(shlex.quote(p) for p in joined)


def load_gate(environ: dict[str, str]) -> ModuleType:
    path = os.path.join(environ.get("PATTON_GATE_DIR") or DEFAULT_GATE_DIR, "patton_gate.py")
    spec = importlib.util.spec_from_file_location("patton_gate", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"no gate at {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["patton_gate"] = module  # dataclasses look the module up while the class is built
    spec.loader.exec_module(module)
    return module


def _ask_gate(gate: Any, event: dict[str, Any], environ: dict[str, str]) -> dict[str, Any]:
    out = io.StringIO()
    gate.main(io.StringIO(json.dumps(event)), out, environ)
    text = out.getvalue().strip()
    return json.loads(text) if text else {}


def decide(event: dict[str, Any], environ: dict[str, str], gate: Any) -> dict[str, Any] | None:
    """The answer to print for one hook event, or None for no answer."""
    name = event.get("hook_event_name")
    if name == "SessionStart":
        _ask_gate(gate, event, environ)
        return None
    tool, tool_input = event.get("tool_name"), event.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if tool not in TOOLS or not isinstance(command, str):
        return _deny(f"Patton gate: the tool {tool!r} is not one the gate can check.") if name == "PreToolUse" else None
    patch = (command, "") if tool == "apply_patch" else heredoc_patch(command)
    gate_event = event
    if patch is not None:
        paths = patch_paths(patch[0])
        if paths is None:
            return _deny(UNREADABLE_PATCH) if name == "PreToolUse" else None
        gate_event = {**event, "tool_name": "Bash", "tool_input": {"command": synthetic_command(paths, patch[1])}}
    if name == "PostToolUse":
        _ask_gate(gate, gate_event, environ)
        return None
    if name != "PreToolUse":
        return None
    cwd = event.get("cwd")
    if tool == "Bash" and not (isinstance(cwd, str) and posixpath.isabs(cwd)):
        return _deny("Patton gate: the hook input names no absolute working directory, so the gate cannot place "
                     "this command.")
    answer = (_ask_gate(gate, gate_event, environ).get("hookSpecificOutput") or {})
    verdict, reason = answer.get("permissionDecision"), str(answer.get("permissionDecisionReason") or "").strip()
    if verdict == "deny":
        return _deny(reason or "Patton gate: refused.")
    if verdict != "allow":
        return _deny(f"Patton gate: the gate gave no decision ({verdict!r}), so the call is refused.")
    if tool == "apply_patch":
        return None  # Codex has no plain allow; no answer lets the patch run as written
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                   "updatedInput": {"command": f"cd {shlex.quote(cwd)} && {command}"}}}


def main(stdin, stdout, environ, gate: Any = None) -> int:
    try:
        event = json.loads(stdin.read())
        if not isinstance(event, dict):
            raise ValueError("the hook input is not a JSON object")
        answer = decide(event, dict(environ), gate if gate is not None else load_gate(environ))
    except Exception as err:  # noqa: BLE001 - Codex runs the call on any crash, so refuse instead
        answer = _deny(f"Patton gate: the hook failed ({type(err).__name__}: {str(err)[:200]}), so it refuses "
                       "this call rather than let it run unchecked.")
    if answer is not None:
        stdout.write(json.dumps(answer))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.stdin, sys.stdout, os.environ))
