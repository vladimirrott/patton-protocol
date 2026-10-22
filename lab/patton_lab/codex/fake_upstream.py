"""A scripted Responses API endpoint, so `trace` drives real Codex through every path for $0.

Each POST to a path ending in /responses takes the next step of the script and streams it back as Server-Sent
Events, the way the Responses API does:

- {"exec": "<JavaScript>"}: a custom `exec` tool call, as a code-mode model makes it;
- {"tool": name, "args": {...}}: a function call;
- {"custom": name, "input": "<text>"}: any other custom tool call, such as a freeform apply_patch;
- {"text": "..."}: an assistant message, which ends the turn.

The last step repeats once the script runs out. Every response carries usage and rate-limit headers, so the
recorder's accounting is exercised too. `requests` keeps each parsed request body.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

RATE_HEADERS = {"x-codex-primary-used-percent": "1.5", "x-codex-primary-window-minutes": "300",
                "x-codex-secondary-used-percent": "0.25", "x-codex-secondary-window-minutes": "10080"}


def sse(events: list[dict[str, Any]]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode("utf-8")


def step_events(step: dict[str, Any], n: int) -> list[dict[str, Any]]:
    rid = f"resp_{n}"
    if "text" in step:
        item = {"type": "message", "role": "assistant", "id": f"msg_{n}",
                "content": [{"type": "output_text", "text": step["text"]}]}
    elif "exec" in step:
        item = {"type": "custom_tool_call", "name": "exec", "input": step["exec"], "call_id": f"call_{n}"}
    elif "custom" in step:
        item = {"type": "custom_tool_call", "name": step["custom"], "input": step["input"], "call_id": f"call_{n}"}
    else:
        item = {"type": "function_call", "name": step["tool"], "arguments": json.dumps(step["args"]),
                "call_id": f"call_{n}"}
    usage = {"input_tokens": 1000 + 10 * n, "input_tokens_details": {"cached_tokens": 800},
             "output_tokens": 40 + n, "output_tokens_details": {"reasoning_tokens": 8}, "total_tokens": 1040 + 11 * n}
    events = [{"type": "response.created", "response": {"id": rid}}]
    if "reasoning" in step:
        events.append({"type": "response.output_item.done", "item": {
            "type": "reasoning", "id": f"rs_{n}", "summary": [{"type": "summary_text", "text": step["reasoning"]}],
            "encrypted_content": "gAAAA" + "x" * 64}})
    events += [{"type": "response.output_item.done", "item": item},
               {"type": "response.completed", "response": {"id": rid, "usage": usage}}]
    return events


class FakeUpstream:
    def __init__(self, steps: list[dict[str, Any]], status: int = 200) -> None:
        if not steps:
            raise ValueError("a script needs at least one step")
        self.steps = steps
        self.status = status
        self.content_type = "text/event-stream"
        self.requests: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None

    def start(self) -> int:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                body = self.rfile.read(int(self.headers.get("content-length") or 0))
                with fake._lock:
                    n = len(fake.requests)
                    fake.requests.append(json.loads(body or b"{}"))
                    fake.headers.append({k.lower(): v for k, v in self.headers.items()})
                if not self.path.endswith("/responses"):
                    self.send_response(404)
                    self.end_headers()
                    return
                if fake.status != 200:
                    payload = json.dumps({"error": {"message": "scripted failure"}}).encode("utf-8")
                    self.send_response(fake.status)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                out = sse(step_events(fake.steps[min(n, len(fake.steps) - 1)], n))
                self.send_response(200)
                self.send_header("content-type", fake.content_type)
                for key, value in RATE_HEADERS.items():
                    self.send_header(key, value)
                self.send_header("content-length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

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
