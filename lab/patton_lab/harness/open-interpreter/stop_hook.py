"""The S7 stop hook: refuse the agent's stop once (or every time, with `always`), and count each call.

    python3 stop_hook.py <once|always> <count-file>

It refuses with Open Interpreter's Stop answer, {"decision": "block", "reason": ...}, whose reason the fork feeds
back to the model as the next prompt (codex-rs/core/src/session/turn.rs). `once` lets the stop through when the
input says `stop_hook_active`, the fork's only loop guard, which it leaves to the hook.
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
        print(json.dumps({"decision": "block", "reason": REASON}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
