"""A scripted Anthropic Messages endpoint that tells conversations apart, for end-to-end runs with subagents.

The main session and each subagent are separate conversations on the same endpoint. A conversation is picked by
the first script key that appears in its first user message (the main prompt, or the prompt the main agent gave the
subagent), and step n of that script answers a request that already holds n assistant turns, so the server keeps no
state between calls. A step is a list of tool calls, sent in one assistant message so Claude Code runs them
together, {"calls": [...], "delay": seconds} to answer late, or {"text": ...}. A request without tools, an unknown conversation, or one past the end of its script
gets a short text answer.
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import time
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lab"))
from patton_lab.cc.fake_upstream import _sse  # noqa: E402


def _first_user_text(body: dict[str, Any]) -> str:
    for message in body.get("messages") or []:
        if isinstance(message, dict) and message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def pick(body: dict[str, Any], scripts: dict[str, list]) -> tuple[str, dict[str, Any] | list]:
    if not body.get("tools"):
        return "", {"text": "ok"}
    first = _first_user_text(body)
    for key, script in scripts.items():
        if key in first:
            n = sum(1 for m in body.get("messages") or [] if isinstance(m, dict) and m.get("role") == "assistant")
            return key, script[n] if n < len(script) else {"text": "Finished."}
    return "", {"text": "Finished."}


def message(step, n: int, key: str, output_tokens: int) -> dict[str, Any]:
    if isinstance(step, dict) and "calls" in step:
        step = step["calls"]
    if isinstance(step, dict):
        content = [{"type": "text", "text": step.get("text", "")}]
    else:
        content = [{"type": "tool_use", "id": f"toolu_{key}_{n}_{i}", "name": call["tool"], "input": call["input"]}
                   for i, call in enumerate(step)]
    return {"id": f"msg_{key}_{n}", "type": "message", "role": "assistant", "model": "fake", "content": content,
            "stop_reason": "end_turn" if isinstance(step, dict) else "tool_use", "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": output_tokens}}


class FakeAgents:
    def __init__(self, scripts: dict[str, list], output_tokens: int = 20) -> None:
        self.scripts, self.output_tokens = scripts, output_tokens
        self.requests: list[dict[str, Any]] = []
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
                    self._send(400, b"{}", "application/json")
                    return
                if self.path.startswith("/v1/messages/count_tokens"):
                    self._send(200, json.dumps({"input_tokens": len(raw) // 4}).encode(), "application/json")
                    return
                key, step = pick(body, outer.scripts)
                n = sum(1 for m in body.get("messages") or [] if isinstance(m, dict) and m.get("role") == "assistant")
                outer.requests.append({"key": key, "n": n})
                if isinstance(step, dict) and step.get("delay"):
                    time.sleep(float(step["delay"]))
                reply = message(step, n, key or "x", outer.output_tokens)
                if body.get("stream"):
                    self._send(200, _sse(reply), "text/event-stream")
                else:
                    self._send(200, json.dumps(reply).encode(), "application/json")

            def log_message(self, *args) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self._server.server_address[1]

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
