"""The fleet port of goose 1.56.0: one headless `goose run --output-format stream-json` per spec.

The container recipe is the harness trace's (`patton_lab/harness/goose/trace.py` `prepare` and `launch`): the pinned
release binary mounted read-only, `--network none` with a Unix-socket relay to the recorder on host
127.0.0.1:`spec.model_port`, goose's openai provider pointed at it (OPENAI_HOST, GOOSE_MODEL=`spec.model`, a dummy
key, no keyring), GOOSE_MODE=auto so goose asks no approvals, and git from `harness/gitmount.py` so the gate can run
the fixture's checks at a stop. `--max-turns` is set high enough that `spec.timeout_s` is what ends a long run.

A guarded arm mounts the gate plugin at ~/.agents/plugins/patton-gate with the shipped hooks: PreToolUse through
`shim/patton-hook.sh goose` with `on_failure: block` (either one alone blocks a crash or a hang), Stop through
`... goose stop`. The bridge caps refused stops at 4, under goose's own GOOSE_STOP_HOOK_BLOCK_CAP of 8. goose has no
channel for the gate's reminder on an allowed call, so CONTEXT is False. The shim's time limit is raised above the
gate's own analyst and Verifier timeouts when the config turns them on. The unguarded arm mounts no plugin. The
final text is the last assistant text in the stream-json events.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from typing import Any

from patton_lab import isolate
from patton_lab.harness import gitmount
from patton_lab.harness.goose import trace

from .. import spec as fleet_spec

VERSION = trace.VERSION
STOP = True  # the Stop hook refuses through the bridge, and the reason reaches the model (trace S7g)
CONTEXT = False  # goose reads only decision and reason from a hook (trace S8)
MODEL_BASE = f"http://127.0.0.1:{trace.PORT}"
MAX_TURNS = 200


def available() -> str | None:
    if not trace.BINARY.is_file():
        return "no goose binary in lab/harness_cache/goose/bin (run python3 -m patton_lab.harness.goose.trace once)"
    why = gitmount.why_not()
    if why:
        return why
    try:
        found = subprocess.run(["podman", "--log-level", "error", "image", "exists", isolate.IMAGE],
                               capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as err:
        return f"podman is not usable ({type(err).__name__})"
    return None if found.returncode == 0 else "the pinned lab image is not pulled"


def shell_step(command: str) -> dict[str, Any]:
    return {"tool": "shell", "args": {"command": command}}


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
    env: dict[str, str] = {}
    config_file = plugin = hooks = None
    if spec.gate_config is not None:
        text = spec.config_file.read_text(encoding="utf-8").replace("{model_base}", MODEL_BASE)
        spec.config_file.write_text(text, encoding="utf-8")
        json.loads(text)  # loud: a config the gate cannot read must not start a run
        config_file = spec.config_file
        limit = hook_limit(spec.gate_config)
        env = {**spec.env, "PATTON_HOOK_LIMIT": str(limit)}
        hooks = trace.guarded_hooks(limit + 15)
        plugin = spec.home.parent / f"goose-plugin-{uuid.uuid4().hex[:8]}"
    trace.prepare(spec.work, spec.state, spec.home, plugin, hooks)
    done = trace.launch(f"patton-fleet-goose-{uuid.uuid4().hex[:10]}", spec.work, spec.state, spec.home, plugin,
                        spec.model_port, spec.mission, spec.timeout_s, spec.model, config_file, env, MAX_TURNS)
    code = done["code"]
    return fleet_spec.RunResult(code, code is None or code in (124, 137), trace.final_text(done["stdout"]),
                                done["seconds"], done["stdout"][-4000:], done["stderr"][-4000:])
