"""One fleet run of a Pi-family harness (Pi, oh-my-pi): the real harness, headless in podman, for the fleet runner.

The container has no network (--network=none). Its one way out is the trace kit's unix-socket link: `fwd.py`
inside listens on 127.0.0.1:8099, and the host end connects each stream only to the run's recorder on
127.0.0.1:`spec.model_port`. So the harness's provider is http://127.0.0.1:8099/v1 and the gate's analyst calls,
made from the bridge inside the same container, go to {MODEL_BASE}/t1/v1, the recorder's analyst route. The socket
lives in a short temp directory (a socket path must stay under 108 bytes) and is removed after the run.

Mounts: `spec.work` at /work, `spec.state` at /patton-state, `spec.home` at /home/user, and, only when the arm has
a gate, the config file read-only at /patton-config/config.json and the code the bridge needs (lab/patton_lab at
/repo/lab/patton_lab, plugins at /repo/plugins), all read-only, plus the harness's own install, read-only. The rest
of lab/harness_cache, where every run's scratch lives, is never mounted. The unguarded arm gets no extension, no
gate code and no PATTON_GATE_* variable.

Every container is named and labelled `patton-fleet=<harness>`; a run that outlives its host timeout is removed by
its own name, never by a pattern. The final text is the last assistant message in the harness's JSON event stream
(`--mode json`, one event per line: `message_end` with role "assistant").
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from patton_lab.fleet import spec as fleet_spec
from patton_lab.harness.pi import tracekit as kit

MODEL_BASE = f"http://127.0.0.1:{kit.PORT}"
CONFIG = "/patton-config/config.json"
STATE = "/patton-state"
LABEL = "patton-fleet"
CODE = kit.LAB / "patton_lab"
PLUGINS = kit.REPO / "plugins"
# The trace image plus git (Containerfile.fleet): P1 makes /work a git repository and the Verifier reads it.
IMAGE = "localhost/patton-fleet-pi:1"


def prepare_config(run_spec: fleet_spec.RunSpec) -> bool:
    """Write the gate's config with {model_base} filled in. False when the arm has no gate."""
    if run_spec.gate_config is None:
        return False
    path = run_spec.config_file
    text = path.read_text(encoding="utf-8") if path.is_file() else json.dumps(run_spec.gate_config)
    path.write_text(text.replace("{model_base}", MODEL_BASE), encoding="utf-8")
    return True


def image_problem() -> str | None:
    """None when the fleet image is built here, else how to build it."""
    if shutil.which("podman") is None:
        return "podman is not installed"
    done = subprocess.run(["podman", "--log-level", "error", "image", "exists", IMAGE], capture_output=True,
                          timeout=60, env=kit.podman_env())
    if done.returncode != 0:
        return (f"the image {IMAGE} is not built; run `podman build --label patton-fleet=image -t {IMAGE} "
                "-f Containerfile.fleet .` in lab/patton_lab/harness/pi")
    return None


def final_text(stdout: str) -> str:
    """The text of the last assistant message in a Pi-family JSON event stream, or "" when there is none."""
    final = ""
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        message = event.get("message") if isinstance(event, dict) and event.get("type") == "message_end" else None
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if isinstance(content, str):
            text = content
        else:
            text = "\n".join(str(part.get("text") or "") for part in content or []
                             if isinstance(part, dict) and part.get("type") == "text")
        if text.strip():
            final = text
    return final


def container_argv(run_spec: fleet_spec.RunSpec, sock: Path, name: str, harness: str,
                   mounts: list[tuple[Path | str, str, str]], env: dict[str, str], harness_argv: list[str],
                   gated: bool) -> list[str]:
    argv = ["podman", "--log-level", "error", "run", "--rm", "--pull=never", "--userns=keep-id", "--name", name,
            "--label", f"{LABEL}={harness}", "--network=none", "--cap-drop=all", "--security-opt=no-new-privileges",
            "--read-only", "--tmpfs", "/tmp:size=256m,exec", "--memory=4g", "--pids-limit=512",
            "-v", f"{run_spec.work}:/work:rw", "-v", f"{run_spec.state}:{STATE}:rw",
            "-v", f"{run_spec.home}:/home/user:rw", "-v", f"{sock}:/sock:rw", "-v", f"{kit.FWD}:/kit/fwd.py:ro"]
    if gated:
        argv += ["-v", f"{run_spec.config_file}:{CONFIG}:ro", "-v", f"{CODE}:/repo/lab/patton_lab:ro",
                 "-v", f"{PLUGINS}:/repo/plugins:ro"]
    for host, dest, mode in mounts:
        argv += ["-v", f"{host}:{dest}:{mode}"]
    base = {"HOME": "/home/user", "XDG_CONFIG_HOME": "/home/user/.config", "PYTHONDONTWRITEBYTECODE": "1",
            "LANG": "C.UTF-8", "TERM": "dumb", "NO_COLOR": "1", **env}
    if gated:
        base.update({"PATTON_GATE_ROOT": "/work", "PATTON_GATE_STATE": STATE, "PATTON_GATE_CONFIG": CONFIG,
                     **run_spec.env})
    for key, value in base.items():
        argv += ["-e", f"{key}={value}"]
    return argv + ["-w", "/work", IMAGE] + kit.entry(harness_argv, run_spec.timeout_s)


def _text(value) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else (value or "")


def run(run_spec: fleet_spec.RunSpec, harness: str, argv_for) -> fleet_spec.RunResult:
    """One run. `argv_for(sock, name, gated)` gives the container command for this harness."""
    gated = prepare_config(run_spec)
    sock = Path(tempfile.mkdtemp(prefix=f"pf-{harness}-"))
    name = f"{LABEL}-{harness}-{uuid.uuid4().hex[:12]}"
    link = kit.SocketLink(sock / "fake.sock", run_spec.model_port)
    started = time.time()
    rc, stdout, stderr, timed_out = None, "", "", False
    try:
        argv = argv_for(sock, name, gated)
        try:
            done = subprocess.run(argv, capture_output=True, text=True, timeout=run_spec.timeout_s + 60,
                                  stdin=subprocess.DEVNULL, env=kit.podman_env())
            rc, stdout, stderr = done.returncode, done.stdout, done.stderr
            timed_out = rc == 124  # `timeout` inside the container ended the harness
        except subprocess.TimeoutExpired as err:
            stdout, stderr, timed_out = _text(err.stdout), "host timeout\n" + _text(err.stderr), True
            subprocess.run(["podman", "--log-level", "error", "rm", "-f", name], capture_output=True, text=True,
                           timeout=60, env=kit.podman_env())
    finally:
        link.close()
        shutil.rmtree(sock, ignore_errors=True)
    return fleet_spec.RunResult(rc, timed_out, final_text(stdout), round(time.time() - started, 1),
                                stdout[-4000:], stderr[-4000:])
