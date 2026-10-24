"""What a harness trace needs besides the harness: a scripted model the container can reach and nothing else, fresh
directories per scenario, and one podman run with a hard time limit.

The container runs with --network=none. The scripted model (FakeChat) listens on host loopback; a host thread
serves a unix socket in a per-run directory and connects each stream only to that port, and `fwd.py` inside the
container listens on 127.0.0.1:<PORT> and pipes to the socket. So the harness reaches the fake at
http://127.0.0.1:<PORT>/v1 and has no route anywhere else. `net_probe()` checks that from inside a container.

Used by the pi and omp traces; nothing here knows about either harness.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from patton_lab import isolate
from patton_lab.harness.fake_chat import FakeChat

LAB = Path(__file__).resolve().parents[3]          # .../lab
REPO = LAB.parent
CACHE = LAB / "harness_cache"
PORT = 8099                                        # inside the container only
FWD = Path(__file__).resolve().parent / "fwd.py"
IMAGE = isolate.IMAGE
KEEP_TEXT = "only copy\n"
LABEL = "patton-harness-trace"   # every container a trace starts carries it, so cleanup checks only ours


def podman_env() -> dict[str, str]:
    keep = ("PATH", "HOME", "XDG_RUNTIME_DIR", "LANG", "USER", "LOGNAME", "TMPDIR")
    return {k: os.environ[k] for k in keep if k in os.environ}


class SocketLink:
    """Host end: accept on a unix socket, connect each stream to 127.0.0.1:<port>, pipe both ways."""

    def __init__(self, path: Path, port: int) -> None:
        self.path, self.port = path, port
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(str(path))
        os.chmod(path, 0o600)
        self._server.listen(64)
        self._stopped = False
        threading.Thread(target=self._accept, daemon=True).start()

    @staticmethod
    def _pump(src: socket.socket, dst: socket.socket) -> None:
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except OSError:
            pass
        finally:
            for s in (src, dst):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def _serve(self, conn: socket.socket) -> None:
        try:
            up = socket.create_connection(("127.0.0.1", self.port), timeout=10)
            up.settimeout(None)
        except OSError:
            conn.close()
            return
        threading.Thread(target=self._pump, args=(conn, up), daemon=True).start()
        self._pump(up, conn)

    def _accept(self) -> None:
        while not self._stopped:
            try:
                conn, _ = self._server.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def close(self) -> None:
        self._stopped = True
        self._server.close()


@dataclass
class Run:
    """One scenario's directories, the fake, and what the container printed."""
    name: str
    base: Path
    fake: FakeChat
    work: Path = field(init=False)
    home: Path = field(init=False)
    state: Path = field(init=False)
    sock: Path = field(init=False)
    extra: Path = field(init=False)
    link: SocketLink | None = None
    rc: int | None = None
    stdout: str = ""
    stderr: str = ""
    seconds: float = 0.0

    def __post_init__(self) -> None:
        for name in ("work", "home", "state", "sock", "extra"):
            d = self.base / name
            d.mkdir(parents=True)
            setattr(self, name, d)
        (self.work / "keep.txt").write_text(KEEP_TEXT)

    def events(self) -> list[dict]:
        out = []
        for line in self.stdout.splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if isinstance(e, dict):
                out.append(e)
        return out

    def keep_intact(self) -> bool:
        p = self.work / "keep.txt"
        return p.is_file() and p.read_text() == KEEP_TEXT

    def save(self) -> None:
        (self.base / "stdout.jsonl").write_text(self.stdout)
        (self.base / "stderr.txt").write_text(self.stderr)
        (self.base / "requests.json").write_text(json.dumps(self.fake.requests, indent=1))


def new_run(harness: str, name: str, steps: list[dict]) -> Run:
    root = CACHE / harness / "runs"
    root.mkdir(parents=True, exist_ok=True)
    target = root / name
    if target.exists():
        shutil.rmtree(target)
    # The socket path must stay under 108 bytes, so the run lives in a short temp dir and is copied back after.
    base = Path(tempfile.mkdtemp(prefix=f"ht-{harness}-{name}-"))
    return Run(name=name, base=base, fake=FakeChat(steps))


def finish(run: Run, harness: str) -> None:
    run.save()
    shutil.rmtree(run.sock, ignore_errors=True)
    dest = CACHE / harness / "runs" / run.name
    shutil.copytree(run.base, dest, symlinks=True, ignore=shutil.ignore_patterns("node_modules"))
    shutil.rmtree(run.base, ignore_errors=True)


def entry(harness_argv: list[str], timeout_s: int) -> list[str]:
    """Container command: start the forwarder, wait until it listens, then run the harness under `timeout`."""
    import shlex
    cmd = " ".join(shlex.quote(a) for a in harness_argv)
    script = (f"python3 -I /kit/fwd.py {PORT} /sock/fake.sock /tmp/fwd.ready 2>/tmp/fwd.err & "
              "i=0; while [ ! -f /tmp/fwd.ready ] && [ $i -lt 100 ]; do sleep 0.1; i=$((i+1)); done; "
              "[ -f /tmp/fwd.ready ] || { echo 'forwarder never listened' >&2; exit 97; }; "
              f"exec timeout -k 5 {timeout_s} {cmd} </dev/null")
    return ["bash", "-c", script]


def podman_argv(run: Run, mounts: list[tuple[Path | str, str, str]], env: dict[str, str],
                harness_argv: list[str], timeout_s: int) -> list[str]:
    argv = ["podman", "--log-level", "error", "run", "--rm", "--pull=never", "--userns=keep-id",
            "--label", f"{LABEL}={run.name}", "--network=none", "--cap-drop=all", "--security-opt=no-new-privileges", "--read-only",
            "--tmpfs", "/tmp:size=256m,exec", "--memory=4g", "--pids-limit=512",
            "-v", f"{run.work}:/work:rw", "-v", f"{run.home}:/home/user:rw", "-v", f"{run.state}:/state:rw",
            "-v", f"{run.sock}:/sock:rw", "-v", f"{FWD}:/kit/fwd.py:ro", "-v", f"{run.extra}:/extra:ro",
            "-v", f"{LAB}:/repo/lab:ro", "-v", f"{REPO / 'plugins'}:/repo/plugins:ro"]
    for host, dest, mode in mounts:
        argv += ["-v", f"{host}:{dest}:{mode}"]
    base_env = {"HOME": "/home/user", "PATTON_GATE_ROOT": "/work", "PATTON_GATE_STATE": "/state",
                "XDG_CONFIG_HOME": "/home/user/.config", "PYTHONDONTWRITEBYTECODE": "1",
                "LANG": "C.UTF-8", "TERM": "dumb", "NO_COLOR": "1"}
    for k, v in {**base_env, **env}.items():
        argv += ["-e", f"{k}={v}"]
    return argv + ["-w", "/work", IMAGE] + entry(harness_argv, timeout_s)


def execute(run: Run, argv: list[str], timeout_s: int) -> Run:
    port = run.fake.start("127.0.0.1")
    run.link = SocketLink(run.sock / "fake.sock", port)
    start = time.time()
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s + 60,
                              stdin=subprocess.DEVNULL, env=podman_env())
        run.rc, run.stdout, run.stderr = done.returncode, done.stdout, done.stderr
    except subprocess.TimeoutExpired as err:
        run.rc = None
        run.stdout = (err.stdout or b"").decode() if isinstance(err.stdout, bytes) else (err.stdout or "")
        run.stderr = "host timeout\n" + ((err.stderr or b"").decode() if isinstance(err.stderr, bytes) else (err.stderr or ""))
    finally:
        run.seconds = round(time.time() - start, 1)
        run.link.close()
        run.fake.stop()
    return run


def net_probe() -> tuple[bool, str]:
    """True when a container started like the traces cannot open a TCP connection to the outside or to the host."""
    code = ("import socket\nres=[]\n"
            "for h,p in [('1.1.1.1',443),('10.0.2.2',22),('192.168.1.1',80)]:\n"
            "    s=socket.socket(); s.settimeout(3)\n"
            "    try: s.connect((h,p)); res.append(f'{h}:open')\n"
            "    except OSError as e: res.append(f'{h}:{type(e).__name__}')\n"
            "print(' '.join(res))\n")
    done = subprocess.run(["podman", "--log-level", "error", "run", "--rm", "--pull=never", "--label", f"{LABEL}=net",
                           "--network=none",
                           "--cap-drop=all", "--read-only", IMAGE, "timeout", "30", "python3", "-I", "-c", code],
                          capture_output=True, text=True, timeout=90, env=podman_env())
    out = done.stdout.strip()
    return done.returncode == 0 and out != "" and "open" not in out, out or done.stderr.strip()[-200:]


def leftover_containers() -> list[str]:
    """Trace containers still present (every trace run uses --rm, so this should be empty). Other jobs on the
    machine use the same image, so the filter is the trace label, not the image."""
    done = subprocess.run(["podman", "ps", "-a", "--format", "{{.ID}} {{.CreatedAt}} {{.Image}}",
                           "--filter", f"label={LABEL}"], capture_output=True, text=True, timeout=60,
                          env=podman_env())
    return [line for line in done.stdout.splitlines() if line.strip()]
