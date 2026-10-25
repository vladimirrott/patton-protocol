"""oh-my-pi 18.12.6 (can1357/oh-my-pi) for the fleet runner: one headless `omp -p --mode json` run on Bun in podman.

The harness, its Bun and the container kit are the ones the $0 trace pins (lab/patton_lab/harness/omp/, RESULT.md);
the run itself is `harness/pi/fleetkit.py`, shared with Pi. The provider is an `openai-completions` entry in a fresh
`~/.omp/agent/models.yml` pointing at the recorder through the relay, with the run's model as its one model and a
zero cost table (omp requires one; the recorder does the charging). `--approval-mode yolo` says outright what omp's
default already is, so the gate alone decides, and ambient extensions, skills, rules, LSP and titles are off.
omp's edit tool is pinned to its `replace` form (PI_EDIT_VARIANT=replace), the form the trace ran end to end; the
default hashline form is mapped by the dialect but untested live. With a gate, `harness/omp/gate.ts` guards every
tool call, returns the gate's reminders as `additionalContext`, and refuses a stop at `session_stop` when the gate
does (the bridge caps those refusals); without one, no extension is loaded.
"""

from __future__ import annotations

from pathlib import Path

from patton_lab.fleet import spec as fleet_spec
from patton_lab.harness.pi import fleetkit
from patton_lab.harness.pi import tracekit as kit

VERSION = "18.12.6"
BUN = kit.CACHE / "omp" / "bun" / "bun-linux-x64"
PKG = kit.CACHE / "omp" / "pkg"
EXT = "/repo/lab/patton_lab/harness/omp/gate.ts"
PROVIDER = "patton-fleet"
STOP = True       # session_stop {decision: "block"} through the bridge (trace S7g, S7h)
CONTEXT = True    # a reminder goes back as the tool call's additionalContext (trace S8)


def available() -> str | None:
    if not (BUN / "bun").is_file() or not (PKG / "node_modules" / "@oh-my-pi" / "pi-coding-agent").is_dir():
        return "lab/harness_cache/omp is missing; see lab/patton_lab/harness/omp/RESULT.md for the download"
    return fleetkit.image_problem()


def shell_step(command: str) -> dict:
    return {"tool": "bash", "args": {"command": command}}


def models_yml(model: str) -> str:
    return (f"providers:\n  {PROVIDER}:\n    baseUrl: {fleetkit.MODEL_BASE}/v1\n    apiKey: PATTON_FAKE_KEY\n"
            f"    api: openai-completions\n    models:\n      - id: {model}\n        name: {model}\n"
            "        reasoning: false\n        input: [text]\n        contextWindow: 131072\n        maxTokens: 16384\n"
            "        cost:\n          input: 0\n          output: 0\n          cacheRead: 0\n          cacheWrite: 0\n")


def prepare_home(run_spec: fleet_spec.RunSpec) -> None:
    agent = run_spec.home / ".omp" / "agent"
    agent.mkdir(parents=True, exist_ok=True)
    (agent / "models.yml").write_text(models_yml(run_spec.model), encoding="utf-8")


def harness_argv(run_spec: fleet_spec.RunSpec, gated: bool) -> list[str]:
    argv = ["/opt/bun/bun", "/pkg/node_modules/@oh-my-pi/pi-coding-agent/dist/cli.js", "-p", "--mode", "json",
            "--no-extensions", "--no-skills", "--no-rules", "--no-lsp", "--no-title", "--no-pty",
            "--approval-mode", "yolo", "--session-dir", "/home/user/sessions",
            "--model", f"{PROVIDER}/{run_spec.model}"]
    if gated:
        argv += ["-e", EXT]
    return argv + [run_spec.mission]


def container_argv(run_spec: fleet_spec.RunSpec, sock: Path, name: str, gated: bool | None = None) -> list[str]:
    gated = run_spec.gate_config is not None if gated is None else gated
    env = {"PI_CODING_AGENT_DIR": "/home/user/.omp/agent", "PATTON_FAKE_KEY": "dummy-not-a-key",
           "PI_EDIT_VARIANT": "replace", "PI_NO_TITLE": "1", "OTEL_SDK_DISABLED": "true",
           "PATH": "/opt/bun:/usr/local/bin:/usr/bin:/bin"}
    return fleetkit.container_argv(run_spec, sock, name, "omp", [(BUN, "/opt/bun", "ro"), (PKG, "/pkg", "ro")], env,
                                   harness_argv(run_spec, gated), gated)


def run(run_spec: fleet_spec.RunSpec) -> fleet_spec.RunResult:
    prepare_home(run_spec)
    return fleetkit.run(run_spec, "omp", lambda sock, name, gated: container_argv(run_spec, sock, name, gated))
