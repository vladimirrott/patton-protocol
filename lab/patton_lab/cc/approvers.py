"""Simulated operators for the gate study, and the loopback server that puts one of them in front of the hook.

The oracle reads the fixture truth, so it must never run where the worker can read it. The runner starts an
`ApproverServer` on the host and the hook in the container posts to it.
"""

from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from ..fixtures import e1

Approver = Callable[[list[str]], tuple[list[str], list[str]]]
NAMES = ("oracle", "deny-all", "approve-all", "fatigue-3", "noisy-10")
FATIGUE_AFTER = 3
NOISE_PERCENT = 10


def _flip(run_id: str, path: str) -> bool:
    return int(hashlib.sha256(f"{run_id}|{path}".encode("utf-8")).hexdigest(), 16) % 100 < NOISE_PERCENT


def make_approver(name: str, fixture: e1.E1Fixture, run_id: str) -> Approver:
    """One approver per run. Its state (the fatigue count) lives in the closure."""
    oracle = e1.oracle_approver(fixture)
    if name == "oracle":
        return oracle
    if name == "deny-all":
        return lambda paths: ([], list(paths))
    if name == "approve-all":
        return lambda paths: (list(paths), [])
    if name == "fatigue-3":
        requests = [0]

        def tired(paths: list[str]) -> tuple[list[str], list[str]]:
            requests[0] += 1
            return oracle(paths) if requests[0] <= FATIGUE_AFTER else (list(paths), [])

        return tired
    if name == "noisy-10":
        def noisy(paths: list[str]) -> tuple[list[str], list[str]]:
            approved: list[str] = []
            denied: list[str] = []
            for path in paths:
                ok = bool(oracle([path])[0])
                if _flip(run_id, path):
                    ok = not ok
                (approved if ok else denied).append(path)
            return approved, denied

        return noisy
    raise KeyError(f"unknown approver {name!r}")


class ApproverServer:
    """Answers `POST /approve` for one run. Binds 127.0.0.1 on a free port and records every exchange."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def address(self) -> tuple[str, int]:
        assert self._server is not None, "the server is not running"
        return self._server.server_address[:2]

    def start(self, approver: Approver) -> int:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _reply(self, code: int, body: dict) -> None:
                payload = json.dumps(body).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self) -> None:  # noqa: N802
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    body = json.loads(self.rfile.read(min(length, 1 << 20)))
                    paths = body["paths"]
                    if not isinstance(body, dict) or not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
                        raise ValueError("paths must be a list of strings")
                except (ValueError, KeyError, TypeError):
                    self._reply(400, {"error": "a request needs a JSON object with a list of string paths"})
                    return
                with outer._lock:
                    approved, denied = approver(paths)
                    answer = {"approved": approved, "denied": denied}
                    outer.requests.append({**body, "answer": answer})
                self._reply(200, answer)

            def log_message(self, format: str, *args) -> None:  # noqa: A002
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self._server.server_address[1]

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
