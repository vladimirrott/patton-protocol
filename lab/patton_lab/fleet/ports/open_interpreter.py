"""The fleet port of Open Interpreter rust-v0.0.56 (a fork of Codex): one headless `interpreter exec` in podman.

The pieces are the trace's (`patton_lab/harness/open-interpreter/trace.py` and `relay.py`): the same pinned package,
image, provider block with `wire_api = "chat"` (the fork speaks Chat Completions to a custom provider, streaming,
which the recorder serves), the one-entry model catalog that turns the `apply_patch` tool on, now with the run's
model as its slug, and the same `--yolo --dangerously-bypass-hook-trust` flags, so the fork's own approvals and
sandbox are off and the gate alone decides.

Layout in the container: /work (rw), /patton-state (rw, PATTON_GATE_STATE), /home/user (rw, the run's home with
~/.openinterpreter), /patton/lab and /patton/plugins (ro, so the shim at /patton/lab/.../shim/patton-hook.sh runs the
bridge and the bridge finds the gate), /patton-config/config.json (ro, only on a gated arm), /opt/oi (ro, the
package), git from `harness/gitmount.py`, and /sock, whose unix socket leads to the recorder: the container has
`--network none`, and `relay.py listen` inside it serves 127.0.0.1:18080. So `{model_base}` is http://127.0.0.1:18080.

Hooks (~/.openinterpreter/hooks.json), only when the arm has a gate: PreToolUse with no matcher and Stop, both the
shim (`open_interpreter` and `open_interpreter stop`). Each hook's fork timeout is above the shim's own limit, so a
hung bridge is refused by the shim before the fork gives up and runs the call, and each shim limit is above the
gate's slowest model call: T1 triage may take 120 s in a tool call, and the Verifier 300 s plus the checks at a
stop (TOOL_LIMIT_S, STOP_LIMIT_S). The final text is the last
`agent_message` item in the `--json` event stream.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from ...harness import gitmount
from .. import spec

HERE = Path(__file__).resolve().parents[2] / "harness" / "open-interpreter"
REPO = HERE.parents[3]
LABEL = "patton-fleet=oi"
SHIM = "/patton/lab/patton_lab/harness/shim/patton-hook.sh"
TOOL_LIMIT_S, TOOL_TIMEOUT_S = 150, 180
STOP_LIMIT_S, STOP_TIMEOUT_S = 420, 450
STOP = True  # the fork's Stop hook refuses a stop through the bridge, which caps it
CONTEXT = True  # PreToolUse `additionalContext` reaches the model's next request (trace S8)


def _trace():
    """The trace module, loaded from its hyphenated directory (its own `relay` import needs the directory on path)."""
    name = "patton_lab_oi_trace"
    if name in sys.modules:
        return sys.modules[name]
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    found = importlib.util.spec_from_file_location(name, HERE / "trace.py")
    module = importlib.util.module_from_spec(found)
    sys.modules[name] = module
    found.loader.exec_module(module)
    return module


T = _trace()
VERSION = T.VERSION


def available() -> str | None:
    if not (T.PKG / "bin" / "interpreter").is_file():
        return f"no Open Interpreter {VERSION} package in lab/harness_cache/open-interpreter/pkg"
    if shutil.which("podman") is None:
        return "podman is not installed"
    probe = subprocess.run(["podman", "--log-level", "error", "image", "exists", T.isolate.IMAGE],
                           capture_output=True, timeout=60)
    if probe.returncode != 0:
        return "the pinned image is missing"
    return gitmount.why_not()


def shell_step(command: str) -> dict:
    return T.exec_step(command)


def hooks(gated: bool) -> dict:
    if not gated:
        return {"hooks": {}}
    return T.hooks_json(f"PATTON_HOOK_LIMIT={TOOL_LIMIT_S} sh {SHIM} open_interpreter",
                        stop=f"PATTON_HOOK_LIMIT={STOP_LIMIT_S} sh {SHIM} open_interpreter stop",
                        timeout=TOOL_TIMEOUT_S, stop_timeout=STOP_TIMEOUT_S)


def model_base() -> str:
    return f"http://127.0.0.1:{T.PORT}"


def prepare(run_spec: spec.RunSpec) -> None:
    """The run's home (provider, catalog, hooks) and, on a gated arm, the gate config with {model_base} filled in."""
    home = run_spec.home / ".openinterpreter"
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(T.config_toml(run_spec.model))
    (home / "catalog.json").write_text(json.dumps(T.catalog(run_spec.model)))
    (home / "hooks.json").write_text(json.dumps(hooks(run_spec.gate_config is not None), indent=2))
    if run_spec.gate_config is not None:
        text = run_spec.config_file.read_text(encoding="utf-8").replace("{model_base}", model_base())
        run_spec.config_file.write_text(text, encoding="utf-8")


def command(run_spec: spec.RunSpec, cname: str, sock_dir: str) -> list[str]:
    inner = (f"python3 -I /patton/lab/patton_lab/harness/open-interpreter/relay.py listen {T.PORT} /sock/fake.sock "
             f">/dev/null & for i in $(seq 50); do python3 -c 'import socket;socket.create_connection("
             f"(\"127.0.0.1\",{T.PORT}))' 2>/dev/null && break; sleep 0.1; done; "
             f"exec timeout -k 5 {run_spec.timeout_s} /opt/oi/bin/interpreter exec --json --yolo "
             f"--skip-git-repo-check --dangerously-bypass-hook-trust \"$PATTON_MISSION_TEXT\"")
    env = {"HOME": "/home/user", "INTERPRETER_HOME": "/home/user/.openinterpreter",
           "SCRIPTED_API_KEY": "dummy-not-a-key", "PYTHONDONTWRITEBYTECODE": "1", "PATTON_MISSION_TEXT":
           run_spec.mission}
    mounts = [(run_spec.work, "/work:rw"), (run_spec.home, "/home/user:rw"), (run_spec.state, "/patton-state:rw"),
              (Path(sock_dir), "/sock:rw"), (T.PKG, "/opt/oi:ro"), (REPO / "lab", "/patton/lab:ro"),
              (REPO / "plugins", "/patton/plugins:ro"), *gitmount.mounts()]
    if run_spec.gate_config is not None:
        env.update({"PATTON_GATE_ROOT": "/work", "PATTON_GATE_STATE": "/patton-state",
                    "PATTON_GATE_CONFIG": "/patton-config/config.json", **run_spec.env})
        mounts.append((run_spec.config_file, "/patton-config/config.json:ro"))
    argv = ["podman", "--log-level", "error", "run", "--rm", "--pull=never", "--name", cname, "--label", LABEL,
            "--userns=keep-id", "--network", "none", "--cap-drop=all", "--security-opt=no-new-privileges",
            "--read-only", "--tmpfs", "/tmp:size=256m", "--memory", "3g", "--pids-limit", "512"]
    for key, value in env.items():
        argv += ["-e", f"{key}={value}"]
    for host, inside in mounts:
        argv += ["-v", f"{host}:{inside}"]
    return argv + ["-w", "/work", T.isolate.IMAGE, "bash", "-c", inner]


def final_text(stdout: str) -> str:
    """The last agent message in the `--json` event stream."""
    text = ""
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        item = event.get("item") if isinstance(event, dict) else None
        if event.get("type") == "item.completed" and isinstance(item, dict) and item.get("type") == "agent_message":
            text = str(item.get("text") or "")
    return text


def run(run_spec: spec.RunSpec) -> spec.RunResult:
    prepare(run_spec)
    sock_dir = tempfile.mkdtemp(prefix="pg-oi-sock-")  # short: AF_UNIX paths stop at 108 bytes
    os.chmod(sock_dir, 0o755)
    relay = T.UnixRelay(os.path.join(sock_dir, "fake.sock"), run_spec.model_port)
    relay.start()
    cname = f"patton-oi-fleet-{os.getpid()}-{int(time.time() * 1000) % 1000000}"
    start = time.monotonic()
    timed_out = False
    try:
        done = subprocess.run(command(run_spec, cname, sock_dir), capture_output=True, text=True, errors="replace",
                              timeout=run_spec.timeout_s + 60, stdin=subprocess.DEVNULL)
        rc, stdout, stderr = done.returncode, done.stdout, done.stderr
        timed_out = rc in (124, 137)
    except subprocess.TimeoutExpired as err:
        subprocess.run(["podman", "--log-level", "error", "rm", "-f", cname], capture_output=True, timeout=60)
        out = err.stdout or ""
        rc, stdout, stderr, timed_out = None, out.decode() if isinstance(out, bytes) else out, "host timeout", True
    finally:
        relay.stop()
        shutil.rmtree(sock_dir, ignore_errors=True)
    return spec.RunResult(rc, timed_out, final_text(stdout), round(time.monotonic() - start, 1),
                          stdout[-4000:], stderr[-4000:])
