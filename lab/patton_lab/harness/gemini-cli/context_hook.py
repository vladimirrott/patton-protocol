"""Gemini CLI's AfterTool hook for the gate's reminders: hand the model the text the BeforeTool bridge kept.

    python3 context_hook.py        # the CLI pipes its AfterTool JSON {tool_name, tool_input, tool_response} in

A BeforeTool answer cannot reach the model, so `dialects/gemini_cli.emit_context` keeps an allowed call's reminder
in a file; this hook takes it (`gemini_cli.take`) and answers
{"hookSpecificOutput": {"hookEventName": "AfterTool", "additionalContext": ...}}, which the CLI appends to the
tool's result. It decides nothing: with no reminder, or on any error, it prints nothing and exits 0, and the result
goes to the model unchanged.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


def main(stdin=sys.stdin, stdout=sys.stdout) -> int:
    try:
        from patton_lab.harness.dialects import gemini_cli
        event = json.loads(stdin.read() or "{}")
        text = gemini_cli.take(str(event.get("tool_name") or ""), event.get("tool_input") or {})
    except Exception as err:  # noqa: BLE001 - a reminder is never worth failing a tool call
        print(f"Patton context hook: {type(err).__name__}: {err}", file=sys.stderr)
        return 0
    if text:
        stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "AfterTool", "additionalContext": text}})
                     + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
