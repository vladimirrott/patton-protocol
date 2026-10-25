"""Gemini CLI v0.65.0 for the fleet runner (see ../spec.py for the contract, harness/gemini-cli/ for the traces).

The CLI speaks only the Gemini wire format, so each run starts `harness/gemini-cli/proxy.py` (GeminiProxy) on the
host with the recorder's chat-completions route as its upstream (`http://127.0.0.1:<model_port>/v1`). The container
has `--network=none` and two relays (`harness/gemini-cli/relay.py`), each a unix socket from a short temporary
directory on the host:

- 127.0.0.1:18080 inside goes to the proxy; the CLI's GOOGLE_GEMINI_BASE_URL points there;
- 127.0.0.1:18081 inside goes straight to the recorder, so "{model_base}" in the gate's config becomes
  http://127.0.0.1:18081 and the gate's analyst and Verifier calls land on the recorder's /t1/v1 route.

Everything else is `harness/gemini-cli/trace.py`'s recipe: the pinned python image with the pinned Node.js and CLI
package mounted read-only, `--approval-mode yolo`, GEMINI_SANDBOX=false and GEMINI_CLI_TRUST_WORKSPACE=true (the
CLI's own sandbox off, the workspace trusted, so yolo stays yolo and the gate alone decides), the API-key auth type
named in ~/.gemini/settings.json with a dummy key, usage statistics and auto-update off. The run's home is
`spec.home` at /home/user, where the settings file is written fresh.

With a gate, settings.json carries three user-scope hooks, the gate code is mounted read-only at /patton
(lab/patton_lab and plugins), and the PATTON_GATE_* variables and `spec.env` are set:

- BeforeTool on run_shell_command, write_file and replace: `sh /patton/lab/patton_lab/harness/shim/patton-hook.sh
  gemini_cli`, the fail-closed shim, since the CLI lets a call run when its hook exits 1 or times out (RESULT.md);
- AfterTool on the same tools: `harness/gemini-cli/context_hook.py`, which hands the model the gate's reminder
  for an allowed call (CONTEXT is True); a refused call's reminder rides on its reason;
- AfterAgent: the shim in stop mode, so the bridge can refuse a stop (STOP is True) and caps the refusals; its
  limit is STOP_LIMIT_S, as the stop runs the project's check and may ask the Verifier.

Without a gate there is no hook, no gate code, no config and no PATTON_* variable. `git` and `python3` are on PATH
inside: python3 is the image's, git is the pinned Debian package the OpenCode port unpacks (its sha256 checked)
mounted read-only at /opt/git/usr. The final text is the last assistant message in `--output-format
stream-json`; the exit code is reported, never used to judge. Containers are named `pf-gemini-cli-<hex>` and
labelled `patton-fleet=gemini-cli`; a run that outlives the host timeout removes its own container by name.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import tempfile
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

from .. import spec as fleet_spec
from . import opencode as _opencode

VERSION = "0.65.0"
STOP = True      # AfterAgent deny through the bridge (harness/gemini-cli/trace_proxy.py S7g, S7h)
CONTEXT = True   # AfterTool additionalContext (trace_proxy.py S8)

LAB = Path(__file__).resolve().parents[3]
REPO = LAB.parent
HARNESS = LAB / "patton_lab" / "harness" / "gemini-cli"
CACHE = LAB / "harness_cache" / "gemini-cli"
PKG, NODE = CACHE / "pkg", CACHE / "node"
IMAGE = "docker.io/library/python@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f"
CLI_PORT, MODEL_PORT = 18080, 18081
MODEL_BASE = f"http://127.0.0.1:{MODEL_PORT}"
CLI_MODEL = "gemini-2.5-flash"  # the name the CLI is started with; the proxy sends spec.model upstream
LABEL = "patton-fleet=gemini-cli"
CONFIG_IN = "/patton-config/config.json"
STATE_IN = "/patton-state"
SHIM = "sh /patton/lab/patton_lab/harness/shim/patton-hook.sh gemini_cli"
CONTEXT_HOOK = "python3 -I /patton/lab/patton_lab/harness/gemini-cli/context_hook.py"
MATCHER = "^(run_shell_command|write_file|replace)$"
TOOL_TIMEOUT_MS = 60_000          # over the shim's own 45 s limit, so the shim answers first
STOP_LIMIT_S = 330                # the shim's limit for a stop: the Verifier's 300 s plus the check
STOP_TIMEOUT_MS = (STOP_LIMIT_S + 30) * 1000
PATH_IN = "/opt/node/bin:/opt/git/usr/bin:/usr/local/bin:/usr/bin:/bin"


def _load(name: str, path: Path):
    """A module from the hyphenated harness directory, by path (it cannot be imported by name)."""
    module_spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


proxy_mod = _load("patton_fleet_gemini_proxy", HARNESS / "proxy.py")
relay_mod = _load("patton_fleet_gemini_relay", HARNESS / "relay.py")


@lru_cache(maxsize=1)
def _image_ok() -> bool:
    if shutil.which("podman") is None:
        return False
    return subprocess.run(["podman", "--log-level", "error", "image", "exists", IMAGE], capture_output=True,
                          timeout=60, env=_opencode.podrun._podman_env()).returncode == 0


def available() -> str | None:
    if not (PKG / "bundle" / "gemini.js").is_file() or not (NODE / "bin" / "node").is_file():
        return "lab/harness_cache/gemini-cli has no CLI package or Node.js; see harness/gemini-cli/RESULT.md"
    if not _opencode._git_ok() or not (_opencode.GIT_ROOT / "bin" / "git").is_file():
        return ("the pinned git package is not unpacked in lab/harness_cache/opencode/git "
                "(dpkg-deb -x dl/git_2.47.3-0+deb13u1_amd64.deb root)")
    if not _image_ok():
        return "podman or the pinned python image is missing"
    return None


def shell_step(command: str) -> dict[str, Any]:
    """A FakeChat step the proxy turns into the CLI's own shell call (a run_shell_command functionCall)."""
    return {"tool": "run_shell_command", "args": {"command": command}}


def settings(gated: bool) -> dict[str, Any]:
    """~/.gemini/settings.json for one run."""
    out: dict[str, Any] = {"security": {"auth": {"selectedType": "gemini-api-key"}},
                           "privacy": {"usageStatisticsEnabled": False},
                           "general": {"enableAutoUpdate": False, "enableAutoUpdateNotification": False}}
    if gated:
        out["hooks"] = {
            "BeforeTool": [{"matcher": MATCHER, "hooks": [{"type": "command", "name": "patton-gate", "command": SHIM,
                                                           "timeout": TOOL_TIMEOUT_MS}]}],
            "AfterTool": [{"matcher": MATCHER, "hooks": [{"type": "command", "name": "patton-context",
                                                          "command": CONTEXT_HOOK, "timeout": 30_000}]}],
            "AfterAgent": [{"hooks": [{"type": "command", "name": "patton-stop",
                                       "command": f"PATTON_HOOK_LIMIT={STOP_LIMIT_S} {SHIM} stop",
                                       "timeout": STOP_TIMEOUT_MS}]}]}
    return out


def prepare(run_spec: fleet_spec.RunSpec) -> bool:
    """Write settings.json and, with a gate, its config with {model_base} filled in. True when gated."""
    gated = run_spec.gate_config is not None
    gemini = run_spec.home / ".gemini"
    gemini.mkdir(parents=True, exist_ok=True)
    (gemini / "settings.json").write_text(json.dumps(settings(gated), indent=2), encoding="utf-8")
    if gated:
        path = run_spec.config_file
        text = path.read_text(encoding="utf-8") if path.is_file() else json.dumps(run_spec.gate_config)
        path.write_text(text.replace("{model_base}", MODEL_BASE), encoding="utf-8")
    return gated


def container_argv(run_spec: fleet_spec.RunSpec, sock: Path, name: str, gated: bool) -> list[str]:
    inner = (f"python3 -I /kit/relay.py listen {CLI_PORT} /sock/cli.sock >/dev/null & "
             f"python3 -I /kit/relay.py listen {MODEL_PORT} /sock/model.sock >/dev/null & "
             f"for p in {CLI_PORT} {MODEL_PORT}; do for i in $(seq 50); do python3 -c "
             f"\"import socket,sys;socket.create_connection(('127.0.0.1',int(sys.argv[1])))\" $p 2>/dev/null && break; "
             f"sleep 0.1; done; done; "
             f"exec timeout -k 5 {int(run_spec.timeout_s)} node /opt/gemini/bundle/gemini.js -p \"$PATTON_RUN_PROMPT\" "
             f"-m {CLI_MODEL} --output-format stream-json --approval-mode yolo")
    env = {"HOME": "/home/user", "PATH": PATH_IN, "GOOGLE_GEMINI_BASE_URL": f"http://127.0.0.1:{CLI_PORT}",
           "GEMINI_API_KEY": "dummy-not-a-key", "GEMINI_SANDBOX": "false", "GEMINI_CLI_TRUST_WORKSPACE": "true",
           "NO_COLOR": "1", "TERM": "dumb", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
           "GIT_EXEC_PATH": "/opt/git/usr/lib/git-core", "GIT_TEMPLATE_DIR": "/opt/git/usr/share/git-core/templates",
           "PATTON_RUN_PROMPT": run_spec.mission}
    mounts = [(run_spec.work, "/work", "rw"), (run_spec.state, STATE_IN, "rw"), (run_spec.home, "/home/user", "rw"),
              (sock, "/sock", "rw"), (HARNESS / "relay.py", "/kit/relay.py", "ro"), (PKG, "/opt/gemini", "ro"),
              (NODE, "/opt/node", "ro"), (_opencode.GIT_ROOT, "/opt/git/usr", "ro")]
    if gated:
        env.update({"PATTON_GATE_ROOT": "/work", "PATTON_GATE_STATE": STATE_IN, "PATTON_GATE_CONFIG": CONFIG_IN,
                    **run_spec.env})
        mounts += [(run_spec.config_file, CONFIG_IN, "ro"), (LAB / "patton_lab", "/patton/lab/patton_lab", "ro"),
                   (REPO / "plugins", "/patton/plugins", "ro")]
    argv = ["podman", "--log-level", "error", "run", "--rm", "--pull=never", "--name", name, "--label", LABEL,
            "--userns=keep-id", "--network=none", "--cap-drop=all", "--security-opt=no-new-privileges", "--read-only",
            "--tmpfs", "/tmp:size=256m", "--memory=3g", "--pids-limit=512"]
    for key, value in env.items():
        argv += ["-e", f"{key}={value}"]
    for host, inside, mode in mounts:
        argv += ["-v", f"{host}:{inside}:{mode}"]
    return argv + ["-w", "/work", IMAGE, "bash", "-c", inner]


def final_text(stdout: str) -> str:
    """The last assistant message in the CLI's stream-json output: its consecutive `message` deltas joined."""
    final, current, open_ = "", [], False
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "message" and event.get("role") == "assistant":
            if not open_:
                current, open_ = [], True
            current.append(str(event.get("content") or ""))
            continue
        if open_:
            open_ = False
            if "".join(current).strip():
                final = "".join(current)
    if open_ and "".join(current).strip():
        final = "".join(current)
    return final


def _text(value: Any) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else (value or "")


def run(run_spec: fleet_spec.RunSpec) -> fleet_spec.RunResult:
    gated = prepare(run_spec)
    sock = Path(tempfile.mkdtemp(prefix="pf-gem-"))  # short: a unix socket path must stay under 108 bytes
    sock.chmod(0o755)
    name = f"pf-gemini-cli-{uuid.uuid4().hex[:12]}"
    proxy = proxy_mod.GeminiProxy(f"http://127.0.0.1:{run_spec.model_port}/v1", run_spec.model,
                                  timeout=max(60.0, float(run_spec.timeout_s)))
    relays = []
    started = time.time()
    rc, stdout, stderr, timed_out = None, "", "", False
    try:
        relays.append(relay_mod.UnixRelay(str(sock / "cli.sock"), proxy.start()))
        relays.append(relay_mod.UnixRelay(str(sock / "model.sock"), run_spec.model_port))
        for relay in relays:
            relay.start()
        try:
            done = subprocess.run(container_argv(run_spec, sock, name, gated), capture_output=True, text=True,
                                  errors="replace", timeout=run_spec.timeout_s + 60, stdin=subprocess.DEVNULL,
                                  env=_opencode.podrun._podman_env())
            rc, stdout, stderr = done.returncode, done.stdout, done.stderr
            timed_out = rc in (124, 137)  # timeout(1) stopped the CLI, or had to kill it
        except subprocess.TimeoutExpired as err:
            subprocess.run(["podman", "--log-level", "error", "rm", "-f", "-t", "0", name], capture_output=True,
                           timeout=60, env=_opencode.podrun._podman_env())
            rc, stdout, stderr, timed_out = None, _text(err.stdout), _text(err.stderr) + "\nhost timeout", True
    finally:
        for relay in relays:
            relay.stop()
        proxy.stop()
        shutil.rmtree(sock, ignore_errors=True)
    if proxy.untranslated:
        stderr += f"\ngemini proxy did not translate: {', '.join(proxy.untranslated)}"
    return fleet_spec.RunResult(rc, timed_out, final_text(stdout), round(time.time() - started, 1),
                                stdout[-4000:], stderr[-4000:])
