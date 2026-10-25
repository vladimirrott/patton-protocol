"""The fleet port of Cline CLI 3.0.73: one headless `cline` task in podman (`patton_lab/harness/cline/pod.py`).

The container, relay, mounts and hook file are the trace's (`harness/cline/trace.py`): `cline auth` writes an
openai-compatible provider whose base URL is the relay's 127.0.0.1:18080/v1 and whose model is the run's, then
`cline -P openai-compatible -m <model> --json --auto-approve true <mission>` runs the task. The relay leads to the
recorder, which serves Chat Completions streaming or not, so `{model_base}` is http://127.0.0.1:18080.

Approvals: `--auto-approve true` (the CLI's default act mode) runs every tool without asking. The trace's `--yolo`
is not used here: under it a run ends only when the model calls `submit_and_exit`, and a plain text answer gets a
"[SYSTEM] This run is not complete" reminder and another request, with no cap (674 requests in 40 s against a
scripted worker that answers in text). Without it, the run ends on the model's first answer without a tool call,
as every other port's does. It offers more tools than `--yolo` (apply_patch for a gpt model id instead of editor,
search_codebase, fetch_web_content, ask_question, spawn_agent and the team_* tools); the dialect maps apply_patch
to the gate and sends spawn_agent and team_* to the gate under their own names, which it refuses, which cancels the run.

Layout in the container: /work (rw), /patton-state (rw), /home/user (rw: ~/.cline with the hook file copied from
`harness/cline/hooks/`), /patton/lab and /patton/plugins (ro), /opt/cline (ro), git from `harness/gitmount.py`, and
/patton-config/config.json (ro) on a gated arm. With no gate config the hooks directory stays empty. The final text
is the `run_result` event's text (else the last text block) in the NDJSON stream.

STOP is False: Cline has no hook that can refuse a stop (its agent_end hook's output is never read), so the gate's
check_before_stop and Verifier never run on this port. CONTEXT is True: a non-cancelling answer's `context` is
appended for the model (trace S8). The shim's limit is PATTON_HOOK_LIMIT=110, under Cline's fixed 120 s hook
timeout, so a T1 triage call that takes longer than about 110 s is refused (fail closed) rather than answered.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ...harness import gitmount
from ...harness.cline import pod
from .. import spec

VERSION = pod.VERSION
STOP = False
CONTEXT = True
HOOK_LIMIT_S = 110
FLAGS = "--auto-approve true"


def available() -> str | None:
    return pod.why_not() or gitmount.why_not()


def shell_step(command: str) -> dict:
    return {"tool": "run_commands", "args": {"commands": [command]}}


def model_base() -> str:
    return f"http://127.0.0.1:{pod.PORT_IN}"


def prepare(run_spec: spec.RunSpec) -> None:
    """The hook file in the run's home on a gated arm, and the gate config with {model_base} filled in."""
    hooks = run_spec.home / ".cline" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    if run_spec.gate_config is None:
        return
    shutil.copyfile(pod.HOOKS / "PreToolUse", hooks / "PreToolUse")
    (hooks / "PreToolUse").chmod(0o755)
    text = run_spec.config_file.read_text(encoding="utf-8").replace("{model_base}", model_base())
    run_spec.config_file.write_text(text, encoding="utf-8")


def mounts_env(run_spec: spec.RunSpec) -> tuple[list[tuple[Path, str]], dict[str, str]]:
    mounts = [(run_spec.work, "/work:rw"), (run_spec.home, "/home/user:rw"), (run_spec.state, "/patton-state:rw"),
              *gitmount.mounts()]
    env: dict[str, str] = {}
    if run_spec.gate_config is not None:
        mounts.append((run_spec.config_file, "/patton-config/config.json:ro"))
        env = {"PATTON_GATE_STATE": "/patton-state", "PATTON_GATE_CONFIG": "/patton-config/config.json",
               "PATTON_HOOK_LIMIT": str(HOOK_LIMIT_S), **pod.SHIM_ENV, **run_spec.env}
    return mounts, env


def final_text(result: pod.Result) -> str:
    text = ""
    for event in result.events():
        if event.get("type") == "run_result" and isinstance(event.get("text"), str):
            return event["text"]
        inner = event.get("event") or {}
        if inner.get("type") == "content_end" and inner.get("contentType") == "text":
            text = str(inner.get("text") or "")
    return text


def run(run_spec: spec.RunSpec) -> spec.RunResult:
    prepare(run_spec)
    mounts, env = mounts_env(run_spec)
    result = pod.run("fleet", run_spec.model_port, pod.task(run_spec.model, run_spec.mission, flags=FLAGS),
                     mounts=mounts, env=env, timeout=run_spec.timeout_s)
    return spec.RunResult(result.code, result.timed_out, final_text(result), result.seconds, result.stdout[-4000:],
                          result.stderr[-4000:])

