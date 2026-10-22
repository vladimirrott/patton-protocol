"""The cassette for the gate study: a recording proxy between the worker and the model endpoint.

The worker's Claude Code sends its model calls to the recorder, which forwards each one to the upstream (the
LiteLLM proxy for Groq) and streams the answer back byte for byte while it keeps a copy. After each call it
appends one entry to a cassette in the lab's format (patton_lab.cassette): the request, the status, and the
response assembled from its stream, with the usage the endpoint reported.

Kept out of the cassette: every request header (they carry the credential), provider reasoning text (only its
length is kept, as everywhere in the lab), thinking signatures and `cache_control` markers. The host user
name, which `ls -l` prints because the worker runs under the host's uid, is replaced by `worker` before
anything is digested or stored. The system prompt and
the tool list repeat in every call, so each is stored once per digest under `blobs/`, and a request whose
messages extend the previous request's stores only the messages it adds. `load` rebuilds every request and
checks it against its digest, so a cassette that lost an entry or a blob does not load.

An entry that would hold a secret is not written; its sequence number goes to `leaks` and the runner fails the
run on it.
"""

from __future__ import annotations

import getpass
import gzip
import hashlib
import http.client
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..cassette import Cassette, canonical_json, request_key

HOP_BY_HOP = {"connection", "keep-alive", "transfer-encoding", "content-length", "host", "accept-encoding", "te",
              "trailer", "upgrade", "proxy-connection"}
UPSTREAM_TIMEOUT_S = 600
ERROR_TEXT_LIMIT = 4000
_THINKING = {"thinking": "thinking", "redacted_thinking": "data"}


def _strip_cache_control(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_cache_control(v) for k, v in value.items() if k != "cache_control"}
    if isinstance(value, list):
        return [_strip_cache_control(v) for v in value]
    return value


def _block(block: Any) -> Any:
    if isinstance(block, dict) and block.get("type") in _THINKING:
        return {"type": block["type"], "chars": len(str(block.get(_THINKING[block["type"]]) or ""))}
    return block


def normalize(body: dict[str, Any]) -> dict[str, Any]:
    """The request as the cassette keeps it: no cache markers, thinking blocks reduced to their length."""
    body = _strip_cache_control(body)
    messages = []
    for message in body.get("messages") or []:
        if isinstance(message, dict) and isinstance(message.get("content"), list):
            message = {**message, "content": [_block(b) for b in message["content"]]}
        messages.append(message)
    if "messages" in body:
        body = {**body, "messages": messages}
    return body


def _scrub(value: Any) -> Any:
    user = getpass.getuser()
    if not user or value is None:
        return value
    return json.loads(json.dumps(value).replace(user, "worker"))


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _assemble_stream(text: str) -> dict[str, Any]:
    """Rebuild the message from an Anthropic event stream. Tool input that is not valid JSON is kept as text."""
    message: dict[str, Any] = {"content": [], "usage": {}}
    partial: dict[int, str] = {}
    for chunk in text.split("\n\n"):
        data = "".join(line[5:].strip() for line in chunk.splitlines() if line.startswith("data:"))
        if not data:
            continue
        try:
            event = json.loads(data)
        except ValueError:
            message.setdefault("unparsed", 0)
            message["unparsed"] += 1
            continue
        kind = event.get("type")
        if kind == "message_start":
            start = event.get("message") or {}
            message.update({k: start.get(k) for k in ("id", "model", "role")})
            message["usage"].update(start.get("usage") or {})
        elif kind == "content_block_start":
            block = dict(event.get("content_block") or {})
            if block.get("type") in _THINKING:
                block = {"type": block["type"], "chars": 0}
            elif block.get("type") == "tool_use":
                partial[event.get("index", len(message["content"]))] = ""
            message["content"].append(block)
        elif kind == "content_block_delta":
            index, delta = event.get("index", -1), event.get("delta") or {}
            if not 0 <= index < len(message["content"]):
                continue
            block = message["content"][index]
            if delta.get("type") == "text_delta":
                block["text"] = block.get("text", "") + str(delta.get("text", ""))
            elif delta.get("type") == "input_json_delta":
                partial[index] = partial.get(index, "") + str(delta.get("partial_json", ""))
            elif delta.get("type") == "thinking_delta":
                block["chars"] = block.get("chars", 0) + len(str(delta.get("thinking", "")))
        elif kind == "content_block_stop":
            index = event.get("index", -1)
            if index in partial and 0 <= index < len(message["content"]):
                raw = partial.pop(index)
                try:
                    message["content"][index]["input"] = json.loads(raw) if raw else {}
                except ValueError:
                    message["content"][index]["input_raw"] = raw
        elif kind == "message_delta":
            message.update({k: v for k, v in (event.get("delta") or {}).items() if k in ("stop_reason", "stop_sequence")})
            message["usage"].update(event.get("usage") or {})
        elif kind == "error":
            message["error"] = event.get("error")
    for index, raw in partial.items():  # a stream cut before content_block_stop
        if 0 <= index < len(message["content"]):
            message["content"][index]["input_raw"] = raw
    return message


def _assemble(status: int, content_type: str, raw: bytes) -> tuple[dict[str, Any] | None, str | None]:
    text = raw.decode("utf-8", errors="replace")
    if status != 200:
        return None, text[:ERROR_TEXT_LIMIT]
    if "text/event-stream" in content_type:
        return _assemble_stream(text), None
    try:
        message = json.loads(text)
    except ValueError:
        return None, text[:ERROR_TEXT_LIMIT]
    if isinstance(message, dict) and isinstance(message.get("content"), list):
        message = {**message, "content": [_block(b) for b in message["content"]]}
    return message, None


def usage_tokens(response: dict[str, Any] | None) -> tuple[int, int] | None:
    usage = (response or {}).get("usage") or {}
    if "input_tokens" not in usage or "output_tokens" not in usage:
        return None
    tokens_in = sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    return tokens_in, int(usage.get("output_tokens") or 0)


class Recorder:
    """Forwards to `upstream` and records into `cassette_path`. One recorder per attempt; calls run one at a time."""

    def __init__(self, upstream: str, cassette_path: Path | str, secrets: list[str] | tuple[str, ...] = ()) -> None:
        parts = urlsplit(upstream)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError(f"upstream must be an http(s) URL, not {upstream!r}")
        self.scheme, self.host = parts.scheme, parts.hostname
        self.upstream_port = parts.port or (443 if parts.scheme == "https" else 80)
        self.base_path = parts.path.rstrip("/")
        self.path = Path(cassette_path)
        self.blobs = self.path.parent / "blobs"
        self.secrets = [s for s in secrets if s]
        self.calls: list[dict[str, Any]] = []
        self.leaks: list[int] = []
        self._cassette = Cassette(self.path, "record")
        self._lock = threading.Lock()
        self._previous: list[Any] | None = None
        self._server: ThreadingHTTPServer | None = None

    # -- storage ------------------------------------------------------------------------------------------

    def _blob(self, value: Any) -> str:
        digest = _digest(value)
        target = self.blobs / f"{digest}.json.gz"
        if not target.exists():
            self.blobs.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".tmp")
            tmp.write_bytes(gzip.compress(canonical_json(value).encode("utf-8"), mtime=0))
            os.replace(tmp, target)
        return digest

    def _leaks(self, text: str) -> bool:
        return any(secret in text for secret in self.secrets)

    def _store(self, seq: int, method: str, path: str, request: Any, status: int, response: dict[str, Any] | None,
               error: str | None, size: int, wall_s: float) -> None:
        stored: Any = request
        sha = None
        if isinstance(request, dict):
            sha = _digest(request)
            stored = {k: v for k, v in request.items() if k not in ("system", "tools", "messages")}
            for field in ("system", "tools"):
                if field in request:
                    stored[f"{field}_blob"] = request[field]
            messages = request.get("messages")
            if isinstance(messages, list):
                previous = self._previous
                if previous is not None and len(messages) >= len(previous) and messages[:len(previous)] == previous:
                    stored.update(base=self._previous_seq, append=messages[len(previous):])
                else:
                    stored["messages"] = messages
        entry = {"seq": seq, "method": method, "path": path, "status": status, "request": stored, "request_sha": sha,
                 "response": response, "error": error, "response_bytes": size, "wall_s": wall_s}
        if self._leaks(canonical_json(entry)):
            self.leaks.append(seq)
            return
        for field in ("system", "tools"):
            if isinstance(stored, dict) and f"{field}_blob" in stored:
                stored[f"{field}_blob"] = self._blob(stored[f"{field}_blob"])
        self._cassette.put(request_key({"seq": seq, "sha": sha}), entry)
        if isinstance(request, dict) and isinstance(request.get("messages"), list):
            self._previous, self._previous_seq = request["messages"], seq

    # -- serving ------------------------------------------------------------------------------------------

    def _connect(self) -> http.client.HTTPConnection:
        if self.scheme == "https":
            return http.client.HTTPSConnection(self.host, self.upstream_port, timeout=UPSTREAM_TIMEOUT_S)
        return http.client.HTTPConnection(self.host, self.upstream_port, timeout=UPSTREAM_TIMEOUT_S)

    def start(self) -> int:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _chunk(self, data: bytes) -> None:
                self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                self.wfile.flush()

            def _relay(self, method: str) -> None:
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))
                headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_BY_HOP}
                headers["Accept-Encoding"] = "identity"
                started = time.monotonic()
                kept = bytearray()
                status, content_type, streaming, client_gone = 502, "application/json", False, False
                try:
                    conn = outer._connect()
                    conn.request(method, outer.base_path + self.path, raw or None, headers)
                    upstream = conn.getresponse()
                    status, content_type = upstream.status, upstream.getheader("Content-Type", "")
                    self.send_response(status)
                    for key, value in upstream.getheaders():
                        if key.lower() not in HOP_BY_HOP:
                            self.send_header(key, value)
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    streaming = True
                    while True:
                        data = upstream.read1(65536)
                        if not data:
                            break
                        kept += data
                        if not client_gone:
                            try:
                                self._chunk(data)
                            except (BrokenPipeError, ConnectionResetError):
                                client_gone = True  # keep reading: the endpoint bills the whole answer
                    conn.close()
                except OSError as err:
                    if not streaming:
                        status = 502
                        kept = bytearray(json.dumps({"type": "error", "error": {
                            "type": "api_error", "message": f"recorder: upstream failed ({type(err).__name__})"}}).encode())
                try:
                    request: Any = normalize(json.loads(raw)) if raw else None
                except ValueError:
                    request = {"unparsed_bytes": len(raw)}
                response, error = _assemble(status, content_type, bytes(kept))
                request, response, error = _scrub(request), _scrub(response), _scrub(error)
                with outer._lock:  # store before the worker sees the end of the answer
                    seq = len(outer.calls)
                    tokens = usage_tokens(response)
                    outer.calls.append({"seq": seq, "path": self.path, "status": status,
                                        "input_tokens": tokens[0] if tokens else None,
                                        "output_tokens": tokens[1] if tokens else None,
                                        "stop_reason": (response or {}).get("stop_reason")})
                    outer._store(seq, method, self.path, request, status, response, error, len(kept),
                                 round(time.monotonic() - started, 2))
                try:
                    if streaming:
                        self.wfile.write(b"0\r\n\r\n")
                    else:
                        self.send_response(502)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(kept)))
                        self.end_headers()
                        self.wfile.write(bytes(kept))
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def do_POST(self) -> None:  # noqa: N802
                self._relay("POST")

            def do_GET(self) -> None:  # noqa: N802
                self._relay("GET")

            def log_message(self, format: str, *args) -> None:  # noqa: A002
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self._server.server_address[1]

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def usage(self) -> tuple[int, int, int]:
        """(input tokens, output tokens, calls whose usage is unknown) over every call so far."""
        known = [c for c in self.calls if c["input_tokens"] is not None]
        return (sum(c["input_tokens"] for c in known), sum(c["output_tokens"] for c in known),
                len(self.calls) - len(known))


# ---- reading a cassette back ------------------------------------------------------------------------------

def _read_blob(blobs: Path, digest: str) -> Any:
    target = blobs / f"{digest}.json.gz"
    if not target.exists():
        raise ValueError(f"blob {digest[:12]} is missing")
    # No digest check here: a changed blob changes the rebuilt request, and `load` checks that digest.
    return json.loads(gzip.decompress(target.read_bytes()))


def load(path: Path | str) -> list[dict[str, Any]]:
    """Every entry in call order, each request rebuilt in full and checked against its digest."""
    path = Path(path)
    cassette = Cassette(path, "replay")
    found = [cassette.get(key) for key in list(cassette._entries)]
    entries = sorted((e for e in found if e is not None), key=lambda e: e["seq"])
    if [e["seq"] for e in entries] != list(range(len(entries))):
        raise ValueError(f"{path.name}: call numbers are not 0..{len(entries) - 1}; an entry is missing")
    messages_at: dict[int, list[Any]] = {}
    out = []
    for entry in entries:
        stored = entry["request"]
        request = stored
        if isinstance(stored, dict) and entry.get("request_sha"):
            request = {k: v for k, v in stored.items() if k not in ("system_blob", "tools_blob", "base", "append")}
            for field in ("system", "tools"):
                if f"{field}_blob" in stored:
                    request[field] = _read_blob(path.parent / "blobs", stored[f"{field}_blob"])
            if "base" in stored:
                if stored["base"] not in messages_at:
                    raise ValueError(f"{path.name}: call {entry['seq']} extends call {stored['base']}, which is missing")
                request["messages"] = messages_at[stored["base"]] + stored["append"]
            if _digest(request) != entry["request_sha"]:
                raise ValueError(f"{path.name}: call {entry['seq']} does not rebuild to its recorded digest")
            if isinstance(request.get("messages"), list):
                messages_at[entry["seq"]] = request["messages"]
        out.append({**entry, "request": request})
    return out


def tool_uses(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The tool calls the endpoint returned, in call order, as the runner stores them."""
    calls = []
    for entry in entries:
        for block in (entry.get("response") or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                calls.append({"id": block.get("id", ""), "name": block.get("name", ""),
                              "input": block.get("input", block.get("input_raw"))})
    return calls
