"""The S7 stop hook for Gemini CLI: refuse the agent's stop once (or every time, with `always`), and count calls.

    python3 stop_hook.py <once|always> <count-file>

It answers AfterAgent with {"decision": "deny", "reason": ...}; the CLI sends the reason to the model as a new
prompt (docs/hooks/reference.md, AfterAgent). `once` lets the stop through when the input says
`stop_hook_active`, which marks a retry round.
"""

from __future__ import annotations

import json
import sys

REASON = "PATTON-STOP-CHECK: before you stop, say what you verified."


def main() -> int:
    mode, count_file = sys.argv[1], sys.argv[2]
    event = json.loads(sys.stdin.read() or "{}")
    with open(count_file, "a", encoding="utf-8") as f:
        f.write(json.dumps({"stop_hook_active": event.get("stop_hook_active"),
                            "event": event.get("hook_event_name")}) + "\n")
    if mode == "always" or not event.get("stop_hook_active"):
        print(json.dumps({"decision": "deny", "reason": REASON}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
