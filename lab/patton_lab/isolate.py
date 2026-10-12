"""Run Python against a copy of a sandbox in a container with no network.

Model-written code runs here (E2 snippets, E4 solutions and hidden tests).

Layout inside the container:

    /src       read-only copy of the sandbox's /work, taken at call time
    /work      a size-capped tmpfs; the boot script copies /src into it
    /harness   read-only: the boot script, the code to run, any hidden tests

The model never controls /harness, so it cannot plant a test file or replace
the runner. The container has no network, no capabilities, no new privileges,
a read-only root, and limits on memory, processes, disk and time. The podman
CLI gets a minimal environment, so no API key reaches it.

A start sentinel tells a podman failure apart from a code result: if the boot
script never ran, `IsolationError` is raised and nothing is recorded as the
code's output. Host paths are removed from everything the container prints.

When podman or the pinned image is missing, `available()` is False and the
callers refuse. There is no fallback to running on the host.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import uuid
from functools import lru_cache
from pathlib import Path

IMAGE = "docker.io/library/python@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f"
OUTPUT_LIMIT = 4000
START = "PATTON_BOOT_START"
END = "PATTON_BOOT_END"
WORK_TMPFS = "size=64m"

BOOT = f'''import os, runpy, shutil, sys
print("{START}", flush=True)
shutil.copytree("/src", "/work", dirs_exist_ok=True, symlinks=True)
os.chdir("/work")
sys.path.append("/work")
try:
    runpy.run_path("/harness/main.py", run_name="__main__")
finally:
    sys.stdout.flush()
    print("{END}", flush=True)
'''


class IsolationError(Exception):
    """The container did not run the code (podman or image failure)."""


def _podman_env() -> dict[str, str]:
    keep = ("PATH", "HOME", "XDG_RUNTIME_DIR", "LANG", "USER", "LOGNAME", "TMPDIR")
    return {name: os.environ[name] for name in keep if name in os.environ}


@lru_cache(maxsize=1)
def available() -> bool:
    if shutil.which("podman") is None:
        return False
    probe = subprocess.run(
        ["podman", "--log-level", "error", "image", "exists", IMAGE],
        capture_output=True,
        timeout=60,
        env=_podman_env(),
    )
    return probe.returncode == 0


def _redact_paths(text: str, temp_root: str) -> str:
    home = str(Path.home())
    text = text.replace(temp_root, "/tmp/patton-lab")
    if home and home != "/":
        text = text.replace(home, "/home/user")
    return text


def _cap(text: str) -> str:
    if len(text) <= OUTPUT_LIMIT:
        return text
    return text[:OUTPUT_LIMIT] + f"\n[truncated: {len(text) - OUTPUT_LIMIT} more characters]"


def run_python(
    work: Path,
    code: str,
    timeout: int = 10,
    harness_files: dict[str, str] | None = None,
) -> tuple[int, str]:
    """Run `code` with /work as the working directory. Returns (exit code, output)."""
    if not available():
        raise IsolationError("isolated runner unavailable: podman or the pinned image is missing")
    with tempfile.TemporaryDirectory(prefix="patton-lab-run-") as tmp:
        src = Path(tmp) / "src"
        harness = Path(tmp) / "harness"
        shutil.copytree(work, src, symlinks=True)
        harness.mkdir()
        (harness / "boot.py").write_text(BOOT, encoding="utf-8")
        (harness / "main.py").write_text(code, encoding="utf-8")
        for rel, content in (harness_files or {}).items():
            target = (harness / rel).resolve()
            if harness.resolve() not in target.parents:
                raise ValueError(f"harness file {rel!r} escapes the harness directory")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        name = f"patton-lab-{uuid.uuid4().hex[:12]}"
        command = [
            "podman", "--log-level", "error", "run", "--rm", "--name", name,
            "--network", "none", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--tmpfs", "/tmp:size=16m", "--tmpfs", f"/work:{WORK_TMPFS}",
            "--memory", "512m", "--pids-limit", "64", "--cpus", "1",
            "--timeout", str(timeout),
            "--env", "PYTHONDONTWRITEBYTECODE=1",
            "-v", f"{src}:/src:ro", "-v", f"{harness}:/harness:ro", "-w", "/work",
            IMAGE, "python3", "-E", "-s", "/harness/boot.py",
        ]
        started = time.monotonic()
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=timeout + 30, env=_podman_env())
        except subprocess.TimeoutExpired:
            subprocess.run(["podman", "--log-level", "error", "kill", name], capture_output=True, env=_podman_env())
            return 124, "TIMEOUT: the code ran past its time limit and was killed"
        elapsed = time.monotonic() - started
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        if START not in stdout:
            raise IsolationError(
                f"the container did not start the code (exit {result.returncode}): "
                + _redact_paths(stderr.strip(), tmp)[:300]
            )
        finished = END in stdout
        body = stdout.replace(START + "\n", "", 1).replace(END + "\n", "").replace(END, "")
        output = _cap(_redact_paths(body + stderr, tmp))
        if not finished and result.returncode != 0 and elapsed >= timeout:
            return 124, "TIMEOUT: the code ran past its time limit and was killed\n" + output
        if result.returncode in (137, 143) and not finished:
            return result.returncode, "KILLED: the container hit its memory or process limit\n" + output
        return result.returncode, output
