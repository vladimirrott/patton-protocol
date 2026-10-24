"""A scripted Gemini API endpoint (generateContent and streamGenerateContent), so a Gemini CLI trace runs for $0.

It mirrors `fake_chat.FakeChat`: a script of steps, `start()` returns the port, `requests` keeps every parsed
body, and `saw(text)` says whether any request carried `text`.

- {"tool": name, "args": {...}}: one function call;
- {"text": "..."}: a model message, which ends the turn.

Only a request that offers tools (`tools` in the body) takes the next step; the last step repeats once the
script runs out. Gemini CLI also makes side calls without tools (the next-speaker check, loop detection, session
titles); those get `side_text` and do not move the script, so the scripted turn stays aligned whatever side calls
a release adds. Every request, side or not, is kept in `requests` with its path under the key "_path".

Routes: POST .../models/<model>:streamGenerateContent (Server-Sent Events, one `data:` chunk per response, as the
API sends with `alt=sse`), POST .../models/<model>:generateContent (one JSON response), POST ...:countTokens, and
GET .../models (one model listed).
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

MODEL = "gemini-2.5-flash"
SIDE_TEXT = '{"reasoning": "scripted", "next_speaker": "user"}'


def _parts(step: dict[str, Any]) -> list[dict[str, Any]]:
    if "text" in step:
        return [{"text": step["text"]}]
    return [{"functionCall": {"name": step["tool"], "args": step["args"]}}]


def response(step: dict[str, Any], n: int) -> dict[str, Any]:
    return {"candidates": [{"content": {"role": "model", "parts": _parts(step)}, "finishReason": "STOP", "index": 0}],
            "usageMetadata": {"promptTokenCount": 1000 + 10 * n, "candidatesTokenCount": 40 + n,
                              "totalTokenCount": 1040 + 11 * n},
            "modelVersion": MODEL, "responseId": f"resp-{n}"}


def sse(step: dict[str, Any], n: int) -> bytes:
    return f"data: {json.dumps(response(step, n))}\r\n\r\n".encode("utf-8")


class FakeGemini:
    def __init__(self, steps: list[dict[str, Any]], side_text: str = SIDE_TEXT) -> None:
        if not steps:
            raise ValueError("a script needs at least one step")
        self.steps = steps
        self.side_text = side_text
        self.requests: list[dict[str, Any]] = []
        self.turns = 0  # requests that took a step
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None

    def start(self, host: str = "127.0.0.1") -> int:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, code: int, payload: bytes, kind: str) -> None:
                self.send_response(code)
                self.send_header("content-type", kind)
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self) -> None:  # noqa: N802
                if self.path.split("?")[0].rstrip("/").endswith("/models"):
                    body = {"models": [{"name": f"models/{MODEL}", "supportedGenerationMethods":
                                        ["generateContent", "streamGenerateContent", "countTokens"]}]}
                    return self._send(200, json.dumps(body).encode("utf-8"), "application/json")
                self._send(404, b"{}", "application/json")

            def do_POST(self) -> None:  # noqa: N802
                raw = self.rfile.read(int(self.headers.get("content-length") or 0))
                path = self.path.split("?")[0]
                try:
                    body = json.loads(raw or b"{}")
                except ValueError:
                    body = {"_raw": raw.decode("utf-8", "replace")}
                with fake._lock:
                    fake.requests.append({**body, "_path": path})
                    scripted = bool(body.get("tools")) and (":generateContent" in path or
                                                           ":streamGenerateContent" in path)
                    if scripted:
                        n = fake.turns
                        fake.turns += 1
                    else:
                        n = len(fake.requests) - 1
                if path.endswith(":countTokens"):
                    return self._send(200, b'{"totalTokens": 1000}', "application/json")
                if not (path.endswith(":generateContent") or path.endswith(":streamGenerateContent")):
                    return self._send(404, b"{}", "application/json")
                step = fake.steps[min(n, len(fake.steps) - 1)] if scripted else {"text": fake.side_text}
                if path.endswith(":streamGenerateContent"):
                    return self._send(200, sse(step, n), "text/event-stream")
                self._send(200, json.dumps(response(step, n)).encode("utf-8"), "application/json")

            def log_message(self, format: str, *args) -> None:  # noqa: A002
                return

        self._server = ThreadingHTTPServer((host, 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self._server.server_address[1]

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def scripted(self) -> list[dict[str, Any]]:
        """The requests that offered tools, in order: the agent's own turns."""
        return [r for r in self.requests if r.get("tools")]

    def saw(self, text: str) -> bool:
        """True when any request's contents carry `text` (for checking a refusal reached the model)."""
        return any(text in json.dumps(r.get("contents", [])) for r in self.requests)
