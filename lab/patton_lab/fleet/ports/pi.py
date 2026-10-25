"""Pi 1.4.0 (earendil-works/pi) for the fleet runner: one headless `pi --mode json` run in podman.

The harness, its Node and the container kit are the ones the $0 trace pins (lab/patton_lab/harness/pi/, RESULT.md);
the run itself is `harness/pi/fleetkit.py`. The provider is an `openai-completions` entry in a fresh
`~/.pi/agent/models.json` pointing at the recorder through the relay, with the run's model as its one model, and
`--offline` keeps Pi off its model catalog. Pi has no approval prompts or sandbox of its own, so the gate alone
decides. With a gate, the extension `harness/pi/gate.ts` guards every tool call and refuses a stop at
`agent_before_settle` when the gate does (the bridge caps those refusals); without one, no extension is loaded.
"""

from __future__ import annotations

import json
from pathlib import Path

from patton_lab.fleet import spec as fleet_spec
from patton_lab.harness.pi import fleetkit
from patton_lab.harness.pi import tracekit as kit

VERSION = "1.4.0"
NODE = kit.CACHE / "pi" / "node"
PKG = kit.CACHE / "pi" / "pkg"
EXT = "/repo/lab/patton_lab/harness/pi/gate.ts"
PROVIDER = "patton-fleet"
STOP = True       # agent_before_settle refuses a stop through the bridge (trace S7g, S7h)
CONTEXT = True    # a reminder is appended to the tool result at tool_result (trace S8)


def available() -> str | None:
    if not (NODE / "bin" / "node").is_file() or not (PKG / "node_modules" / ".bin" / "pi").exists():
        return "lab/harness_cache/pi is missing; see lab/patton_lab/harness/pi/RESULT.md for the download"
    return fleetkit.image_problem()


def shell_step(command: str) -> dict:
    return {"tool": "bash", "args": {"command": command}}


def prepare_home(run_spec: fleet_spec.RunSpec) -> None:
    agent = run_spec.home / ".pi" / "agent"
    agent.mkdir(parents=True, exist_ok=True)
    models = {"providers": {PROVIDER: {
        "baseUrl": f"{fleetkit.MODEL_BASE}/v1", "api": "openai-completions", "apiKey": "dummy-not-a-key",
        "models": [{"id": run_spec.model, "name": run_spec.model, "contextWindow": 131072, "maxTokens": 16384}]}}}
    (agent / "models.json").write_text(json.dumps(models, indent=1), encoding="utf-8")


def harness_argv(run_spec: fleet_spec.RunSpec, gated: bool) -> list[str]:
    argv = ["/pkg/node_modules/.bin/pi", "--mode", "json", "--offline", "-na", "--no-context-files",
            "--session-dir", "/home/user/sessions", "--provider", PROVIDER, "--model", run_spec.model]
    if gated:
        argv += ["-e", EXT]
    return argv + [run_spec.mission]


def container_argv(run_spec: fleet_spec.RunSpec, sock: Path, name: str, gated: bool | None = None) -> list[str]:
    gated = run_spec.gate_config is not None if gated is None else gated
    env = {"PI_CODING_AGENT_DIR": "/home/user/.pi/agent", "PI_OFFLINE": "1",
           "PATH": "/opt/node/bin:/usr/local/bin:/usr/bin:/bin"}
    return fleetkit.container_argv(run_spec, sock, name, "pi", [(NODE, "/opt/node", "ro"), (PKG, "/pkg", "ro")], env,
                                   harness_argv(run_spec, gated), gated)


def run(run_spec: fleet_spec.RunSpec) -> fleet_spec.RunResult:
    prepare_home(run_spec)
    return fleetkit.run(run_spec, "pi", lambda sock, name, gated: container_argv(run_spec, sock, name, gated))
