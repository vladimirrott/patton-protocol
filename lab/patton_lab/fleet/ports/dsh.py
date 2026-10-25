"""The fleet port of DeepSeek Harness (dsh 0.2.1-rc.3): one headless `dsh --profile headless --json` run per spec.

The container recipe is the harness trace's (`patton_lab/harness/dsh/trace.py` `prepare` and `launch`): the pinned
npm install and Node mounted read-only, `--network none` with a Unix-socket relay to the recorder on host
127.0.0.1:`spec.model_port`, dsh's own sandbox and approvals off (`DSH_PERMISSION_MODE=danger-full-access`), and
git from `harness/gitmount.py` so the gate can run the fixture's checks at a stop. The provider route in the patch
overlay names `spec.model`; dsh sends streaming chat completions, which the recorder answers as a stream.

A guarded arm mounts the Claude-Code hook bridge with the shipped `harness/dsh/hooks.json`: PreToolUse through
`shim/patton-hook.sh dsh` (a crash, a hang or a missing python3 blocks the call), Stop through `... dsh stop` (the
bridge caps refusals at PATTON_BRIDGE_STOP_CAP; dsh has no cap), and the PostToolUse reminder carrier
`harness/dsh/context_hook.py`. The shim's time limit is raised above the gate's own analyst and Verifier timeouts
when the config turns them on, so a slow model call is not taken for a hang. The unguarded arm mounts no hook
plugin at all. The final text is the run's closing `final` event on stdout.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

from patton_lab import isolate
from patton_lab.harness import gitmount
from patton_lab.harness.dsh import trace

from .. import spec as fleet_spec

VERSION = trace.VERSION
STOP = True  # the Stop hook refuses through the bridge, and the reason reaches the model (trace S7g)
CONTEXT = True  # the reminder reaches the model through the PostToolUse carrier (trace S8)
MODEL_BASE = f"http://127.0.0.1:{trace.PORT}"


def available() -> str | None:
    why = trace.check_install() or gitmount.why_not()
    if why:
        return why
    try:
        found = subprocess.run(["podman", "--log-level", "error", "image", "exists", isolate.IMAGE],
                               capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as err:
        return f"podman is not usable ({type(err).__name__})"
    return None if found.returncode == 0 else "the pinned lab image is not pulled"


def shell_step(command: str) -> dict[str, Any]:
    return {"tool": "bash", "args": {"description": "run a command", "command": command}}


def hook_limit(config: dict[str, Any] | None) -> int:
    """Seconds the shim gives the bridge: its default, plus the gate's own model calls and checks when on."""
    limit = trace.SHIM_LIMIT_S
    config = config or {}
    triage, verify = config.get("triage") or {}, config.get("verify") or {}
    if triage.get("on"):
        limit += int(triage.get("timeout_s") or 60)
    if verify.get("main") or verify.get("subagents"):
        limit += int(verify.get("timeout_s") or 120) + int(verify.get("check_timeout_s") or 300)
    return limit


def run(spec: fleet_spec.RunSpec) -> fleet_spec.RunResult:
    guarded = spec.gate_config is not None
    env: dict[str, str] = {}
    config_file = None
    hooks = None
    if guarded:
        text = spec.config_file.read_text(encoding="utf-8").replace("{model_base}", MODEL_BASE)
        spec.config_file.write_text(text, encoding="utf-8")
        json.loads(text)  # loud: a config the gate cannot read must not start a run
        config_file = spec.config_file
        limit = hook_limit(spec.gate_config)
        env = {**spec.env, "PATTON_HOOK_LIMIT": str(limit)}
        hooks = trace.guarded_hooks(limit + 15)
    cfg = Path(tempfile.mkdtemp(prefix="dsh-cfg-", dir=spec.home.parent))
    try:
        trace.prepare(spec.work, spec.state, spec.home, cfg, spec.model, hooks)
        done = trace.launch(f"patton-fleet-dsh-{uuid.uuid4().hex[:10]}", spec.work, spec.state, spec.home, cfg,
                            spec.model_port, spec.mission, spec.timeout_s, config_file, env)
    finally:
        shutil.rmtree(cfg, ignore_errors=True)
    code = done["code"]
    return fleet_spec.RunResult(code, code is None or code in (124, 137), trace.final_text(done["stdout"]),
                                done["seconds"], done["stdout"][-4000:], done["stderr"][-4000:])
