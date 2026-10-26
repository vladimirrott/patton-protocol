"""The fleet runs' recording proxy: every model call a harness or its gate makes goes through here, is charged, and
is kept.

One Recorder serves one run on 127.0.0.1. A harness speaks OpenAI chat completions to it (`/v1/chat/completions`,
or `/chat/completions`), or Anthropic Messages (`/v1/messages`, for Claude Code), and the gate inside the container
sends its analyst and Verifier calls to `/t1/v1/chat/completions`. Each request is forwarded without streaming, so
the reply always carries its usage, and is sent back to the harness as it asked: one JSON reply, or Server-Sent
Events (chat.completion chunks in the shape `harness/fake_chat.py` uses, which every port's trace accepted, or
Anthropic's message events). A Messages request goes to the upstream's `/v1/messages`, which LiteLLM translates
for Groq; `/v1/messages/count_tokens` is answered here with an estimate and never forwarded.

Money: before each forward the Meter holds the request's worst case (prompt characters / 2 at the input price,
plus max_tokens at the output price; a request without max_tokens gets MAX_OUT), against the run set's ledger cap
and the lab-wide cap over every ledger (patton_lab/spend.py). A call that could pass either is never sent: the
harness gets a 503 and the run is marked stopped. A 429 waits for its retry-after and is sent again, so the harness
never sees it; a 5xx or a lost connection is retried twice, then the harness gets a 502 and the run is marked
failed. The model name the harness sends is replaced by the run's model, so a harness cannot pick another one.

The cassette holds every exchange: the body as forwarded, the status, the reply and each attempt's usage and cost.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from ..triage import runner as triage

MAX_OUT = 16384
MAX_429 = 20
MODELS = triage.MODELS
SpendStop = triage.SpendStop


def bound(model: str, body: dict[str, Any]) -> float:
    _, price_in, _, price_out = MODELS[model]
    chars = len(json.dumps(body.get("messages", []))) + len(json.dumps(body.get("tools", [])))
    out = int(body.get("max_tokens") or body.get("max_completion_tokens") or MAX_OUT)
    return (chars / 2 * price_in + out * price_out) / 1e6


def _anthropic_events(message: dict[str, Any]) -> bytes:
    """A finished Anthropic message as the event stream Claude Code asked for. Text, thinking and tool_use blocks
    are sent; any other block type is left out."""
    def event(name: str, data: dict[str, Any]) -> str:
        return f"event: {name}\ndata: {json.dumps(data)}\n\n"

    usage = message.get("usage") or {}
    head = {**message, "content": [], "stop_reason": None,
            "usage": {"input_tokens": int(usage.get("input_tokens") or 0), "output_tokens": 1}}
    out = [event("message_start", {"type": "message_start", "message": head})]
    blocks = [b for b in message.get("content") or [] if b.get("type") in ("text", "thinking", "tool_use")]
    for i, block in enumerate(blocks):
        if block["type"] == "tool_use":
            start, delta = {**block, "input": {}}, {"type": "input_json_delta",
                                                    "partial_json": json.dumps(block.get("input") or {})}
        elif block["type"] == "thinking":
            start, delta = {"type": "thinking", "thinking": "", "signature": ""}, {
                "type": "thinking_delta", "thinking": block.get("thinking", "")}
        else:
            start, delta = {"type": "text", "text": ""}, {"type": "text_delta", "text": block.get("text", "")}
        out.append(event("content_block_start", {"type": "content_block_start", "index": i, "content_block": start}))
        out.append(event("content_block_delta", {"type": "content_block_delta", "index": i, "delta": delta}))
        if block["type"] == "thinking" and block.get("signature"):
            out.append(event("content_block_delta", {"type": "content_block_delta", "index": i,
                                                     "delta": {"type": "signature_delta",
                                                               "signature": block["signature"]}}))
        out.append(event("content_block_stop", {"type": "content_block_stop", "index": i}))
    out.append(event("message_delta", {"type": "message_delta",
                                       "delta": {"stop_reason": message.get("stop_reason") or "end_turn",
                                                 "stop_sequence": None},
                                       "usage": {"output_tokens": int(usage.get("output_tokens") or 0)}}))
    out.append(event("message_stop", {"type": "message_stop"}))
    return "".join(out).encode("utf-8")


def _post_messages(url: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any] | None, str, float | None]:
    """triage.post, for an Anthropic Messages upstream: (status, JSON body or None, error text, retry-after)."""
    import urllib.error
    import urllib.request
    request = urllib.request.Request(url + "/v1/messages", data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "x-api-key": "local",
                                              "anthropic-version": "2023-06-01"})
    try:
        with urllib.request.urlopen(request, timeout=triage.TIMEOUT_S) as response:
            return response.status, json.loads(response.read()), "", None
    except urllib.error.HTTPError as err:
        text = err.read()[:2000].decode("utf-8", "replace")
        after = err.headers.get("retry-after") if err.headers else None
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        return err.code, data, text, float(after) if after and after.replace(".", "", 1).isdigit() else None
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as err:
        return 0, None, f"{type(err).__name__}: {err}", None


def _anthropic_usage(usage: dict[str, Any] | None) -> dict[str, Any] | None:
    """Anthropic usage in the chat-completions form the price table reads; cache reads count as cached input."""
    if not isinstance(usage, dict) or not isinstance(usage.get("input_tokens"), int):
        return None
    cached = int(usage.get("cache_read_input_tokens") or 0)
    created = int(usage.get("cache_creation_input_tokens") or 0)
    return {"prompt_tokens": usage["input_tokens"] + cached + created, "completion_tokens":
            int(usage.get("output_tokens") or 0), "prompt_tokens_details": {"cached_tokens": cached}}


# How Groq marks a refusal of the model's own output: a malformed tool call (`tool_use_failed`), output it could not
# parse or validate, each with the output in `failed_generation`. The model made it, a person on Groq meets it too,
# and the harness decides what to do next, so the run goes on; any other refused request stops the run.
MODEL_FAULTS = ("tool_use_failed", "failed_generation")
# A model fault is drawn again this many times before the harness sees it, as a client retrying a bad sample would.
# The pilots passed the first one on: 48 of 112 P1 runs and 18 of 144 H1 runs ended early, most at such a fault.
MODEL_FAULT_RETRIES = 2


def _groq_turn(message: Any) -> Any:
    """An assistant turn with its reasoning under `reasoning`, the name Groq takes back. Cline, OpenCode and goose
    send it as `reasoning_content`, which Groq refuses with a 400; pi, oh-my-pi and dsh already send `reasoning`."""
    if not (isinstance(message, dict) and message.get("role") == "assistant" and "reasoning_content" in message):
        return message
    out = {k: v for k, v in message.items() if k != "reasoning_content"}
    if out.get("reasoning") is None and message["reasoning_content"] is not None:
        out["reasoning"] = message["reasoning_content"]
    return out


def _chunks(data: dict[str, Any]) -> bytes:
    """A finished chat.completion as the stream a harness asked for."""
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    base = {"id": data.get("id", "chatcmpl"), "object": "chat.completion.chunk", "created": data.get("created", 0),
            "model": data.get("model", "")}
    delta: dict[str, Any] = {"role": "assistant"}
    for key, value in message.items():
        if key not in ("role", "tool_calls") and value is not None:
            delta[key] = value
    if message.get("tool_calls"):
        delta["tool_calls"] = [{"index": i, "id": c.get("id"), "type": "function",
                                "function": {"name": (c.get("function") or {}).get("name"),
                                             "arguments": (c.get("function") or {}).get("arguments") or ""}}
                               for i, c in enumerate(message["tool_calls"])]
    events = [{**base, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
              {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": choice.get("finish_reason") or "stop"}],
               "usage": data.get("usage")}]
    return ("".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n").encode("utf-8")


class Recorder:
    def __init__(self, worker_url: str, meter, run_id: str, model: str, analyst_url: str | None = None,
                 analyst_model: str = "gpt-oss-120b", sleep=time.sleep) -> None:
        if model not in MODELS or analyst_model not in MODELS:
            raise ValueError(f"unknown model {model!r} or {analyst_model!r}")
        self.urls = {"worker": worker_url.rstrip("/"), "t1": (analyst_url or worker_url).rstrip("/")}
        self.models = {"worker": model, "t1": analyst_model}
        self.meter, self.run_id, self.sleep = meter, run_id, sleep
        self.calls: list[dict[str, Any]] = []
        self.stopped: str | None = None  # the spend cap, or an upstream that kept failing
        self.lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None

    @property
    def usd(self) -> float:
        return round(sum(c["usd"] for c in self.calls), 8)

    def forward(self, route: str, body: dict[str, Any], wire: str = "chat") -> tuple[int, dict[str, Any]]:
        """Send one request upstream under the meter. Returns (status, JSON for the harness). `wire` is "chat" or
        "anthropic"."""
        model = self.models[route]
        body = {**body, "model": model}
        if wire == "chat" and isinstance(body.get("messages"), list):
            body["messages"] = [_groq_turn(m) for m in body["messages"]]
        stream = bool(body.pop("stream", False))
        body.pop("stream_options", None)
        for key in ("max_tokens", "max_completion_tokens"):
            if key in body:
                body[key] = min(int(body[key] or MAX_OUT), MAX_OUT)
        if "max_tokens" not in body and "max_completion_tokens" not in body:
            body["max_tokens"] = MAX_OUT
        worst = bound(model, body)
        with self.lock:
            n = len(self.calls)
            record: dict[str, Any] = {"i": n, "route": route, "wire": wire, "stream": stream, "request": body,
                                      "attempts": [], "status": None, "response": None, "usd": 0.0}
            self.calls.append(record)
        failures = waits = redraws = 0
        while True:
            if self.stopped:
                record["status"] = 503
                return 503, {"error": {"message": f"the run was stopped: {self.stopped}"}}
            try:
                self.meter.reserve(worst)
            except SpendStop as err:
                self.stopped = str(err)
                record["status"] = 503
                return 503, {"error": {"message": f"the lab's spend cap stops this run: {err}"}}
            started = time.time()
            post = _post_messages if wire == "anthropic" else triage.post
            status, data, error, after = post(self.urls[route], body)
            usage = (data or {}).get("usage") if status == 200 else None
            if wire == "anthropic":
                usage = _anthropic_usage(usage)
            cost = triage.usd(model, usage)
            if status == 200 and cost is None:
                cost = worst  # a success without usage is charged its worst case
            self.meter.settle(worst, f"{self.run_id}/c{n}/a{len(record['attempts'])}", cost or 0.0, usage)
            record["attempts"].append({"status": status, "wall_s": round(time.time() - started, 3),
                                       "usd": round(cost or 0.0, 8), "usage": usage, "error": error[:500]})
            record["usd"] = round(record["usd"] + (cost or 0.0), 8)
            if status == 429 and waits < MAX_429:
                waits += 1
                self.sleep(min(after or 5.0, 60.0))
                continue
            if (status == 0 or status >= 500) and failures < 2:
                failures += 1
                self.sleep(5.0 * failures)
                continue
            if 400 <= status < 500 and any(mark in error for mark in MODEL_FAULTS) and redraws < MODEL_FAULT_RETRIES:
                redraws += 1
                continue
            break
        record["status"], record["response"] = status, data
        if status != 200:
            if status == 0 or status >= 500 or status == 429:
                self.stopped = f"upstream {status}: {error[:200]}"
                return 502, {"error": {"message": f"upstream failed ({status})"}}
            if not any(mark in error for mark in MODEL_FAULTS):
                # a request the provider refuses is the lab's fault, so the run measures nothing and is not kept
                self.stopped = f"upstream refused the request ({status}): {error[:200]}"
            return status, data or {"error": {"message": error[:500]}}
        return 200, data

    def start(self, host: str = "127.0.0.1") -> int:
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, code: int, payload: bytes, kind: str = "application/json") -> None:
                self.send_response(code)
                self.send_header("content-type", kind)
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self) -> None:  # noqa: N802
                if self.path.rstrip("/").endswith("/models"):
                    model = recorder.models["worker"]
                    body = {"object": "list", "data": [{"id": model, "object": "model", "owned_by": "lab"}]}
                    return self._send(200, json.dumps(body).encode("utf-8"))
                self._send(404, b"{}")

            def do_POST(self) -> None:  # noqa: N802
                raw = self.rfile.read(int(self.headers.get("content-length") or 0))
                route = "t1" if self.path.startswith("/t1/") else "worker"
                path = self.path.split("?")[0].rstrip("/")
                wire = "anthropic" if path.endswith("/v1/messages") and route == "worker" else "chat"
                if path.endswith("/v1/messages/count_tokens"):
                    estimate = {"input_tokens": max(1, len(raw) // 4)}
                    return self._send(200, json.dumps(estimate).encode("utf-8"))
                if wire == "chat" and not path.endswith("/chat/completions"):
                    return self._send(404, b'{"error": {"message": "only chat completions and messages are served"}}')
                try:
                    body = json.loads(raw or b"{}")
                    if not isinstance(body, dict):
                        raise ValueError("not an object")
                except ValueError:
                    return self._send(400, b'{"error": {"message": "the body is not a JSON object"}}')
                stream = bool(body.get("stream"))
                status, data = recorder.forward(route, body, wire)
                if status == 200 and stream:
                    events = _anthropic_events(data) if wire == "anthropic" else _chunks(data)
                    return self._send(200, events, "text/event-stream")
                self._send(status, json.dumps(data).encode("utf-8"))

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
