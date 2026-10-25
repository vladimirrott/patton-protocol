"""What the fleet runner asks of a harness port, and what it gets back.

Each port is a module `patton_lab.fleet.ports.<package>` (the harness's own files stay in
`patton_lab/harness/<name>/`, whose hyphenated names cannot be imported) with:

- `VERSION: str`, the harness version the port pins;
- `available() -> str | None`: None when the harness can run on this machine, else why not (a missing image, a
  missing download);
- `shell_step(command: str) -> dict`: a FakeChat step (`{"tool": name, "args": {...}}`) that makes this harness run
  one shell command in /work, for the $0 trace;
- `run(spec: RunSpec) -> RunResult`: one headless run in a disposable podman container with no network of its own;
- `STOP: bool`: whether the port's stop hook can refuse a stop through the bridge;
- `CONTEXT: bool`: whether the gate's reminder text (`additionalContext`) reaches the model's next request.
- optional `WIRE = "anthropic"` for a harness that speaks Anthropic Messages to the recorder (Claude Code); the
  default is OpenAI chat completions.
- optional `STAMP_FILES`: further files the port runs (helpers in another harness's directory, a Containerfile),
  so the trace stamp covers them too.

What `run` must do:

- mount `spec.work` at /work (the agent's project, its working directory), `spec.state` at /patton-state, and,
  when `spec.gate_config` is not None, the gate's config file `spec.config_file` read-only at
  /patton-config/config.json;
- reach the host's recorder at 127.0.0.1:`spec.model_port` through the port's relay, and point the harness's
  OpenAI-compatible provider at it with the model name `spec.model`;
- with `spec.gate_config` None, configure no hook at all (the unguarded arm); otherwise run the gate through
  `shim/patton-hook.sh` (command-hook harnesses) or the port's in-process extension, with PATTON_GATE_ROOT=/work,
  PATTON_GATE_STATE=/patton-state, PATTON_GATE_CONFIG=/patton-config/config.json, every variable in `spec.env`
  (PATTON_GATE_MISSION among them), and the port's stop hook where the harness has one;
- before writing the config file, replace "{model_base}" in it with the base URL that reaches the recorder from
  inside the container (the gate's analyst calls go to "{model_base}/t1/v1");
- turn off the harness's own approval prompts and sandbox, so the gate alone decides, as each port's trace does;
- give the agent `spec.mission` as its one prompt, end after `spec.timeout_s` seconds, and remove its container;
- leave the host user's name and home out of everything the run writes into `spec.work` and `spec.state`.

For the P1 suite /work is a git repository the runner made on the host, and the gate may run the fixture's check
(`python3 -m unittest -q`) inside the container at the stop, so `git` and `python3` must be on PATH there.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# harness name -> its module under patton_lab.fleet.ports
PACKAGES = {"pi": "pi", "omp": "omp", "opencode": "opencode", "dsh": "dsh", "gemini-cli": "gemini_cli",
            "cline": "cline", "open-interpreter": "open_interpreter", "goose": "goose", "claude-code": "claude_code"}


@dataclass
class RunSpec:
    run_id: str
    harness: str
    arm: str
    work: Path
    state: Path
    home: Path
    mission: str
    model_port: int
    model: str
    gate_config: dict[str, Any] | None
    config_file: Path
    timeout_s: int = 600
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class RunResult:
    rc: int | None
    timed_out: bool
    final_text: str
    wall_s: float
    stdout_tail: str = ""
    stderr_tail: str = ""


def port(harness: str):
    """The harness's run module, or None when its port has none yet. A port that fails to import raises."""
    name = f"patton_lab.fleet.ports.{PACKAGES[harness]}"
    if importlib.util.find_spec(name) is None:
        return None
    return importlib.import_module(name)
