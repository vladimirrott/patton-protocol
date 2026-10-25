"""One Cline CLI 3.0.73 container run, shared by the trace (`trace.py`) and the fleet port (`fleet/ports/cline.py`).

The container has `--network=none`; its only way out is the unix socket in `sock_dir`, which `HostRelay` forwards
to a TCP port on the host's 127.0.0.1 (the trace's FakeChat, or the fleet's recorder), and which the in-container
half of `../opencode/relay.py` serves as 127.0.0.1:PORT_IN. The repo's lab/ and plugins/ are mounted read-only at
/patton/lab and /patton/plugins, so the shim at SHIM runs the bridge, and the bridge finds the gate at
/patton/plugins/patton-gate/scripts/patton_gate.py. Every container carries the LABEL and a name of our own, and a
host-side timeout removes it by that name, so cleanup never touches another agent's containers.

`cline auth` writes the provider settings under HOME, then `cline` runs one task with `--json`, which prints one
NDJSON event per line; `tools()` and `final_text()` read them.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..opencode.podrun import IMAGE, REPO, _podman_env, available as podman_available
from ..opencode.relay import HostRelay

VERSION = "3.0.73"
PACKAGE = REPO / "lab" / "harness_cache" / "cline" / "pkg" / "cline-cli-linux-x64-3.0.73" / "package"
BINARY = PACKAGE / "bin" / "cline"
BINARY_SHA256 = "1ff8841d17d4d83763134c193d414e4f7fcf2ef56af759e5ebbe96727fc2c8f7"  # RESULT.md says where it comes from
LABEL = "patton-fleet=cline"
PORT_IN = 18080
RELAY_IN = "/patton/lab/patton_lab/harness/opencode/relay.py"
SHIM = "/patton/lab/patton_lab/harness/shim/patton-hook.sh"
GATE_ENV = {"PATTON_GATE_ROOT": "/work", "PYTHONDONTWRITEBYTECODE": "1",
            "PATH": "/opt/cline/bin:/usr/local/bin:/usr/bin:/bin"}

HOOKS = Path(__file__).resolve().parent / "hooks"  # PreToolUse: the shim, at PATTON_SHIM in the container
SHIM_ENV = {"PATTON_SHIM": SHIM}


def why_not() -> str | None:
    """None when podman, the pinned image and the pinned Cline build are here, else why not."""
    import hashlib
    missing = podman_available()
    if missing:
        return missing
    if not BINARY.is_file():
        return f"the pinned Cline package is not in {PACKAGE.relative_to(REPO)}"
    if hashlib.sha256(BINARY.read_bytes()).hexdigest() != BINARY_SHA256:
        return f"{BINARY.relative_to(REPO)} is not the pinned build (sha256 differs)"
    return None


def task(model: str, prompt: str, base: str = f"http://127.0.0.1:{PORT_IN}/v1", flags: str = "--yolo") -> list[str]:
    """The container's command: write the provider settings, then run one headless task."""
    script = (f"cline auth -p openai-compatible -k dummy-not-a-key -m {shlex.quote(model)} -b {shlex.quote(base)} "
              f">/dev/null && exec cline -P openai-compatible -m {shlex.quote(model)} --json {flags} "
              f"{shlex.quote(prompt)}")
    return ["sh", "-c", script]


@dataclass
class Result:
    code: int | None
    timed_out: bool
    seconds: float
    stdout: str
    stderr: str

    def events(self) -> list[dict[str, Any]]:
        out = []
        for line in self.stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                out.append(event)
        return out


def run(name: str, upstream_port: int, command: list[str], *, mounts: list[tuple[Path, str]],
        env: dict[str, str], timeout: int) -> Result:
    """Run `command` in a fresh labeled container whose 127.0.0.1:PORT_IN leads to the host's `upstream_port`."""
    sock_dir = tempfile.mkdtemp(prefix="pg-cline-sock-")  # short: AF_UNIX paths stop at 108 bytes
    os.chmod(sock_dir, 0o755)
    relay = HostRelay(os.path.join(sock_dir, "chat.sock"), upstream_port)
    cname = f"patton-cline-{name}-{os.getpid()}-{int(time.time() * 1000) % 100000}"
    argv = ["podman", "--log-level", "error", "run", "--rm", "--pull=never", "--name", cname, "--label", LABEL,
            "--userns=keep-id", "--network=none", "--cap-drop=all", "--security-opt=no-new-privileges", "--read-only",
            "--tmpfs", "/tmp:size=256m", "--memory=3g", "--pids-limit=1024", "-e", "HOME=/home/user",
            "-v", f"{sock_dir}:/relay:ro", "-v", f"{REPO / 'lab'}:/patton/lab:ro",
            "-v", f"{REPO / 'plugins'}:/patton/plugins:ro", "-v", f"{PACKAGE}:/opt/cline:ro"]
    for key, value in {**GATE_ENV, **env}.items():
        argv += ["-e", f"{key}={value}"]
    for host, inside in mounts:
        argv += ["-v", f"{host}:{inside}"]
    argv += ["-w", "/work", IMAGE, "timeout", "-k", "5", str(timeout), "python3", "-I", RELAY_IN, "/relay/chat.sock",
             str(PORT_IN), "--", *command]
    start = time.monotonic()
    timed_out = False
    try:
        done = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=timeout + 60,
                              stdin=subprocess.DEVNULL, env=_podman_env())
        code, stdout, stderr = done.returncode, done.stdout, done.stderr
        timed_out = code in (124, 137)
    except subprocess.TimeoutExpired as err:
        subprocess.run(["podman", "--log-level", "error", "rm", "-f", cname], capture_output=True, timeout=60,
                       env=_podman_env())
        out = err.stdout or ""
        code, stdout, stderr, timed_out = None, out.decode() if isinstance(out, bytes) else out, "host timeout", True
    finally:
        relay.close()
        try:
            os.rmdir(sock_dir)
        except OSError:
            pass
    return Result(code, timed_out, round(time.monotonic() - start, 1), stdout, stderr)


def tools(result: Result) -> list[dict[str, Any]]:
    """Every finished tool call: {"tool", "input" (as started), "output", "error"}."""
    started, done = {}, []
    for event in result.events():
        inner = event.get("event") or {}
        if event.get("type") != "agent_event" or inner.get("contentType") != "tool":
            continue
        if inner.get("type") == "content_start":
            started[inner.get("toolCallId")] = inner.get("input")
        elif inner.get("type") == "content_end":
            done.append({"tool": inner.get("toolName"), "input": started.get(inner.get("toolCallId")),
                         "output": inner.get("output"), "error": inner.get("error")})
    return done


def aborted(result: Result) -> bool:
    return any(e.get("type") == "run_aborted" for e in result.events())
