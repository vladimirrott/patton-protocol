"""A scripted T1 analyst for the $0 fleet trace, so the triage arm's wiring is checked without a model.

It answers the gate's analyst prompt the way a careful analyst would on the lab's fixtures: a path is allowed when a
line of the project's notes names it and calls it disposable, rotated, scratch or safe to delete, and says nothing
of it being required or the only copy; the evidence is that line, word for word. Everything else escalates. The
claim reader always reads a claim, and the Verifier always passes, so the P1 trace can see both run.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FREE = re.compile(r"disposable|can go|scratch|rotated|safe to delete|can be deleted", re.I)
KEEP = re.compile(r"required|only copy|not shipped|must stay|keep", re.I)


def answer(user: str) -> dict:
    notes = user.split("## Project notes", 1)[1].split("## Flagged files", 1)[0] if "## Project notes" in user else ""
    paths = [line[2:] for line in user.split("## Paths the gate flagged\n", 1)[-1].splitlines() if line.startswith("- ")]
    rows = []
    for path in paths:
        name = path.rsplit("/", 1)[-1]
        line = next((l for l in notes.splitlines() if name in l and FREE.search(l) and not KEEP.search(l)), None)
        rows.append({"path": path, "action": "allow" if line else "escalate", "severity": "low" if line else "high",
                     "evidence": line.strip() if line else "the notes do not say this file can go"})
    return {"paths": rows}


class FakeAnalyst:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self._server: ThreadingHTTPServer | None = None

    def start(self) -> int:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
                outer.requests.append(body)
                user = next((m["content"] for m in body.get("messages", []) if m.get("role") == "user"), "")
                system = next((m["content"] for m in body.get("messages", []) if m.get("role") == "system"), "")
                if system.startswith("You read the final message of a coding agent"):
                    content = json.dumps({"claims_done": True, "quote": "Done."})
                elif system.startswith("You are an independent verifier"):
                    content = json.dumps({"verdict": "pass", "findings": []})
                else:
                    content = json.dumps(answer(user))
                data = json.dumps({"id": "t1", "object": "chat.completion", "model": "scripted",
                                   "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                                                "finish_reason": "stop"}],
                                   "usage": {"prompt_tokens": 900, "completion_tokens": 60}}).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args) -> None:  # noqa: A002
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self._server.server_address[1]

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
