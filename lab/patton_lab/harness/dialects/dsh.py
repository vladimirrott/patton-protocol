"""DeepSeek Harness (dsh 0.2.1-rc.3) through its Claude-Code-compatible bridge, `@deepseek-ai/dsh-hooks-claude-code`.

The bridge speaks Claude Code's output dialect (exit 0 with `hookSpecificOutput.permissionDecision: "deny"`, or exit 2
with the reason on stderr), so `emit` and `emit_error` are the `claude` dialect's. The payload differs in one way
that matters: `tool_name` is dsh's own tool name, lowercase (`bash`, `write`, `edit`), not Claude Code's, so the
`claude` dialect would pass every call through as "not guarded". The inputs already use Claude Code's keys
(`command`, `file_path`, `content`, `old_string`, `new_string`). The `bash` tool takes a `workdir` that replaces the
session directory for that one command, so it becomes the cwd the gate resolves relative paths against.
(`packages/experimental/hooks-claude-code/src/index.ts` `preToolPayload`; `docs/tool-catalog.md`.)
"""

from __future__ import annotations

import os

from patton_lab.harness.dialects.claude import emit, emit_error  # noqa: F401 - re-exported for the bridge

TOOLS = {"bash": "Bash", "write": "Write", "edit": "Edit"}


def parse(payload: dict) -> dict | None:
    tool = TOOLS.get(payload.get("tool_name"))
    if tool is None:
        return None
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        raise ValueError("tool_input is not an object")
    cwd = payload["cwd"]
    workdir = tool_input.get("workdir")
    if tool == "Bash" and isinstance(workdir, str) and workdir:
        cwd = workdir if os.path.isabs(workdir) else os.path.join(cwd, workdir)
    return {"tool": tool, "input": tool_input, "cwd": cwd, "session": str(payload.get("session_id") or "session")}
