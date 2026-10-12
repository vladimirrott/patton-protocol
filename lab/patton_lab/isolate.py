"""Run Python against a copy of a sandbox in a container with no network.

Model-written code runs here (E2 snippets, E4 solutions and hidden tests).
The container sees only a disposable copy of /work. It has no network, no
host environment, no host home directory, a read-only root, and limits on
memory, processes and time. The copy is thrown away, so nothing the code does
reaches the sandbox the model is working in.

When podman or the pinned image is missing, `available()` is False and the
callers refuse. There is no fallback to running on the host.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
import uuid
from functools import lru_cache
from pathlib import Path

IMAGE = "docker.io/library/python@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f"
OUTPUT_LIMIT = 4000
SNIPPET_NAME = "_patton_snippet.py"


@lru_cache(maxsize=1)
def available() -> bool:
    if shutil.which("podman") is None:
        return False
    probe = subprocess.run(
        ["podman", "--log-level", "error", "image", "exists", IMAGE],
        capture_output=True,
        timeout=60,
    )
    return probe.returncode == 0


def _cap(text: str) -> str:
    if len(text) <= OUTPUT_LIMIT:
        return text
    return text[:OUTPUT_LIMIT] + f"\n[truncated: {len(text) - OUTPUT_LIMIT} more characters]"


def run_python(
    work: Path,
    code: str,
    timeout: int = 10,
    extra_files: dict[str, str] | None = None,
) -> tuple[int, str]:
    """Run `code` with /work as the working directory. Returns (exit code, output)."""
    if not available():
        raise RuntimeError("isolated runner unavailable: podman or the pinned image is missing")
    with tempfile.TemporaryDirectory(prefix="patton-lab-run-") as tmp:
        copy = Path(tmp) / "work"
        shutil.copytree(work, copy, symlinks=True)
        for rel, content in (extra_files or {}).items():
            target = copy / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        (copy / SNIPPET_NAME).write_text(code, encoding="utf-8")
        name = f"patton-lab-{uuid.uuid4().hex[:12]}"
        command = [
            "podman", "--log-level", "error", "run", "--rm", "--name", name,
            "--network", "none", "--read-only", "--tmpfs", "/tmp",
            "--memory", "512m", "--pids-limit", "64", "--cpus", "1",
            "--timeout", str(timeout),
            "--env", "PYTHONDONTWRITEBYTECODE=1",
            "-v", f"{copy}:/work", "-w", "/work",
            IMAGE, "python3", "-E", "-s", f"/work/{SNIPPET_NAME}",
        ]
        started = time.monotonic()
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=timeout + 30)
        except subprocess.TimeoutExpired:
            subprocess.run(["podman", "--log-level", "error", "kill", name], capture_output=True)
            return 124, "TIMEOUT: the code ran past its time limit and was killed"
        output = (result.stdout or "") + (result.stderr or "")
        elapsed = time.monotonic() - started
        # podman's --timeout ends the container with exit 255 (measured on podman 4.9),
        # which podman also uses for its own errors, so elapsed time decides.
        if result.returncode != 0 and elapsed >= timeout:
            return 124, "TIMEOUT: the code ran past its time limit and was killed\n" + _cap(output)
        if result.returncode in (137, 143):
            return result.returncode, "KILLED: the container hit its memory or process limit\n" + _cap(output)
        return result.returncode, _cap(output)
