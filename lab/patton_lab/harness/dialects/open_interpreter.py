"""Open Interpreter's hook dialect (rust-v0.0.56, a fork of Codex): Claude-style PreToolUse JSON in and out.

What the fork sends (codex-rs/core/src/tools/handlers/...):

- the shell (`exec_command`) as tool_name "Bash", tool_input {"command": cmd}. The hook never sees the call's
  `workdir` (unified_exec/exec_command.rs `pre_tool_use_payload`), so an allow comes back as an allow that
  rewrites the command to `cd <cwd> && <command>`: the command then runs where the gate checked it;
- a file edit (`apply_patch`) as tool_name "apply_patch", tool_input {"command": <patch text>}. The patch goes to
  the gate as one `rm -f -- <every path it touches>`, as the Codex adapter does, so a patch that touches a
  starting file is held; an unreadable patch is refused;
- MCP tools under their own names. The gate cannot read them, so they are refused.

The fork lets a call run when its hook exits with anything but 0 or 2, or exits 2 with an empty stderr
(hooks/src/events/pre_tool_use.rs), so every refusal here, errors included, is a deny JSON with exit code 0.
"""

from __future__ import annotations

import json
import shlex

from patton_lab.codex.adapter import heredoc_patch, patch_paths, synthetic_command

_pending: dict | None = None  # the Bash call parse() saw, for the cwd rewrite in emit()


class Refused(Exception):
    """A call the gate cannot check; the bridge turns it into a block with this text."""


def parse(payload: dict) -> dict | None:
    global _pending
    _pending = None
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input") or {}
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    cwd = payload.get("cwd")
    session = str(payload.get("session_id") or "session")
    if not isinstance(cwd, str) or not cwd.startswith("/"):
        raise Refused("Patton gate: the hook input names no absolute working directory.")
    if tool not in ("Bash", "apply_patch"):
        raise Refused(f"Patton gate: the tool {tool!r} is not one the gate can check.")
    if not isinstance(command, str):
        raise Refused(f"Patton gate: the {tool} call carries no command.")
    patch = (command, "") if tool == "apply_patch" else heredoc_patch(command)
    if patch is not None:
        paths = patch_paths(patch[0])
        if paths is None:
            raise Refused("Patton gate: this patch cannot be checked: it has no '*** Begin Patch' line or names "
                          "no file.")
        return {"tool": "Bash", "input": {"command": synthetic_command(paths, patch[1])}, "cwd": cwd,
                "session": session}
    _pending = {"command": command, "cwd": cwd}
    return {"tool": "Bash", "input": {"command": command}, "cwd": cwd, "session": session}


def _deny(reason: str) -> tuple[str, int]:
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                              "permissionDecisionReason": reason}}), 0


def emit(verdict: str, reason: str) -> tuple[str, int]:
    if verdict != "allow":
        return _deny(reason or "Patton gate: refused.")
    if _pending is None:
        return "", 0  # a patch, or a call the gate does not guard: no answer lets it run as written
    rewritten = f"cd {shlex.quote(_pending['cwd'])} && {_pending['command']}"
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                              "updatedInput": {"command": rewritten}}}), 0


def emit_error(reason: str) -> tuple[str, int]:
    marker = "Refused: "
    if marker in reason:  # the bridge wraps our own refusal; give the agent the refusal itself
        reason = reason.split(marker, 1)[1].rsplit("), so it blocks this call.", 1)[0]
    return _deny(reason)
