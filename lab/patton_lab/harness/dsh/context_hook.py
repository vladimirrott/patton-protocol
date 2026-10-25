"""dsh's PostToolUse hook: hand the model the reminder the gate attached to this call's PreToolUse answer.

dsh's Claude-Code bridge drops `additionalContext` from a PreToolUse answer (`tools/pre-execute` reads only the
decision) but injects it from a PostToolUse answer as a user message before the next model request
(`hooks-claude-code/src/index.ts`, `tools/post-execute`). So the `dsh` dialect's `emit_context` leaves an allowed
call's reminder in the gate's state dir under the call's id, and this hook prints it after the call ran:

    {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "<the reminder>"}}

Standard library only and run with `python3 -I`. Anything that goes wrong prints nothing and exits 0: the call has
already run, and a lost reminder is not worth turning its result into a block.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys


def stash_path(state_dir: str, call_id: str) -> str:
    return os.path.join(state_dir, "dsh-context", hashlib.sha256(call_id.encode()).hexdigest()[:32] + ".txt")


def main(stdin=sys.stdin, stdout=sys.stdout, environ=os.environ) -> int:
    try:
        payload = json.loads(stdin.read())
        call_id = str(payload.get("tool_use_id") or "")
        state_dir = environ.get("PATTON_GATE_STATE") or ""
        if not call_id or not state_dir:
            return 0
        path = stash_path(state_dir, call_id)
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        os.unlink(path)
    except (OSError, ValueError, AttributeError):
        return 0
    if text:
        stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}})
                     + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
