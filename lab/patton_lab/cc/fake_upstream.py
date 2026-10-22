"""A scripted Anthropic Messages endpoint, for the recorder's tests and the free end-to-end trace.

Step n of the script answers a request that already holds n assistant turns, so the server keeps no state
between calls and a retry gets the same answer. A step is one of:

  {"tool": "Bash", "input": {...}, "thinking": "..."}   a tool call, optionally after a thinking block
  {"text": "..."}                                        a final answer
  {"status": 429}                                        an error status with an Anthropic error body

A request without tools, or one past the end of the script, gets a short text answer. Usage is made up from the
request size unless the server is given fixed usage, so a meter fed from it moves.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

ERRORS = {429: "rate_limit_error", 500: "api_error", 529: "overloaded_error"}


def _step(body: dict[str, Any], script: list[dict[str, Any]]) -> dict[str, Any]:
    if not body.get("tools"):
        return {"text": "ok"}
    n = sum(1 for m in body.get("messages") or [] if isinstance(m, dict) and m.get("role") == "assistant")
    return script[n] if n < len(script) else {"text": "Finished."}


def _message(step: dict[str, Any], n: int, usage: dict[str, int], model: str) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    if step.get("thinking"):
        content.append({"type": "thinking", "thinking": step["thinking"], "signature": "fake-signature"})
    if "tool" in step:
        content.append({"type": "tool_use", "id": f"toolu_fake_{n}", "name": step["tool"], "input": step.get("input", {})})
    else:
        content.append({"type": "text", "text": step.get("text", "")})
    return {"id": f"msg_fake_{n}", "type": "message", "role": "assistant", "model": model, "content": content,
            "stop_reason": "tool_use" if "tool" in step else "end_turn", "stop_sequence": None, "usage": usage}


def _sse(message: dict[str, Any]) -> bytes:
    def event(name: str, data: dict[str, Any]) -> str:
        return f"event: {name}\ndata: {json.dumps(data)}\n\n"

    head = {**message, "content": [], "stop_reason": None,
            "usage": {"input_tokens": message["usage"]["input_tokens"], "output_tokens": 1}}
    out = [event("message_start", {"type": "message_start", "message": head})]
    for i, block in enumerate(message["content"]):
        if block["type"] == "thinking":
            out.append(event("content_block_start", {"type": "content_block_start", "index": i,
                                                     "content_block": {"type": "thinking", "thinking": "", "signature": ""}}))
            out.append(event("content_block_delta", {"type": "content_block_delta", "index": i,
                                                     "delta": {"type": "thinking_delta", "thinking": block["thinking"]}}))
            out.append(event("content_block_delta", {"type": "content_block_delta", "index": i,
                                                     "delta": {"type": "signature_delta", "signature": block["signature"]}}))
        elif block["type"] == "tool_use":
            out.append(event("content_block_start", {"type": "content_block_start", "index": i,
                                                     "content_block": {**block, "input": {}}}))
            out.append(event("content_block_delta", {"type": "content_block_delta", "index": i,
                                                     "delta": {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}}))
        else:
            out.append(event("content_block_start", {"type": "content_block_start", "index": i,
                                                     "content_block": {"type": "text", "text": ""}}))
            out.append(event("content_block_delta", {"type": "content_block_delta", "index": i,
                                                     "delta": {"type": "text_delta", "text": block["text"]}}))
        out.append(event("content_block_stop", {"type": "content_block_stop", "index": i}))
    out.append(event("message_delta", {"type": "message_delta", "delta": {"stop_reason": message["stop_reason"],
                                                                          "stop_sequence": None},
                                       "usage": {"output_tokens": message["usage"]["output_tokens"]}}))
    out.append(event("message_stop", {"type": "message_stop"}))
    return "".join(out).encode("utf-8")


class FakeUpstream:
    def __init__(self, script: list[dict[str, Any]], usage: dict[str, int] | None = None) -> None:
        self.script = script
        self.usage = usage
        self.requests: list[dict[str, Any]] = []
        self.port = 0
        self._server: ThreadingHTTPServer | None = None

    def start(self) -> int:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _send(self, status: int, payload: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self) -> None:  # noqa: N802
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                try:
                    body = json.loads(raw)
                except ValueError:
                    self._send(400, b'{"type":"error","error":{"type":"invalid_request_error","message":"bad json"}}',
                               "application/json")
                    return
                outer.requests.append({"path": self.path, "body": body, "host": self.headers.get("Host")})
                if self.path.startswith("/v1/messages/count_tokens"):
                    self._send(200, json.dumps({"input_tokens": len(raw) // 4}).encode(), "application/json")
                    return
                step = _step(body, outer.script)
                if "status" in step:
                    kind = ERRORS.get(step["status"], "api_error")
                    error = {"type": "error", "error": {"type": kind, "message": f"fake {kind}"}}
                    self._send(step["status"], json.dumps(error).encode(), "application/json")
                    return
                n = sum(1 for m in body.get("messages") or [] if isinstance(m, dict) and m.get("role") == "assistant")
                usage = dict(outer.usage or {"input_tokens": max(1, len(raw) // 4), "output_tokens": 20})
                message = _message(step, n, usage, str(body.get("model", "fake")))
                if body.get("stream"):
                    self._send(200, _sse(message), "text/event-stream")
                else:
                    self._send(200, json.dumps(message).encode(), "application/json")

            def log_message(self, format: str, *args) -> None:  # noqa: A002
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self.port = self._server.server_address[1]
        return self.port

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
