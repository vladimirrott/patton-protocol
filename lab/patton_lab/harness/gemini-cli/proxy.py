"""A Gemini API to chat-completions translating proxy, so Gemini CLI can run against any OpenAI-compatible model.

    proxy = GeminiProxy("https://api.example/openai/v1", "some-model", api_key=key)
    port = proxy.start()      # point GOOGLE_GEMINI_BASE_URL at http://127.0.0.1:<port>
    ...
    proxy.stop()

Standard library only. Gemini CLI v0.65.0 speaks only the Gemini wire format; each of its model calls becomes one
non-streaming `POST {upstream}/chat/completions`, and the reply is translated back.

Routes, under any prefix (the CLI uses /v1beta):

- POST .../models/<m>:generateContent: one JSON GenerateContentResponse.
- POST .../models/<m>:streamGenerateContent (`alt=sse`): the same response as ONE Server-Sent Events chunk
  (`data: {...}` and a blank line, the shape `fake_gemini.py` proved the CLI accepts). Upstream is never streamed:
  the proxy knows the whole answer, or the error, before it writes a byte, so an error keeps its HTTP status.
- POST .../models/<m>:countTokens: an estimate, ceil(characters / 4) of the JSON of contents, systemInstruction
  and tools, without calling upstream.
- GET .../models and .../models/<m>: one model listed. Anything else is a Gemini-style 404.

The model named in the CLI's path is ignored; `model` is what goes upstream.

Request translation. systemInstruction (a string, a Content or a list of parts) becomes one system message. A
"user" content becomes a user message of its text parts; its functionResponse parts become tool messages. A
"model" content becomes an assistant message: its non-thought text as `content`, each functionCall as a
tool_call with `arguments` the JSON of `args`. Thought parts (`thought: true`) are dropped from history, as the
Gemini API does with its own thoughts. A functionResponse's `response` becomes the tool message's content: the
bare string when it is `{"output": "<str>"}`, its JSON otherwise (so `{"error": ...}` stays visibly an error).
`tools[].functionDeclarations` become `tools` in OpenAI function form; the schema comes from
`parametersJsonSchema` (JSON Schema, which v0.65.0 sends) or `parameters` (the older OpenAPI-style Schema with
upper-case types such as "OBJECT"), both converted by `json_schema`. generationConfig: maxOutputTokens to
max_tokens, temperature, topP to top_p, stopSequences to stop. toolConfig.functionCallingConfig mode AUTO / ANY /
NONE to tool_choice auto / required / none (ANY with one allowed name forces that function).

Tool-call ids. The upstream's tool_call ids are passed to the CLI as `functionCall.id`. The CLI keeps an id it is
given (v0.65.0 echoes it on the functionResponse; newer sources prefix it as `<name>__<id>`), so on the way back
an id is mapped by: (1) the id itself, or with a `<name>__` prefix removed, when it is one the proxy issued;
(2) otherwise the CLI's own id, shortened to `call_<sha1[:20]>` when it is longer than 40 characters (a limit
some upstreams enforce); (3) a call with no id at all gets `call_<content index>_<part index>`, stable across
requests. A functionResponse is matched to a call of the model content just before it: by id first, then by
name in order (the first unanswered call of that name: what a CLI that sends only the name needs), then the
first unanswered call. A call left without a response gets a tool message saying no result was recorded, and a
response that matches no call becomes user text, so the upstream always sees well-formed tool history.

Response translation. Assistant text becomes a text part, a `reasoning` / `reasoning_content` field a thought
part, and each tool_call a functionCall part with `args` parsed from its JSON string. Empty or "null" arguments
become `{}` (gpt-oss sends empty arguments for no-argument tools). Arguments that do not parse to a JSON object
turn the whole candidate into finishReason MALFORMED_FUNCTION_CALL with a finishMessage naming the tool and the
error, and no functionCall: the CLI shows it and retries, as it does for the real API's malformed calls; the
proxy never crashes on it. finish_reason stop / tool_calls to STOP, length to MAX_TOKENS, content_filter to
SAFETY, anything else to OTHER, missing to STOP. `usage` to usageMetadata (promptTokenCount,
candidatesTokenCount, totalTokenCount, plus thoughtsTokenCount and cachedContentTokenCount when the upstream
reports reasoning or cached tokens).

Errors come back as a Gemini error body `{"error": {"code", "message", "status"}}`, before any SSE byte: an
upstream 4xx/5xx keeps its status (429 RESOURCE_EXHAUSTED with a RetryInfo detail and "Please retry in Ns" from
Retry-After, so the CLI's quota classifier retries it; 5xx stays retryable; 400/401/403/404 stop the CLI); a
refused or failed connection is 502, a timeout 504, an unparseable or shapeless upstream body 502. Every
upstream call has a timeout, so nothing hangs.

`requests` holds every chat body sent upstream, `received` every Gemini body the CLI sent (with "_path"), and
`untranslated` the sorted set of request fields the proxy saw and did not translate (for example
"generationConfig.thinkingConfig"), so a trace can report them.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import socket
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

MAX_ID = 40
TIMEOUT = 180.0
_ROUTE = re.compile(r"/models/([^/:]+):([A-Za-z]+)$")
_STATUS = {400: "INVALID_ARGUMENT", 401: "UNAUTHENTICATED", 403: "PERMISSION_DENIED", 404: "NOT_FOUND",
           409: "ABORTED", 413: "INVALID_ARGUMENT", 422: "INVALID_ARGUMENT", 429: "RESOURCE_EXHAUSTED",
           499: "CANCELLED", 500: "INTERNAL", 501: "UNIMPLEMENTED", 502: "UNAVAILABLE", 503: "UNAVAILABLE",
           504: "DEADLINE_EXCEEDED"}
_FINISH = {"stop": "STOP", "tool_calls": "STOP", "function_call": "STOP", "length": "MAX_TOKENS",
           "content_filter": "SAFETY"}
# Gemini Schema fields that JSON Schema spells the same way; everything else in an old-style Schema is dropped.
_KEEP = {"description", "enum", "required", "minimum", "maximum", "pattern", "title", "default", "format",
         "minItems", "maxItems", "minLength", "maxLength", "minProperties", "maxProperties", "const",
         "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "uniqueItems", "examples", "$ref", "$id",
         "contentEncoding", "contentMediaType", "readOnly", "writeOnly", "deprecated"}
_INTS = {"minItems", "maxItems", "minLength", "maxLength", "minProperties", "maxProperties"}
_SUB_MAP = ("properties", "$defs", "definitions", "patternProperties")
_SUB_LIST = ("anyOf", "oneOf", "allOf", "prefixItems")
_SUB_ONE = ("items", "not", "additionalProperties", "if", "then", "else", "contains", "propertyNames")
_GEN_DONE = {"maxOutputTokens", "temperature", "topP", "stopSequences"}


class ProxyError(Exception):
    """An error to hand the CLI as a Gemini error body with this HTTP status."""

    def __init__(self, code: int, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.retry_after = code, message, retry_after


def json_schema(schema: Any) -> Any:
    """A Gemini parameter schema (JSON Schema, or the OpenAPI-style Schema with "OBJECT"/"STRING" types and
    `nullable`) as JSON Schema: types lower-cased, `nullable: true` as a "null" type, integer fields given as
    strings made ints, `format: "enum"` and Gemini-only fields (propertyOrdering, example, $schema) dropped.
    Property names are never read as keywords: only the values under `properties` are converted."""
    if not isinstance(schema, dict):
        return schema
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "type":
            if isinstance(value, list):
                out["type"] = [v.lower() if isinstance(v, str) else v for v in value]
            elif isinstance(value, str):
                out["type"] = value.lower()
            if out.get("type") in ("type_unspecified", ""):
                del out["type"]
        elif key in _SUB_MAP and isinstance(value, dict):
            out[key] = {k: json_schema(v) for k, v in value.items()}
        elif key in _SUB_LIST and isinstance(value, list):
            out[key] = [json_schema(v) for v in value]
        elif key in _SUB_ONE:
            out[key] = json_schema(value) if isinstance(value, dict) else value
        elif key in _INTS and isinstance(value, str) and value.isdigit():
            out[key] = int(value)
        elif key == "format" and value == "enum":
            continue
        elif key in ("minimum", "maximum") and isinstance(value, str):
            try:
                out[key] = float(value) if "." in value else int(value)
            except ValueError:
                continue
        elif key in _KEEP:
            out[key] = value
    if schema.get("nullable") is True and "type" in out:
        t = out["type"]
        out["type"] = (t if "null" in t else [*t, "null"]) if isinstance(t, list) else [t, "null"]
    if "type" not in out and "properties" in out:
        out["type"] = "object"
    return out


def _parameters(decl: dict[str, Any]) -> dict[str, Any]:
    raw = decl.get("parametersJsonSchema")
    if raw is None:
        raw = decl.get("parameters")
    schema = json_schema(raw) if isinstance(raw, dict) else {}
    if not schema:
        return {"type": "object", "properties": {}}
    if schema.get("type") == "object" and "properties" not in schema:
        schema["properties"] = {}
    return schema


def _text(value: Any) -> str:
    """The text of a systemInstruction: a string, a Content ({parts}), a part, or a list of any of those."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n\n".join(t for t in (_text(v) for v in value) if t)
    if isinstance(value, dict):
        if "parts" in value:
            return _text(value["parts"])
        if isinstance(value.get("text"), str) and not value.get("thought"):
            return value["text"]
    return ""


def _short(cid: str) -> str:
    return cid if len(cid) <= MAX_ID else "call_" + hashlib.sha1(cid.encode("utf-8")).hexdigest()[:20]


def _tool_content(response: Any) -> str:
    if isinstance(response, dict) and set(response) == {"output"} and isinstance(response["output"], str):
        return response["output"]
    if isinstance(response, str):
        return response
    return json.dumps(response, ensure_ascii=False)


def gemini_error(code: int, message: str, retry_after: float | None = None) -> dict[str, Any]:
    status = _STATUS.get(code, "INTERNAL" if code >= 500 else "INVALID_ARGUMENT")
    err: dict[str, Any] = {"code": code, "message": message, "status": status}
    if code == 429 and retry_after is not None:
        secs = max(0.0, retry_after)
        err["message"] = f"{message} Please retry in {secs:g}s."
        err["details"] = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": f"{secs:g}s"}]
    return {"error": err}


class GeminiProxy:
    def __init__(self, upstream_url: str, model: str, api_key: str | None = None, timeout: float = TIMEOUT,
                 extra_body: dict[str, Any] | None = None) -> None:
        url = upstream_url.rstrip("/")
        self.url = url if url.endswith("/chat/completions") else url + "/chat/completions"
        self.model = model
        self._key = api_key  # sent as a bearer header only; never stored in `requests`
        self.timeout = timeout
        self.extra_body = dict(extra_body or {})
        self.requests: list[dict[str, Any]] = []
        self.received: list[dict[str, Any]] = []
        self._untranslated: set[str] = set()
        self._issued: set[str] = set()
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @property
    def untranslated(self) -> list[str]:
        with self._lock:
            return sorted(self._untranslated)

    def _skip(self, what: str) -> None:
        with self._lock:
            self._untranslated.add(what)

    # ---- Gemini request -> chat body ------------------------------------------------------------------------

    def _upstream_id(self, call: dict[str, Any], name: str, ci: int, pi: int) -> str:
        cid = call.get("id")
        if not isinstance(cid, str) or not cid:
            return f"call_{ci}_{pi}"
        with self._lock:
            if cid in self._issued:
                return cid
            prefix = f"{name}__"
            if cid.startswith(prefix) and cid[len(prefix):] in self._issued:
                return cid[len(prefix):]
        return _short(cid)

    def chat_body(self, body: dict[str, Any]) -> dict[str, Any]:
        """The chat-completions body for one Gemini generateContent body (see the module docstring)."""
        messages: list[dict[str, Any]] = []
        system = _text(body.get("systemInstruction"))
        if system:
            messages.append({"role": "system", "content": system})
        pending: list[dict[str, Any]] = []  # calls of the last model content: {"id", "name", "answered"}

        def flush() -> None:
            for call in pending:
                if not call["answered"]:
                    messages.append({"role": "tool", "tool_call_id": call["id"],
                                     "content": json.dumps({"error": "no result was recorded for this call"})})
                    call["answered"] = True
            pending.clear()

        for ci, content in enumerate(body.get("contents") or []):
            if not isinstance(content, dict):
                continue
            role = content.get("role") or "user"
            parts = [p for p in content.get("parts") or [] if isinstance(p, dict)]
            if role == "model":
                flush()
                texts, calls = [], []
                for pi, part in enumerate(parts):
                    self._note_part(part)
                    if part.get("thought"):
                        self._skip("contents.parts.thought (dropped from history)")
                        continue
                    if isinstance(part.get("text"), str):
                        texts.append(part["text"])
                    fc = part.get("functionCall")
                    if isinstance(fc, dict):
                        name = str(fc.get("name") or "")
                        uid = self._upstream_id(fc, name, ci, pi)
                        calls.append({"id": uid, "type": "function",
                                      "function": {"name": name,
                                                   "arguments": json.dumps(fc.get("args") or {}, ensure_ascii=False)}})
                        cli_id = fc.get("id")
                        pending.append({"id": uid, "name": name, "answered": False,
                                        "cli_ids": {cli_id, f"{name}__{cli_id}"} if cli_id else set()})
                text = "".join(texts)
                if not calls and not text:
                    continue
                msg: dict[str, Any] = {"role": "assistant", "content": text or None}
                if calls:
                    msg["tool_calls"] = calls
                messages.append(msg)
                continue
            texts, orphans = [], []
            for part in parts:
                self._note_part(part)
                if part.get("thought"):
                    continue
                if isinstance(part.get("text"), str):
                    texts.append(part["text"])
                fr = part.get("functionResponse")
                if isinstance(fr, dict):
                    call = self._match(pending, fr)
                    content_text = _tool_content(fr.get("response"))
                    if fr.get("parts"):
                        self._skip("functionResponse.parts")
                    if call is None:
                        orphans.append(f"[result of {fr.get('name')}] {content_text}")
                    else:
                        call["answered"] = True
                        messages.append({"role": "tool", "tool_call_id": call["id"], "content": content_text})
            if texts or orphans:
                flush()
                chunks = orphans + (["".join(texts)] if texts else [])
                messages.append({"role": "user", "content": "\n\n".join(chunks)})
        flush()

        out: dict[str, Any] = {"model": self.model, "messages": messages, "stream": False}
        tools = []
        for tool in body.get("tools") or []:
            if not isinstance(tool, dict):
                continue
            for key in tool:
                if key != "functionDeclarations":
                    self._skip(f"tools.{key}")
            for decl in tool.get("functionDeclarations") or []:
                fn: dict[str, Any] = {"name": decl.get("name"), "parameters": _parameters(decl)}
                if decl.get("description"):
                    fn["description"] = decl["description"]
                for key in decl:
                    if key not in ("name", "description", "parameters", "parametersJsonSchema"):
                        self._skip(f"functionDeclarations.{key}")
                tools.append({"type": "function", "function": fn})
        if tools:
            out["tools"] = tools
            choice = self._tool_choice(body.get("toolConfig"))
            if choice is not None:
                out["tool_choice"] = choice
        gen = body.get("generationConfig") or {}
        if isinstance(gen, dict):
            if gen.get("maxOutputTokens") is not None:
                out["max_tokens"] = gen["maxOutputTokens"]
            if gen.get("temperature") is not None:
                out["temperature"] = gen["temperature"]
            if gen.get("topP") is not None:
                out["top_p"] = gen["topP"]
            if gen.get("stopSequences"):
                out["stop"] = gen["stopSequences"]
            for key in gen:
                if key not in _GEN_DONE:
                    self._skip(f"generationConfig.{key}")
        for key in body:
            if key not in ("contents", "systemInstruction", "tools", "toolConfig", "generationConfig", "model"):
                self._skip(key)
        out.update(self.extra_body)
        return out

    def _note_part(self, part: dict[str, Any]) -> None:
        for key in part:
            if key not in ("text", "thought", "functionCall", "functionResponse", "thoughtSignature"):
                self._skip(f"contents.parts.{key}")
        if "thoughtSignature" in part:
            self._skip("contents.parts.thoughtSignature")

    @staticmethod
    def _match(pending: list[dict[str, Any]], fr: dict[str, Any]) -> dict[str, Any] | None:
        open_calls = [c for c in pending if not c["answered"]]
        rid, name = fr.get("id"), fr.get("name")
        if rid:
            for c in open_calls:
                if rid in c["cli_ids"] or rid == c["id"]:
                    return c
        for c in open_calls:
            if c["name"] == name:
                return c
        return open_calls[0] if open_calls else None

    def _tool_choice(self, config: Any) -> Any:
        fcc = (config or {}).get("functionCallingConfig") if isinstance(config, dict) else None
        if not isinstance(fcc, dict):
            return None
        mode = str(fcc.get("mode") or "").upper()
        names = fcc.get("allowedFunctionNames") or []
        if mode == "NONE":
            return "none"
        if mode in ("ANY", "VALIDATED"):
            if len(names) == 1:
                return {"type": "function", "function": {"name": names[0]}}
            if names:
                self._skip("toolConfig.functionCallingConfig.allowedFunctionNames")
            return "required"
        if mode == "AUTO":
            return "auto"
        return None

    # ---- chat reply -> Gemini response ----------------------------------------------------------------------

    def gemini_response(self, reply: Any) -> dict[str, Any]:
        """The GenerateContentResponse for one chat.completion; raises ProxyError(502) on a shapeless reply."""
        choices = reply.get("choices") if isinstance(reply, dict) else None
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ProxyError(502, f"upstream reply has no choices: {json.dumps(reply)[:300]}")
        choice = choices[0]
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        parts: list[dict[str, Any]] = []
        reasoning = message.get("reasoning") or message.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning:
            parts.append({"text": reasoning, "thought": True})
        content = message.get("content")
        if isinstance(content, list):  # some upstreams send content parts
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        if isinstance(content, str) and content:
            parts.append({"text": content})
        finish = _FINISH.get(choice.get("finish_reason"), "OTHER") if choice.get("finish_reason") else "STOP"
        bad: list[str] = []
        calls: list[dict[str, Any]] = []
        for n, tc in enumerate(message.get("tool_calls") or []):
            fn = tc.get("function") if isinstance(tc, dict) else None
            if not isinstance(fn, dict):
                bad.append(f"tool call {n} has no function")
                continue
            name, raw = str(fn.get("name") or ""), fn.get("arguments")
            try:
                if isinstance(raw, dict):
                    args = raw
                elif raw is None or (isinstance(raw, str) and raw.strip() in ("", "null")):
                    args = {}
                else:
                    args = json.loads(raw)
                if not isinstance(args, dict):
                    raise ValueError(f"arguments are a JSON {type(args).__name__}, not an object")
            except ValueError as err:
                bad.append(f"{name or 'unnamed tool'}: {err}; arguments: {str(raw)[:200]}")
                continue
            cid = tc.get("id") if isinstance(tc.get("id"), str) and tc.get("id") else None
            call: dict[str, Any] = {"name": name, "args": args}
            if cid:
                call["id"] = cid
                with self._lock:
                    self._issued.add(cid)
            calls.append({"functionCall": call})
        candidate: dict[str, Any] = {"content": {"role": "model", "parts": parts}, "index": 0}
        if bad:
            candidate["finishReason"] = "MALFORMED_FUNCTION_CALL"
            candidate["finishMessage"] = "Malformed function call: " + " | ".join(bad)
        else:
            parts.extend(calls)
            candidate["finishReason"] = finish
        out: dict[str, Any] = {"candidates": [candidate], "modelVersion": str(reply.get("model") or self.model)}
        if reply.get("id"):
            out["responseId"] = str(reply["id"])
        usage = reply.get("usage")
        if isinstance(usage, dict):
            prompt = int(usage.get("prompt_tokens") or 0)
            completion = int(usage.get("completion_tokens") or 0)
            meta = {"promptTokenCount": prompt, "candidatesTokenCount": completion,
                    "totalTokenCount": int(usage.get("total_tokens") or prompt + completion)}
            details = usage.get("completion_tokens_details") or {}
            if isinstance(details, dict) and details.get("reasoning_tokens"):
                meta["thoughtsTokenCount"] = int(details["reasoning_tokens"])
            cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") \
                if isinstance(usage.get("prompt_tokens_details"), dict) else None
            if cached:
                meta["cachedContentTokenCount"] = int(cached)
            out["usageMetadata"] = meta
        return out

    # ---- upstream -------------------------------------------------------------------------------------------

    def _call(self, chat: dict[str, Any]) -> Any:
        with self._lock:
            self.requests.append(chat)
        headers = {"content-type": "application/json", "accept": "application/json"}
        if self._key:
            headers["authorization"] = f"Bearer {self._key}"
        req = urllib.request.Request(self.url, data=json.dumps(chat).encode("utf-8"), headers=headers,
                                     method="POST")
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as err:
            text = err.read().decode("utf-8", "replace")
            try:
                detail = json.loads(text).get("error", {})
                msg = detail.get("message") if isinstance(detail, dict) else str(detail)
            except (ValueError, AttributeError):
                msg = None
            retry = None
            if err.code == 429:
                try:
                    retry = float(err.headers.get("retry-after") or "")
                except ValueError:
                    retry = None
            code = err.code if 400 <= err.code < 600 else 502
            raise ProxyError(code, f"upstream HTTP {err.code}: {(msg or text)[:500]}", retry) from None
        except (TimeoutError, socket.timeout) as err:
            raise ProxyError(504, f"upstream timed out after {self.timeout:g}s: {err}") from None
        except urllib.error.URLError as err:
            if isinstance(err.reason, (TimeoutError, socket.timeout)):
                raise ProxyError(504, f"upstream timed out after {self.timeout:g}s") from None
            raise ProxyError(502, f"upstream unreachable: {err.reason}") from None
        except (OSError, ValueError) as err:  # reset mid-read, bad status line
            raise ProxyError(502, f"upstream connection failed: {type(err).__name__}: {err}") from None
        try:
            return json.loads(raw)
        except ValueError:
            raise ProxyError(502, f"upstream sent a body that is not JSON: {raw[:200]!r}") from None

    def generate(self, body: dict[str, Any]) -> dict[str, Any]:
        """One Gemini generateContent body in, one GenerateContentResponse out (ProxyError on failure)."""
        return self.gemini_response(self._call(self.chat_body(body)))

    @staticmethod
    def count_tokens(body: dict[str, Any]) -> int:
        inner = body.get("generateContentRequest") if isinstance(body.get("generateContentRequest"), dict) else {}
        payload = {k: (body.get(k) if body.get(k) is not None else inner.get(k))
                   for k in ("contents", "systemInstruction", "tools")}
        chars = len(json.dumps({k: v for k, v in payload.items() if v is not None}, ensure_ascii=False))
        return math.ceil(chars / 4)

    # ---- server ---------------------------------------------------------------------------------------------

    def start(self, host: str = "127.0.0.1") -> int:
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, code: int, payload: bytes, kind: str = "application/json") -> None:
                self.send_response(code)
                self.send_header("content-type", kind)
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def _error(self, err: ProxyError) -> None:
                self._send(err.code, json.dumps(gemini_error(err.code, err.message, err.retry_after)).encode())

            def do_GET(self) -> None:  # noqa: N802
                path = self.path.split("?")[0].rstrip("/")
                entry = {"supportedGenerationMethods": ["generateContent", "streamGenerateContent", "countTokens"],
                         "inputTokenLimit": 131072, "outputTokenLimit": 65536}
                if path.endswith("/models"):
                    body: Any = {"models": [{"name": f"models/{proxy.model}", **entry}]}
                    return self._send(200, json.dumps(body).encode())
                m = re.search(r"/models/([^/:]+)$", path)
                if m:
                    return self._send(200, json.dumps({"name": f"models/{m.group(1)}", **entry}).encode())
                proxy._skip(f"route GET {path}")
                self._error(ProxyError(404, f"no route for GET {path}"))

            def do_POST(self) -> None:  # noqa: N802
                path = self.path.split("?")[0]
                raw = self.rfile.read(int(self.headers.get("content-length") or 0))
                try:
                    body = json.loads(raw or b"{}")
                    if not isinstance(body, dict):
                        raise ValueError("not an object")
                except ValueError as err:
                    return self._error(ProxyError(400, f"request body is not a JSON object: {err}"))
                with proxy._lock:
                    proxy.received.append({**body, "_path": path})
                m = _ROUTE.search(path)
                method = m.group(2) if m else ""
                if method == "countTokens":
                    return self._send(200, json.dumps({"totalTokens": proxy.count_tokens(body)}).encode())
                if method not in ("generateContent", "streamGenerateContent"):
                    proxy._skip(f"route POST {path}")
                    return self._error(ProxyError(404, f"no route for POST {path}"))
                try:
                    out = proxy.generate(body)
                except ProxyError as err:
                    return self._error(err)
                except Exception as err:  # noqa: BLE001 - a translation bug is an error answer, never a hang
                    return self._error(ProxyError(500, f"proxy failed: {type(err).__name__}: {err}"))
                if method == "streamGenerateContent":
                    return self._send(200, f"data: {json.dumps(out)}\r\n\r\n".encode(), "text/event-stream")
                self._send(200, json.dumps(out).encode())

            def log_message(self, format: str, *args) -> None:  # noqa: A002
                return

        self._server = ThreadingHTTPServer((host, 0), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self._server.server_address[1]

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
