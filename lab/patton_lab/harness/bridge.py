"""One command hook for every harness: map the harness's hook payload onto the patton-gate plugin, and back.

    python3 bridge.py <dialect>      # the harness pipes its hook JSON to stdin

A dialect is a module in `dialects/` with three functions:

- `parse(payload: dict) -> dict | None`: the gate's view of the call, {"tool": "Bash"|"Write"|"Edit",
  "input": {...}, "cwd": str, "session": str}; None when the call is not one the gate guards (a read);
- `emit(verdict: str, reason: str) -> tuple[str, int]`: what to print on stdout and the exit code, for a verdict of
  "allow" or "block";
- `emit_error(reason: str) -> tuple[str, int]`: how to block when anything failed.

The bridge fails closed: any error in reading the payload, in the dialect, or in the gate blocks the call with a
reason. A harness with no ask (every one except Claude Code) gets the gate's ask as a block, because nobody is
there to answer it headless; the reason tells the agent what was held.

The project root and the gate's state directory come from PATTON_GATE_ROOT and PATTON_GATE_STATE when set (the lab
sets both), else from the payload's cwd and session. The first call in a session writes the gate's manifest, as
Claude Code's SessionStart would.
"""

from __future__ import annotations

import importlib
import io
import json
import os
import sys
from pathlib import Path

GATE = Path(os.environ.get("PATTON_GATE_SCRIPT") or
            Path(__file__).resolve().parents[3] / "plugins" / "patton-gate" / "scripts" / "patton_gate.py")


def _load_gate():
    import importlib.util
    spec = importlib.util.spec_from_file_location("patton_gate_bridge", GATE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load the gate at {GATE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def decide(call: dict, environ: dict) -> tuple[str, str]:
    """("allow" | "block", reason) from the gate for one parsed call."""
    gate = _load_gate()
    env = dict(environ)
    root = env.get("PATTON_GATE_ROOT") or call["cwd"]
    env["PATTON_GATE_ROOT"] = root
    env.setdefault("PATTON_GATE_STATE", os.path.join(env.get("HOME", "/tmp"), ".patton-gate", call["session"]))
    if not os.path.isfile(os.path.join(env["PATTON_GATE_STATE"], "manifest.json")):
        start = {"hook_event_name": "SessionStart", "session_id": call["session"], "cwd": root}
        gate.main(io.StringIO(json.dumps(start)), io.StringIO(), env)
    event = {"hook_event_name": "PreToolUse", "session_id": call["session"], "cwd": call["cwd"],
             "tool_name": call["tool"], "tool_input": call["input"], "permission_mode": "default"}
    out = io.StringIO()
    gate.main(io.StringIO(json.dumps(event)), out, env)
    text = out.getvalue().strip()
    if not text:
        return "allow", ""
    decision = json.loads(text).get("hookSpecificOutput") or {}
    verdict = decision.get("permissionDecision")
    reason = decision.get("permissionDecisionReason") or "Patton gate: refused."
    if verdict == "allow":
        return "allow", ""
    if verdict == "ask":
        return "block", reason + " Nobody can answer a question in this session, so the change was held."
    return "block", reason


def main(argv: list[str], stdin=sys.stdin, stdout=sys.stdout, environ=os.environ) -> int:
    dialect = None
    try:
        dialect = importlib.import_module(f"patton_lab.harness.dialects.{argv[0]}")
        call = dialect.parse(json.loads(stdin.read()))
        if call is None:
            text, code = dialect.emit("allow", "")
        else:
            verdict, reason = decide(call, dict(environ))
            text, code = dialect.emit(verdict, reason)
    except Exception as err:  # noqa: BLE001 - fail closed on anything
        reason = f"Patton gate bridge failed ({type(err).__name__}: {str(err)[:200]}), so it blocks this call."
        try:
            text, code = dialect.emit_error(reason) if dialect is not None else ("", 2)
        except Exception:  # noqa: BLE001
            text, code = "", 2
        print(reason, file=sys.stderr)
    if text:
        stdout.write(text if text.endswith("\n") else text + "\n")
    return code


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    sys.exit(main(sys.argv[1:]))
