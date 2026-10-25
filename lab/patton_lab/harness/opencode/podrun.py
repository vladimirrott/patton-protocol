"""Run one harness scenario in podman against the scripted model, and keep what a trace needs to judge it.

Used by the OpenCode and Cline traces. Each run gets a fresh directory on the host:

    work/      -> /work            keep.txt ("only copy\\n") and nothing else
    home/      -> /home/user       the harness's HOME (its config, caches, logs)
    state/     -> /state           PATTON_GATE_STATE, per run
    relay/     -> /relay (ro)      the unix socket that leads to the fake (see relay.py)
    extra mounts as given          the harness package, hook directories

The repo's lab/ and plugins/ are mounted read-only at /repo/lab and /repo/plugins, so the bridge at
/repo/lab/patton_lab/harness/bridge.py finds the gate at /repo/plugins/patton-gate/scripts/patton_gate.py.
The container has no network (`--network=none`), no capabilities, no new privileges, a read-only root, a /tmp
tmpfs, and limits on memory and processes. `timeout -k 5` inside it and a host-side timeout bound every run.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..fake_chat import FakeChat
from .relay import HostRelay

REPO = Path(__file__).resolve().parents[4]
IMAGE = "docker.io/library/python@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f"
RELAY_IN = "/repo/lab/patton_lab/harness/opencode/relay.py"
PORT_IN = 18080  # the fake, as the harness inside the container sees it: http://127.0.0.1:18080/v1
KEEP = "only copy\n"


@dataclass
class Run:
    name: str
    code: int
    seconds: float
    stdout: str
    stderr: str
    requests: list[dict[str, Any]]
    root: Path
    saw: Any = None
    files: dict[str, str] = field(default_factory=dict)
    # The bridge writes the gate's manifest on its first call in a session, so gate_ran means a hook ran.
    gate_ran: bool = False
    ledger: list[dict[str, Any]] = field(default_factory=list)

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


def _podman_env() -> dict[str, str]:
    keep = ("PATH", "HOME", "XDG_RUNTIME_DIR", "LANG", "USER", "LOGNAME")
    return {name: os.environ[name] for name in keep if name in os.environ}


def available() -> str | None:
    """None when podman and the pinned image are here, else why not."""
    if shutil.which("podman") is None:
        return "podman is not installed"
    probe = subprocess.run(["podman", "--log-level", "error", "image", "exists", IMAGE], capture_output=True,
                           timeout=60, env=_podman_env())
    return None if probe.returncode == 0 else f"the pinned image {IMAGE} is missing"


def run(name: str, steps: list[dict[str, Any]], command: list[str], *, env: dict[str, str] | None = None,
        mounts: list[tuple[Path, str]] | None = None, timeout: int = 150, keep: bool = False,
        prepare=None) -> Run:
    """Run `command` in a fresh container against a FakeChat playing `steps`."""
    root = Path(tempfile.mkdtemp(prefix=f"pg-{name}-"))
    for sub in ("work", "home", "state", "relay"):
        (root / sub).mkdir()
    (root / "work" / "keep.txt").write_text(KEEP, encoding="utf-8")
    if prepare is not None:
        prepare(root)
    fake = FakeChat(steps)
    port = fake.start("127.0.0.1")
    relay = HostRelay(str(root / "relay" / "chat.sock"), port)
    argv = ["podman", "--log-level", "error", "run", "--rm", "--pull=never", "--label", f"patton-trace={name}",
            "--userns=keep-id", "--network=none",
            "--cap-drop=all", "--security-opt=no-new-privileges", "--read-only", "--tmpfs", "/tmp:size=256m",
            "--memory=3g", "--pids-limit=1024",
            "-e", "HOME=/home/user", "-e", "PATTON_GATE_ROOT=/work", "-e", "PATTON_GATE_STATE=/state",
            "-e", "PYTHONDONTWRITEBYTECODE=1",
            "-v", f"{root / 'work'}:/work:rw", "-v", f"{root / 'home'}:/home/user:rw",
            "-v", f"{root / 'state'}:/state:rw", "-v", f"{root / 'relay'}:/relay:ro",
            "-v", f"{REPO / 'lab'}:/repo/lab:ro", "-v", f"{REPO / 'plugins'}:/repo/plugins:ro"]
    for key, value in (env or {}).items():
        argv += ["-e", f"{key}={value}"]
    for host, inside in mounts or []:
        argv += ["-v", f"{host}:{inside}"]
    argv += ["-w", "/work", IMAGE, "timeout", "-k", "5", str(timeout), "python3", "-I", RELAY_IN, "/relay/chat.sock",
             str(PORT_IN), "--", *command]
    start = time.time()
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout + 60, stdin=subprocess.DEVNULL,
                              env=_podman_env())
        code, stdout, stderr = done.returncode, done.stdout, done.stderr
    except subprocess.TimeoutExpired as err:
        code, stdout, stderr = -1, (err.stdout or b"").decode() if isinstance(err.stdout, bytes) else (err.stdout or ""), \
            "host timeout"
    finally:
        relay.close()
        fake.stop()
    result = Run(name, code, round(time.time() - start, 1), stdout, stderr, list(fake.requests), root, saw=fake.saw)
    for path in sorted((root / "work").rglob("*")):
        if path.is_file():
            result.files[str(path.relative_to(root / "work"))] = path.read_text(encoding="utf-8", errors="replace")
    result.gate_ran = (root / "state" / "manifest.json").is_file()
    ledger = root / "state" / "ledger.jsonl"
    if ledger.is_file():
        result.ledger = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not keep:
        shutil.rmtree(root, ignore_errors=True)
    return result
