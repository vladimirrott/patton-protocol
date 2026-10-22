"""The Codex worker's only way to its model: a loopback proxy that relays Responses API calls and records them.

The worker's Codex points a custom provider at this proxy. Each POST is forwarded to the upstream (the ChatGPT
Codex backend for subscription runs, a scripted endpoint for `trace`) and streamed back as it arrives. When the
upstream needs a login, `auth` supplies the headers on every call, so the container holds only a placeholder key.

Each call becomes one cassette entry: the request, the output items and usage the stream reported, and the
rate-limit headers, which give the share of the 5-hour and weekly windows used. The cassette is gzip JSONL. Request
parts that repeat from call to call (instructions, tools, earlier input items) are stored once as blobs and
referenced by digest. Reasoning text and encrypted reasoning are reduced to their length, and the host user name
becomes "worker". An entry that would hold a secret is not written; its sequence number goes to `leaks` and the
runner refuses the run.
"""

from __future__ import annotations

import base64
import getpass
import gzip
import hashlib
import http.client
import json
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

HOP_HEADERS = {"host", "content-length", "connection", "transfer-encoding", "accept-encoding", "keep-alive",
               "proxy-connection", "te", "upgrade"}
RATE_SUFFIXES = ("-used-percent", "-window-minutes", "-reset-at", "-limit-name")
DROPPED_REQUEST_KEYS = ("client_metadata",)  # ids of the installation and the turn, nothing the model reads
BLOB_KEYS = ("instructions", "tools")
CHATGPT_UPSTREAM = "https://chatgpt.com"


class TokenExpiring(Exception):
    """The host's Codex login cannot carry a run: it is not a ChatGPT login, or its token expires too soon."""


def _jwt_exp(token: str) -> float | None:
    try:
        part = token.split(".")[1]
        return float(json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))["exp"])
    except (IndexError, ValueError, KeyError, TypeError):
        return None


class ChatGPTAuth:
    """The host's ChatGPT login, read from auth.json on every call so a refresh on the host is picked up."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path or Path.home() / ".codex" / "auth.json")

    def _read(self) -> dict[str, Any]:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def __call__(self) -> dict[str, str]:
        tokens = self._read().get("tokens") or {}
        return {"authorization": f"Bearer {tokens['access_token']}", "chatgpt-account-id": str(tokens["account_id"])}

    def secrets(self) -> list[str]:
        data = self._read()
        found = [v for v in (data.get("tokens") or {}).values() if isinstance(v, str)]
        if isinstance(data.get("OPENAI_API_KEY"), str):
            found.append(data["OPENAI_API_KEY"])
        return [s for s in found if len(s) >= 8]

    def check(self, min_seconds: float = 900) -> None:
        data = self._read()
        if data.get("auth_mode") != "chatgpt" or not (data.get("tokens") or {}).get("access_token"):
            raise TokenExpiring(f"{self.path} holds no ChatGPT login; sign in with `codex login` first")
        exp = _jwt_exp(data["tokens"]["access_token"])
        left = (exp - time.time()) if exp is not None else -1
        if left < min_seconds:
            raise TokenExpiring(f"the Codex login token expires in {max(left, 0):.0f} s; run `codex login` in your "
                                "own terminal to refresh it, then start again")


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()


def _scrub(value: Any) -> Any:
    """Reduce reasoning text and encrypted reasoning to their length."""
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if not isinstance(value, dict):
        return value
    out = {}
    for key, item in value.items():
        if key == "encrypted_content" and isinstance(item, str):
            out[key] = {"chars": len(item)}
        elif value.get("type") == "reasoning" and key in ("summary", "content") and isinstance(item, list):
            out[key] = [{"type": p.get("type"), "chars": len(p.get("text") or "")} if isinstance(p, dict) else p
                        for p in item]
        else:
            out[key] = _scrub(item)
    return out


def parse_sse(text: str) -> list[dict[str, Any]]:
    events = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        data = "\n".join(line[5:].lstrip() for line in block.splitlines() if line.startswith("data:"))
        if not data or data == "[DONE]":
            continue
        try:
            event = json.loads(data)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def summarize(status: int, content_type: str, raw: bytes) -> dict[str, Any]:
    """The response as stored: output items, usage and errors from the stream, or the error body."""
    text = raw.decode("utf-8", errors="replace")
    # The ChatGPT Codex backend streams SSE under another content type, so the body decides too.
    streamed = "event-stream" in content_type or text.lstrip().startswith(("event:", "data:"))
    if status != 200 or not streamed:
        try:
            return {"error": json.loads(text)}
        except ValueError:
            return {"error": text[:2000]}
    events = parse_sse(text)
    output = [e["item"] for e in events if e.get("type") == "response.output_item.done" and isinstance(e.get("item"), dict)]
    final = next((e.get("response") or {} for e in reversed(events)
                  if e.get("type") in ("response.completed", "response.failed", "response.incomplete")), {})
    summary: dict[str, Any] = {"output": output, "usage": final.get("usage"),
                               "event_types": sorted({str(e.get("type")) for e in events})}
    if final.get("error"):
        summary["error"] = final["error"]
    rate_events = [e for e in events if "rate_limit" in str(e.get("type"))]
    if rate_events:
        summary["rate_limit_events"] = rate_events
    return summary


def usage_tokens(response: dict[str, Any] | None) -> dict[str, int | None]:
    usage = (response or {}).get("usage") or {}
    if not usage:
        return {"input_tokens": None, "cached_tokens": None, "output_tokens": None, "reasoning_tokens": None}
    return {"input_tokens": int(usage.get("input_tokens") or 0),
            "cached_tokens": int((usage.get("input_tokens_details") or {}).get("cached_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
            "reasoning_tokens": int((usage.get("output_tokens_details") or {}).get("reasoning_tokens") or 0)}


class Recorder:
    def __init__(self, upstream: str, cassette_path: Path | str, secrets: list[str] | tuple[str, ...] = (),
                 auth: Callable[[], dict[str, str]] | None = None, user: str | None = None) -> None:
        self.upstream = urllib.parse.urlsplit(upstream)
        self.cassette_path = Path(cassette_path)
        self.secrets = [s for s in secrets if s]
        self.auth = auth
        self.user = user if user is not None else getpass.getuser()
        self.calls: list[dict[str, Any]] = []
        self.leaks: list[int] = []
        self._seq = 0
        self._lock = threading.Lock()
        self._blobs: set[str] = set()
        self._server: ThreadingHTTPServer | None = None

    # ---- storage --------------------------------------------------------------------------------------

    def _clean(self, value: Any) -> Any:
        text = json.dumps(_scrub(value))
        if self.user and len(self.user) > 2:
            text = text.replace(self.user, "worker")
        return json.loads(text)

    def _request(self, body: Any, new_blobs: dict[str, Any]) -> Any:
        if not isinstance(body, dict):
            return body
        stored: dict[str, Any] = {}
        for key, value in body.items():
            if key in DROPPED_REQUEST_KEYS:
                stored[key] = {"dropped": True}
            elif key in BLOB_KEYS or key == "input":
                parts = value if key == "input" and isinstance(value, list) else [value]
                refs = []
                for part in parts:
                    digest = _digest(part)
                    if digest not in self._blobs:
                        new_blobs[digest] = part
                    refs.append({"$blob": digest})
                stored[key] = refs if key == "input" and isinstance(value, list) else refs[0]
            else:
                stored[key] = value
        return stored

    def _store(self, seq: int, method: str, path: str, body: Any, status: int, response: dict[str, Any],
               rate_limits: dict[str, str]) -> None:
        with self._lock:
            new_blobs: dict[str, Any] = {}
            entry = {"seq": seq, "method": method, "path": path, "status": status,
                     "request": self._request(self._clean(body), new_blobs), "response": self._clean(response),
                     "rate_limits": rate_limits}
            lines = [json.dumps({"blob": d, "value": v}) for d, v in new_blobs.items()] + [json.dumps(entry)]
            text = "\n".join(lines) + "\n"
            if any(secret in text for secret in self.secrets):
                self.leaks.append(seq)
                return
            self.cassette_path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(self.cassette_path, "at", encoding="utf-8") as handle:
                handle.write(text)
            self._blobs.update(new_blobs)

    # ---- relay ----------------------------------------------------------------------------------------

    def _connect(self) -> http.client.HTTPConnection:
        host, port = self.upstream.hostname or "127.0.0.1", self.upstream.port
        if self.upstream.scheme == "https":
            return http.client.HTTPSConnection(host, port or 443, timeout=600)
        return http.client.HTTPConnection(host, port or 80, timeout=600)

    def start(self) -> int:
        rec = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:  # noqa: N802
                raw = self.rfile.read(int(self.headers.get("content-length") or 0))
                with rec._lock:
                    seq = rec._seq
                    rec._seq += 1
                headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_HEADERS}
                headers["accept-encoding"] = "identity"
                if rec.auth is not None:
                    headers = {k: v for k, v in headers.items() if k.lower() not in ("authorization", "chatgpt-account-id")}
                    headers.update(rec.auth())
                target = (rec.upstream.path.rstrip("/") + self.path) if rec.upstream.path else self.path
                status, content_type, data, rate = 502, "application/json", b"", {}
                try:
                    conn = rec._connect()
                    conn.request("POST", target, raw, headers)
                    resp = conn.getresponse()
                    status, content_type = resp.status, resp.getheader("content-type") or ""
                    rate = {k.lower(): v for k, v in resp.getheaders()
                            if k.lower().startswith("x-codex-") and (k.lower().endswith(RATE_SUFFIXES) or "credits" in k.lower())}
                    self.send_response(status)
                    for key, value in resp.getheaders():
                        if key.lower() not in HOP_HEADERS and key.lower() != "content-encoding":
                            self.send_header(key, value)
                    self.send_header("transfer-encoding", "chunked")
                    self.end_headers()
                    chunks = []
                    while True:
                        chunk = resp.read1(65536)
                        if not chunk:
                            break
                        chunks.append(chunk)
                        self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                        self.wfile.flush()
                    self.wfile.write(b"0\r\n\r\n")
                    data = b"".join(chunks)
                    conn.close()
                except (OSError, http.client.HTTPException) as err:
                    data = json.dumps({"error": f"recorder could not reach the upstream: {type(err).__name__}"}).encode()
                    try:
                        self.send_response(502)
                        self.send_header("content-type", "application/json")
                        self.send_header("content-length", str(len(data)))
                        self.end_headers()
                        self.wfile.write(data)
                    except OSError:
                        pass
                response = summarize(status, content_type, data)
                try:
                    body = json.loads(raw or b"{}")
                except ValueError:
                    body = {"unreadable_bytes": len(raw)}
                with rec._lock:
                    rec.calls.append({"status": status, **usage_tokens(response)})
                rec._store(seq, "POST", self.path, body, status, response, rate)

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


# ---- reading cassettes -------------------------------------------------------------------------------

def _resolve(value: Any, blobs: dict[str, Any]) -> Any:
    if isinstance(value, dict) and set(value) == {"$blob"}:
        return blobs[value["$blob"]]
    if isinstance(value, list):
        return [_resolve(v, blobs) for v in value]
    return value


def load(path: Path | str) -> list[dict[str, Any]]:
    """Every entry of a cassette in call order, with its request rebuilt from the blobs."""
    blobs: dict[str, Any] = {}
    entries = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            if "blob" in item and "value" in item:
                blobs[item["blob"]] = item["value"]
                continue
            request = item.get("request")
            if isinstance(request, dict):
                item["request"] = {k: _resolve(v, blobs) for k, v in request.items()}
            entries.append(item)
    return sorted(entries, key=lambda e: e["seq"])


def model_calls(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The tool calls the model made, in order, as the cassette holds them."""
    calls = []
    for entry in entries:
        for item in (entry.get("response") or {}).get("output") or []:
            if item.get("type") == "custom_tool_call":
                calls.append({"call_id": item.get("call_id"), "name": item.get("name"), "input": item.get("input")})
            elif item.get("type") == "function_call":
                calls.append({"call_id": item.get("call_id"), "name": item.get("name"), "input": item.get("arguments")})
    return calls
