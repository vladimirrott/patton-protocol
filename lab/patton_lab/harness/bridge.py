"""One command hook for every harness: map the harness's hook payload onto the patton-gate plugin, and back.

    python3 bridge.py <dialect>      # the harness pipes its hook JSON to stdin

A dialect is a module in `dialects/` with these functions:

- `parse(payload: dict) -> dict | None`: the gate's view of the event. A tool call is {"tool": "Bash"|"Write"|"Edit",
  "input": {...}, "cwd": str, "session": str}; None when the call is not one the gate guards (a read). A dialect whose
  harness can refuse a stop returns {"event": "Stop", "final": <the agent's last message>, "cwd", "session"} for
  it, and {"event": "UserPromptSubmit", "prompt", "cwd", "session"} for a new request where the harness has one;
- `emit(verdict: str, reason: str) -> tuple[str, int]`: what to print on stdout and the exit code, for a verdict of
  "allow" or "block" on a tool call;
- `emit_error(reason: str) -> tuple[str, int]`: how to block a tool call when anything failed;
- optional `emit_stop(verdict: str, reason: str) -> tuple[str, int]`: "allow" or "block" for a stop;
- optional `emit_context(verdict: str, reason: str, context: str) -> tuple[str, int]`: a tool-call verdict that also
  carries a discipline reminder for the model. A dialect without it gets the verdict alone.

The bridge fails closed on tool calls: any error in reading the payload, in the dialect, or in the gate blocks the
call with a reason. A harness with no ask (every one except Claude Code) gets the gate's ask as a block, because
nobody is there to answer it headless; the reason tells the agent what was held. A stop is the opposite: a stop
the bridge cannot check is allowed, and after PATTON_BRIDGE_STOP_CAP refusals in a session (default 4) every
later stop is allowed whatever the gate says, because several harnesses repeat a refused stop without a limit.

The project root and the gate's state directory come from PATTON_GATE_ROOT and PATTON_GATE_STATE when set (the lab
sets both), else from the payload's cwd and session. The first event in a session writes the gate's manifest, as
Claude Code's SessionStart would, and, when PATTON_GATE_MISSION is set, records it as the person's request.

With PATTON_BRIDGE_SENTINEL set, the bridge ends its stdout with the SENTINEL line once it has an answer; the
fail-closed shim (`shim/patton-hook.sh`) treats an answer without it as a crash.
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
SENTINEL = "#patton-bridge-done"
STOP_CAP = 4


def _load_gate():
    import importlib.util
    spec = importlib.util.spec_from_file_location("patton_gate_bridge", GATE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load the gate at {GATE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _gate_env(call: dict, environ: dict) -> dict:
    env = dict(environ)
    env["PATTON_GATE_ROOT"] = env.get("PATTON_GATE_ROOT") or call["cwd"]
    env.setdefault("PATTON_GATE_STATE", os.path.join(env.get("HOME", "/tmp"), ".patton-gate", call["session"]))
    return env


def _hook(gate, event: dict, env: dict) -> dict:
    out = io.StringIO()
    gate.main(io.StringIO(json.dumps(event)), out, env)
    text = out.getvalue().strip()
    return json.loads(text) if text else {}


def _start(gate, call: dict, env: dict) -> None:
    """The session's start, once: the manifest, and the lab's mission as the person's request."""
    state, root = env["PATTON_GATE_STATE"], env["PATTON_GATE_ROOT"]
    if os.path.isfile(os.path.join(state, "manifest.json")):
        return
    base = {"session_id": call["session"], "cwd": root}
    _hook(gate, {**base, "hook_event_name": "SessionStart"}, env)
    if env.get("PATTON_GATE_MISSION") and call.get("event") != "UserPromptSubmit":
        _hook(gate, {**base, "hook_event_name": "UserPromptSubmit", "prompt": env["PATTON_GATE_MISSION"]}, env)


def decide(call: dict, environ: dict) -> tuple[str, str, str]:
    """("allow" | "block", reason, reminder context) from the gate for one parsed tool call."""
    gate = _load_gate()
    env = _gate_env(call, environ)
    _start(gate, call, env)
    event = {"hook_event_name": "PreToolUse", "session_id": call["session"], "cwd": call["cwd"],
             "tool_name": call["tool"], "tool_input": call["input"], "permission_mode": "default",
             "tool_use_id": call.get("id", "")}
    decision = _hook(gate, event, env).get("hookSpecificOutput") or {}
    verdict = decision.get("permissionDecision")
    reason = decision.get("permissionDecisionReason") or "Patton gate: refused."
    context = decision.get("additionalContext") or ""
    if verdict in (None, "allow"):
        return "allow", "", context
    if verdict == "ask":
        return "block", reason + " Nobody can answer a question in this session, so the change was held.", context
    return "block", reason, context


def turn(call: dict, environ: dict) -> tuple[str, str]:
    """("allow" | "block", reason) for a stop; ("allow", "") after recording a prompt."""
    gate = _load_gate()
    env = _gate_env(call, environ)
    _start(gate, call, env)
    base = {"session_id": call["session"], "cwd": env["PATTON_GATE_ROOT"]}
    if call["event"] == "UserPromptSubmit":
        _hook(gate, {**base, "hook_event_name": "UserPromptSubmit", "prompt": call.get("prompt", "")}, env)
        return "allow", ""
    cap = int(env.get("PATTON_BRIDGE_STOP_CAP") or STOP_CAP)
    count_path = os.path.join(env["PATTON_GATE_STATE"], "bridge-stops.json")
    try:
        refused = int(json.loads(Path(count_path).read_text()))
    except (OSError, ValueError):
        refused = 0
    answer = _hook(gate, {**base, "hook_event_name": "Stop", "stop_hook_active": bool(call.get("active")),
                          "last_assistant_message": call.get("final", "")}, env)
    if answer.get("systemMessage"):
        print(answer["systemMessage"], file=sys.stderr)
    if answer.get("decision") != "block":
        return "allow", ""
    if refused >= cap:
        print(f"Patton bridge: {refused} stops refused in this session, the cap; this one is allowed.", file=sys.stderr)
        return "allow", ""
    Path(count_path).write_text(json.dumps(refused + 1))
    return "block", str(answer.get("reason") or "Patton: the stop was refused.")


def main(argv: list[str], stdin=sys.stdin, stdout=sys.stdout, environ=os.environ) -> int:
    dialect = call = None
    try:
        dialect = importlib.import_module(f"patton_lab.harness.dialects.{argv[0]}")
        call = dialect.parse(json.loads(stdin.read()))
        if call is None:
            text, code = dialect.emit("allow", "")
        elif call.get("event") in ("Stop", "UserPromptSubmit"):
            verdict, reason = turn(call, dict(environ))
            text, code = dialect.emit_stop(verdict, reason) if call["event"] == "Stop" else ("", 0)
        else:
            verdict, reason, context = decide(call, dict(environ))
            if context and hasattr(dialect, "emit_context"):
                text, code = dialect.emit_context(verdict, reason, context)
            else:
                text, code = dialect.emit(verdict, reason)
    except Exception as err:  # noqa: BLE001 - fail closed on a tool call, open on a stop
        reason = f"Patton gate bridge failed ({type(err).__name__}: {str(err)[:200]}), so it blocks this call."
        if isinstance(call, dict) and call.get("event") in ("Stop", "UserPromptSubmit"):
            reason = f"Patton gate bridge could not check this stop ({type(err).__name__}), so it is allowed."
            text, code = "", 0
            try:
                if call["event"] == "Stop":
                    text, code = dialect.emit_stop("allow", "")
            except Exception:  # noqa: BLE001
                text, code = "", 0
        else:
            try:
                text, code = dialect.emit_error(reason) if dialect is not None else ("", 2)
            except Exception:  # noqa: BLE001
                text, code = "", 2
        print(reason, file=sys.stderr)
    if text:
        stdout.write(text if text.endswith("\n") else text + "\n")
    if environ.get("PATTON_BRIDGE_SENTINEL"):
        stdout.write(SENTINEL + "\n")
    return code


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    sys.exit(main(sys.argv[1:]))
