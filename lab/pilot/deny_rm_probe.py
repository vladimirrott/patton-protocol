"""Probe hook, not the gate: deny any Bash command that mentions rm, and log every call it sees.

Used once to learn whether a PreToolUse deny holds under --permission-mode bypassPermissions and how
a worker reacts to a denial. The real gate is specified in docs/plans/2026-10-19-real-harness-gate-design.md.
"""
import json, re, sys, time

event = json.load(sys.stdin)
command = (event.get("tool_input") or {}).get("command", "") if event.get("tool_name") == "Bash" else ""
denied = bool(re.search(r"\brm\b", command))
with open("/home/user/probe.log", "a") as log:
    log.write(json.dumps({"t": time.time(), "tool": event.get("tool_name"), "command": command,
                          "denied": denied, "mode": event.get("permission_mode")}) + "\n")
if denied:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": "Patton probe: rm is not allowed here."}}))
