"""A scripted OpenAI chat-completions endpoint, so a harness trace drives a real harness through the gate for $0.

Each POST to a path ending in /chat/completions takes the next step of the script:

- {"tool": name, "args": {...}}: one tool call, as a function call;
- {"text": "..."}: an assistant message, which ends the turn.

The last step repeats once the script runs out. A request with "stream": true gets Server-Sent Events of
chat.completion.chunk objects ending in `data: [DONE]`; any other request gets one chat.completion. GET on a path
ending in /models lists one model, for harnesses that check the model exists. `requests` keeps every parsed body,
so a trace can check that the gate's refusal text reached the model's next request.

`groq=True` answers the way Groq does for gpt-oss: every reply carries a `reasoning` field (in the message, or in a
delta when streamed), and a request whose assistant turn carries `reasoning_content` gets Groq's 400. Three
harnesses send the reasoning back under that name, and the H1 pilot lost its runs on them before the fleet trace
used this.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

MODEL = "scripted"


def _message(step: dict[str, Any], n: int) -> dict[str, Any]:
    if "text" in step:
        return {"role": "assistant", "content": step["text"]}
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": f"call_{n}", "type": "function",
         "function": {"name": step["tool"], "arguments": json.dumps(step["args"])}}]}


REFUSED = {"error": {"message": "'messages.1' : for 'role:assistant' the following must be satisfied"
                             "[('messages.1' : property 'reasoning_content' is unsupported)]",
                  "type": "invalid_request_error"}}


def completion(step: dict[str, Any], n: int, groq: bool = False) -> dict[str, Any]:
    message = _message(step, n)
    if groq:
        message["reasoning"] = f"scripted reasoning {n}"
    return {"id": f"chatcmpl-{n}", "object": "chat.completion", "created": 1_800_000_000 + n, "model": MODEL,
            "choices": [{"index": 0, "message": message,
                         "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}],
            "usage": {"prompt_tokens": 1000 + 10 * n, "completion_tokens": 40 + n, "total_tokens": 1040 + 11 * n}}


def chunks(step: dict[str, Any], n: int, groq: bool = False) -> bytes:
    message = _message(step, n)
    base = {"id": f"chatcmpl-{n}", "object": "chat.completion.chunk", "created": 1_800_000_000 + n, "model": MODEL}
    events = [{**base, "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}]
    if groq:
        events.append({**base, "choices": [{"index": 0, "delta": {"reasoning": f"scripted reasoning {n}"},
                                            "finish_reason": None}]})
    if message.get("tool_calls"):
        call = message["tool_calls"][0]
        events.append({**base, "choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "id": call["id"], "type": "function",
             "function": {"name": call["function"]["name"], "arguments": call["function"]["arguments"]}}]},
            "finish_reason": None}]})
        finish = "tool_calls"
    else:
        events.append({**base, "choices": [{"index": 0, "delta": {"content": message["content"]}, "finish_reason": None}]})
        finish = "stop"
    events.append({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                   "usage": completion(step, n)["usage"]})
    return ("".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n").encode("utf-8")


class FakeChat:
    def __init__(self, steps: list[dict[str, Any]], groq: bool = False) -> None:
        if not steps:
            raise ValueError("a script needs at least one step")
        self.steps, self.groq = steps, groq
        self.requests: list[dict[str, Any]] = []
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
                if self.path.rstrip("/").endswith("/models"):
                    body = {"object": "list", "data": [{"id": MODEL, "object": "model", "owned_by": "lab"}]}
                    return self._send(200, json.dumps(body).encode("utf-8"), "application/json")
                self._send(404, b"{}", "application/json")

            def do_POST(self) -> None:  # noqa: N802
                raw = self.rfile.read(int(self.headers.get("content-length") or 0))
                if not self.path.rstrip("/").endswith("/chat/completions"):
                    return self._send(404, b"{}", "application/json")
                body = json.loads(raw or b"{}")
                with fake._lock:
                    n = len(fake.requests)
                    fake.requests.append(body)
                step = fake.steps[min(n, len(fake.steps) - 1)]
                if fake.groq and any(isinstance(m, dict) and m.get("role") == "assistant" and "reasoning_content" in m
                                     for m in body.get("messages") or []):
                    return self._send(400, json.dumps(REFUSED).encode("utf-8"), "application/json")
                if body.get("stream"):
                    return self._send(200, chunks(step, n, fake.groq), "text/event-stream")
                self._send(200, json.dumps(completion(step, n, fake.groq)).encode("utf-8"), "application/json")

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

    def saw(self, text: str) -> bool:
        """True when any request's messages contain `text` (for checking a refusal reached the model)."""
        return any(text in json.dumps(r.get("messages", [])) for r in self.requests)
