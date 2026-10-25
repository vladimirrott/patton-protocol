"""The fleet runner's port of OpenCode v1.19.4 (see ../spec.py for the contract, harness/opencode/ for the trace).

One run is one container from the harness trace's recipe (`harness/opencode/podrun.py`): the pinned python image,
`--network=none`, no capabilities, a read-only root, and the pinned OpenCode binary mounted read-only at
/opt/opencode. The only way out is the trace's relay: the host serves a unix socket that forwards to the recorder
on 127.0.0.1:`spec.model_port`, and inside the container `relay.py` listens on 127.0.0.1:18080. So the harness's
provider is `http://127.0.0.1:18080/v1`, and "{model_base}" in the gate's config becomes `http://127.0.0.1:18080`,
which puts the gate's analyst calls on the recorder's `/t1/v1` route through the same socket.

OpenCode reads its whole configuration from OPENCODE_CONFIG_CONTENT: one OpenAI-compatible provider `lab` with
the run's model as its only model and a dummy key, `"permission": "allow"` so nothing of OpenCode's own asks or
refuses, and, when the arm has a gate, our plugin (`harness/opencode/plugin/patton-gate.js`) by absolute path.
With `spec.gate_config` None the plugin list is empty, no PATTON_* variable is set and no config is mounted.
The model id must not contain `gpt-` without `oss` (OpenCode would then offer `apply_patch` instead of `edit` and
`write`, which the dialect covers but no container run has); `gpt-oss-120b` gets `edit` and `write`.

`git` and `python3` are on PATH inside: python3 is the image's, and git is Debian trixie's own `git` package (the
image is python:3.12-slim on trixie, which has none), pinned by the sha256 in the trixie index, unpacked into
lab/harness_cache/opencode/git/root and mounted read-only at /opt/git/usr, with GIT_EXEC_PATH and GIT_TEMPLATE_DIR
pointing into it. The binary needs only libc, libz and libpcre2, which the image has; the helpers that need curl or
expat (http remotes, http-push) do not work, and need no network anyway. OpenCode's own git snapshots are off
(`"snapshot": false`), so it keeps no shadow repository of /work.

OpenCode has no stop hook (harness/opencode/RESULT.md, S7), so the gate's stop checks never run here (STOP is
False). The plugin carries the gate's reminder on the call's output, or after its refusal (CONTEXT is True,
checked by the harness trace's S8). The final
text is the last `text` part of `opencode run --format json`; the exit code is reported, never used to judge.
Every container is named `pg-fleet-opencode-<hex>` and labelled `patton-fleet=opencode`; a run that outlives the
host timeout removes its own container by that name and nothing else.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

from ...harness.opencode import podrun, trace
from ...harness.opencode.relay import HostRelay
from .. import spec as fleet_spec

VERSION = trace.VERSION
STOP = False
CONTEXT = True
PLUGIN_IN = trace.PLUGIN_IN
LABEL = "patton-fleet=opencode"
CONFIG_IN = "/patton-config/config.json"
MODEL_BASE = f"http://127.0.0.1:{podrun.PORT_IN}"
GIT_DIR = podrun.REPO / "lab" / "harness_cache" / "opencode" / "git"
# https://deb.debian.org/debian/pool/main/g/git/git_2.47.3-0+deb13u1_amd64.deb, its SHA256 in trixie's
# main/binary-amd64/Packages.xz (whose own sha256 matched the trixie InRelease on 2026-10-25)
GIT_DEB = GIT_DIR / "dl" / "git_2.47.3-0+deb13u1_amd64.deb"
GIT_DEB_SHA256 = "3e35662fd5c46add561703e54031a1d8ad9df45811927689f0a51122b13be722"
GIT_ROOT = GIT_DIR / "root" / "usr"
PATH_IN = "/opt/git/usr/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin"


@lru_cache(maxsize=1)
def _binary_ok() -> bool:
    return hashlib.sha256(trace.BINARY.read_bytes()).hexdigest() == trace.BINARY_SHA256


@lru_cache(maxsize=1)
def _git_ok() -> bool:
    return GIT_DEB.is_file() and hashlib.sha256(GIT_DEB.read_bytes()).hexdigest() == GIT_DEB_SHA256


def available() -> str | None:
    why = podrun.available()
    if why:
        return why
    if not trace.BINARY.is_file():
        return f"the pinned OpenCode {VERSION} package is not in lab/harness_cache/opencode (see its RESULT.md)"
    if not _binary_ok():
        return f"lab/harness_cache/opencode holds a binary that is not the pinned OpenCode {VERSION} build"
    if not _git_ok() or not (GIT_ROOT / "bin" / "git").is_file():
        return ("the pinned git package is not unpacked in lab/harness_cache/opencode/git "
                "(dpkg-deb -x dl/git_2.47.3-0+deb13u1_amd64.deb root)")
    return None


def shell_step(command: str) -> dict[str, Any]:
    return trace.bash(command)


def harness_config(run_spec: fleet_spec.RunSpec) -> dict[str, Any]:
    """OPENCODE_CONFIG_CONTENT for this run."""
    return {
        "$schema": "https://opencode.ai/config.json",
        "provider": {"lab": {"npm": "@ai-sdk/openai-compatible", "name": "Lab recorder",
                             "options": {"baseURL": f"{MODEL_BASE}/v1", "apiKey": "dummy"},
                             "models": {run_spec.model: {"name": run_spec.model, "tool_call": True}}}},
        "model": f"lab/{run_spec.model}",
        "plugin": [] if run_spec.gate_config is None else [f"file://{PLUGIN_IN}"],
        "permission": "allow",
        "autoupdate": False,
        "share": "disabled",
        "lsp": False,
        "formatter": False,
        "snapshot": False,
    }


def prepare_config(run_spec: fleet_spec.RunSpec) -> None:
    """Fill "{model_base}" in the gate's config file with the recorder's address as the container sees it."""
    text = run_spec.config_file.read_text(encoding="utf-8")
    run_spec.config_file.write_text(text.replace("{model_base}", MODEL_BASE), encoding="utf-8")


def podman_argv(run_spec: fleet_spec.RunSpec, name: str, relay_dir: Path) -> list[str]:
    env = {"HOME": "/home/user", "PYTHONDONTWRITEBYTECODE": "1", "PATH": PATH_IN,
           "GIT_EXEC_PATH": "/opt/git/usr/lib/git-core", "GIT_TEMPLATE_DIR": "/opt/git/usr/share/git-core/templates",
           "OPENCODE_CONFIG_CONTENT": json.dumps(harness_config(run_spec)), "OPENCODE_DISABLE_AUTOUPDATE": "1",
           "OPENCODE_DISABLE_MODELS_FETCH": "1"}
    mounts = [(run_spec.work, "/work:rw"), (run_spec.state, "/patton-state:rw"), (run_spec.home, "/home/user:rw"),
              (relay_dir, "/relay:ro"), (podrun.REPO / "lab", "/repo/lab:ro"),
              (podrun.REPO / "plugins", "/repo/plugins:ro"), (trace.PACKAGE, "/opt/opencode:ro"),
              (GIT_ROOT, "/opt/git/usr:ro")]
    if run_spec.gate_config is not None:
        env.update({"PATTON_GATE_ROOT": "/work", "PATTON_GATE_STATE": "/patton-state",
                    "PATTON_GATE_CONFIG": CONFIG_IN, **run_spec.env})
        mounts.append((run_spec.config_file, f"{CONFIG_IN}:ro"))
    argv = ["podman", "--log-level", "error", "run", "--rm", "--pull=never", "--name", name, "--label", LABEL,
            "--userns=keep-id", "--network=none", "--cap-drop=all", "--security-opt=no-new-privileges", "--read-only",
            "--tmpfs", "/tmp:size=256m", "--memory=3g", "--pids-limit=1024"]
    for key, value in env.items():
        argv += ["-e", f"{key}={value}"]
    for host, inside in mounts:
        argv += ["-v", f"{host}:{inside}"]
    return argv + ["-w", "/work", podrun.IMAGE, "timeout", "-k", "5", str(run_spec.timeout_s),
                   "python3", "-I", podrun.RELAY_IN, "/relay/chat.sock", str(podrun.PORT_IN), "--",
                   "/opt/opencode/bin/opencode", "run", "--format", "json", "--title", "fleet", "-m",
                   f"lab/{run_spec.model}", "--print-logs", "--log-level", "WARN", run_spec.mission]


def final_text(stdout: str) -> str:
    """The text of the last finished text part in OpenCode's JSON event stream."""
    last = ""
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "text":
            text = (event.get("part") or {}).get("text")
            if isinstance(text, str):
                last = text
    return last


def _text(value: Any) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else (value or "")


def run(run_spec: fleet_spec.RunSpec) -> fleet_spec.RunResult:
    if run_spec.gate_config is not None:
        prepare_config(run_spec)
    name = f"pg-fleet-opencode-{uuid.uuid4().hex[:12]}"
    # A short directory of its own: a unix socket path must stay under 108 bytes.
    relay_dir = Path(tempfile.mkdtemp(prefix="pg-oc-"))
    relay = None
    started = time.time()
    try:
        relay = HostRelay(str(relay_dir / "chat.sock"), run_spec.model_port)
        try:
            done = subprocess.run(podman_argv(run_spec, name, relay_dir), capture_output=True, text=True,
                                  timeout=run_spec.timeout_s + 60, stdin=subprocess.DEVNULL, env=podrun._podman_env())
            rc, stdout, stderr = done.returncode, done.stdout, done.stderr
            # timeout(1) exits 124 when it stopped the harness and 137 when it had to kill it.
            timed_out = rc in (124, 137)
        except subprocess.TimeoutExpired as err:
            subprocess.run(["podman", "--log-level", "error", "rm", "-f", "-t", "0", name], capture_output=True,
                           timeout=60, env=podrun._podman_env())
            rc, stdout, stderr, timed_out = None, _text(err.stdout), _text(err.stderr) + "\nhost timeout", True
    finally:
        if relay is not None:
            relay.close()
        shutil.rmtree(relay_dir, ignore_errors=True)
    return fleet_spec.RunResult(rc, timed_out, final_text(stdout), round(time.time() - started, 1),
                                stdout[-4000:], stderr[-4000:])
