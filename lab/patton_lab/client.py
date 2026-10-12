"""The only module that talks to a model provider.

Every call goes through `Client.chat`, which keys the request, serves it from
the cassette when it can, asks the meter before spending, and records the
response. Replay mode never reaches `HttpTransport`.

Spend is charged conservatively. A failed attempt that may already have been
billed (a timeout, a dropped connection, an unreadable body) is charged at the
call's bound. A response without a usable `usage` block is charged at the
bound too. The ledger can overstate the bill; it should never understate it.

A provider can reject a malformed tool call the model produced (Groq answers
400 `tool_use_failed`). That is model behaviour, not an outage: the client
returns it as a completion with `tool_use_failed`, the cassette records it, and
the agent loop tells the model its call could not be parsed.

The key comes from an environment variable named by the provider. It goes
into the Authorization header and nowhere else, and key-shaped text is removed
from every error message.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from .cassette import Cassette, CassetteMiss, request_key
from .cost import Meter, call_bound, estimate_tokens

MAX_RATE_LIMIT_WAITS = 5
SERVER_RETRY_WAITS = (2.0, 4.0, 8.0)
DEFAULT_RATE_LIMIT_WAIT = 2.0
REQUEST_TIMEOUT_S = 180
FAILED_GENERATION_LIMIT = 2000
MODEL_OUTPUT_FAILURES = frozenset({"tool_use_failed", "output_parse_failed", "json_validate_failed"})


class AuthError(Exception):
    """The provider rejected the key, or no key is configured."""


class QuotaError(Exception):
    """The key has no credit left."""


class RateLimitError(Exception):
    """The provider kept rate-limiting after every allowed wait."""


class ProviderError(Exception):
    """Any other failure from the provider."""


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    key_env: str
    supports_reasoning_effort: bool


PROVIDERS: dict[str, Provider] = {
    "groq": Provider("groq", "https://api.groq.com/openai/v1", "PATTON_LAB_GROQ_KEY", True),
    "openai": Provider("openai", "https://api.openai.com/v1", "PATTON_LAB_OPENAI_KEY", False),
}


@dataclass
class Completion:
    message: dict[str, Any]
    usage: dict[str, Any]
    model: str
    finish_reason: str
    from_cassette: bool


@dataclass
class PostResult:
    body: dict[str, Any] | None
    headers: dict[str, str]
    uncertain_attempts: int
    tool_use_failed: dict[str, str] | None = None


_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|s|m|h)")
_KEYISH = re.compile(r"(sk-|gsk_|ghp_)[A-Za-z0-9*_\-]{3,}|(api key provided:\s*)\S+", re.IGNORECASE)


def parse_duration(text: str | None) -> float | None:
    """Parse `89ms`, `7.66s`, `1m2.5s`, or bare seconds. Unknown text gives None."""
    if not text:
        return None
    text = text.strip()
    try:
        return float(text)
    except ValueError:
        pass
    parts = _DURATION_PART.findall(text)
    if not parts or "".join(n + u for n, u in parts) != text:
        return None
    scale = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}
    return sum(float(number) * scale[unit] for number, unit in parts)


def redact(text: str) -> str:
    return _KEYISH.sub(lambda m: (m.group(2) or "") + "[redacted]", text)


def _is_quota_error(error: dict[str, Any]) -> bool:
    """Read only the provider's own fields: `failed_generation` is model text and must not decide this."""
    fields = " ".join(str(error.get(key, "")) for key in ("code", "type", "message")).lower()
    return "insufficient_quota" in fields or ("quota" in fields and "exceeded" in fields)


def _error_object(body_text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(body_text)
    except json.JSONDecodeError:
        return {}
    error = parsed.get("error", parsed) if isinstance(parsed, dict) else {}
    return error if isinstance(error, dict) else {}


def _error_excerpt(body_text: str) -> str:
    error = _error_object(body_text)
    text = str(error.get("message") or error.get("code") or body_text)
    return redact(text)[:300]


_TRANSIENT = (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException, ssl.SSLError)


class HttpTransport:
    """POST to `<base_url>/chat/completions` with retries and token-window pacing."""

    def __init__(
        self,
        provider: Provider,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = REQUEST_TIMEOUT_S,
    ) -> None:
        key = os.environ.get(provider.key_env, "").strip()
        if not key:
            raise AuthError(f"environment variable {provider.key_env} is not set")
        self.provider = provider
        self._key = key
        self._sleep = sleep
        self._timeout = timeout
        self._remaining_tokens: int | None = None
        self._reset_seconds: float | None = None
        #: Attempts of the current post() that may have been billed without a usable reply.
        #: The client reads it after post() returns or raises, so none goes uncharged.
        self.uncertain_attempts = 0

    def _pace(self, estimated_tokens: int) -> None:
        if self._remaining_tokens is None or self._reset_seconds is None:
            return
        if estimated_tokens > self._remaining_tokens:
            self._sleep(self._reset_seconds)

    def _note_window(self, headers: dict[str, str]) -> None:
        remaining = headers.get("x-ratelimit-remaining-tokens")
        try:
            self._remaining_tokens = int(remaining) if remaining is not None else None
        except ValueError:
            self._remaining_tokens = None
        self._reset_seconds = parse_duration(headers.get("x-ratelimit-reset-tokens"))

    def _retry_or_raise(self, failures: int, reason: str) -> int:
        if failures >= len(SERVER_RETRY_WAITS):
            raise ProviderError(f"{self.provider.name}: {reason} after {failures} retries")
        self._sleep(SERVER_RETRY_WAITS[failures])
        return failures + 1

    def post(self, payload: dict[str, Any], estimated_tokens: int) -> PostResult:
        data = json.dumps(payload).encode("utf-8")
        url = self.provider.base_url.rstrip("/") + "/chat/completions"
        rate_waits = 0
        failures = 0
        self.uncertain_attempts = 0
        self._pace(estimated_tokens)
        while True:
            request = urllib.request.Request(
                url,
                data=data,
                method="POST",
                headers={
                    "Authorization": f"Bearer {self._key}",
                    "Content-Type": "application/json",
                    "User-Agent": "patton-lab/1",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=self._timeout) as response:
                    headers = {k.lower(): v for k, v in response.headers.items()}
                    raw = response.read()
                self._note_window(headers)
                try:
                    body = json.loads(raw.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    self.uncertain_attempts += 1
                    failures = self._retry_or_raise(failures, "a 200 response with an unreadable body")
                    continue
                if not isinstance(body, dict):
                    self.uncertain_attempts += 1
                    failures = self._retry_or_raise(failures, "a 200 response that is not a JSON object")
                    continue
                return PostResult(body, headers, self.uncertain_attempts)
            except urllib.error.HTTPError as exc:
                headers = {k.lower(): v for k, v in (exc.headers or {}).items()}
                body_text = exc.read().decode("utf-8", errors="replace")
                status = exc.code
                if status in (401, 403):
                    raise AuthError(f"{self.provider.name} rejected the key (HTTP {status}): {_error_excerpt(body_text)}") from None
                error = _error_object(body_text)
                if status == 400 and (error.get("code") in MODEL_OUTPUT_FAILURES or "failed_generation" in error):
                    # The model produced output the provider could not parse (a malformed tool call or
                    # unparseable text). That is the model's behaviour, recorded as a turn, not an outage.
                    return PostResult(None, headers, self.uncertain_attempts, {
                        "code": str(error.get("code", "")),
                        "message": redact(str(error.get("message", "")))[:300],
                        "failed_generation": redact(str(error.get("failed_generation", "")))[:FAILED_GENERATION_LIMIT],
                    })
                if status == 402 or _is_quota_error(error):
                    raise QuotaError(f"{self.provider.name} reports no credit (HTTP {status}): {_error_excerpt(body_text)}") from None
                if status == 429:
                    if rate_waits >= MAX_RATE_LIMIT_WAITS:
                        raise RateLimitError(f"{self.provider.name} rate-limited after {rate_waits} waits") from None
                    wait = (
                        parse_duration(headers.get("retry-after"))
                        or parse_duration(headers.get("x-ratelimit-reset-tokens"))
                        or parse_duration(headers.get("x-ratelimit-reset-requests"))
                        or DEFAULT_RATE_LIMIT_WAIT
                    )
                    rate_waits += 1
                    self._sleep(wait)
                    continue
                if 500 <= status <= 599:
                    # A gateway error can arrive after the model generated (and billed) a reply.
                    self.uncertain_attempts += 1
                    failures = self._retry_or_raise(failures, f"HTTP {status}: {_error_excerpt(body_text)}")
                    continue
                raise ProviderError(f"{self.provider.name} HTTP {status}: {_error_excerpt(body_text)}") from None
            except _TRANSIENT as exc:
                # The provider may have generated (and billed) a reply that never arrived.
                self.uncertain_attempts += 1
                failures = self._retry_or_raise(failures, f"transport error {type(exc).__name__}")


def clean_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copy of `messages` without the provider-specific `reasoning` field."""
    cleaned = []
    for message in messages:
        if "reasoning" in message:
            message = {k: v for k, v in message.items() if k != "reasoning"}
        cleaned.append(message)
    return cleaned


def _usable_usage(usage: Any) -> bool:
    if not isinstance(usage, dict):
        return False
    for key in ("prompt_tokens", "completion_tokens"):
        value = usage.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return False
    return usage["prompt_tokens"] > 0  # every real request has a prompt; zero means the count is missing


class Client:
    def __init__(self, cassette: Cassette, transports: dict[str, Any], meter: Meter) -> None:
        self.cassette = cassette
        self.transports = transports
        self.meter = meter

    def chat(
        self,
        provider: Provider,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        salt: str,
        max_tokens: int = 2000,
        temperature: float = 1.0,
        reasoning_effort: str = "medium",
    ) -> Completion:
        payload: dict[str, Any] = {
            "model": model,
            "messages": clean_history(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if provider.supports_reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort

        key = request_key({"provider": provider.name, "payload": payload, "salt": salt})
        hit = self.cassette.get(key)
        if hit is not None:
            return Completion(hit["message"], hit["usage"], hit["model"], hit["finish_reason"], True)
        if self.cassette.mode == "replay":
            self.cassette.note_miss(key)
            raise CassetteMiss(f"no recorded call for salt {salt!r}")

        estimated = estimate_tokens(payload)
        bound = call_bound(provider.name, model, estimated, max_tokens)
        self.meter.authorize(bound)
        transport = self.transports[provider.name]
        try:
            result = transport.post(payload, estimated)
        finally:
            # Charged whether post() returned or raised, so an outage cannot hide billed attempts.
            uncertain = getattr(transport, "uncertain_attempts", 0)
            if uncertain:
                self.meter.charge_usd(provider.name, bound * uncertain)

        if result.tool_use_failed is not None:
            message = {"role": "assistant", "content": None, "tool_use_failed": result.tool_use_failed}
            usage: dict[str, Any] = {"prompt_tokens": estimated, "completion_tokens": max_tokens, "estimated": True}
            self.meter.charge_usd(provider.name, bound)
            model_returned, finish_reason = model, "tool_use_failed"
        else:
            body = result.body or {}
            try:
                choice = body["choices"][0]
                message = choice["message"]
            except (KeyError, IndexError, TypeError) as exc:
                self.meter.charge_usd(provider.name, bound)
                raise ProviderError(f"{provider.name} returned an unexpected body shape: {sorted(body)}") from exc
            if not isinstance(message, dict):
                self.meter.charge_usd(provider.name, bound)
                raise ProviderError(f"{provider.name} returned a message that is not an object")
            raw_usage = body.get("usage")
            if _usable_usage(raw_usage):
                usage = raw_usage
                self.meter.charge(provider.name, model, usage)
            else:
                usage = {"prompt_tokens": estimated, "completion_tokens": max_tokens, "estimated": True}
                self.meter.charge_usd(provider.name, bound)
            model_returned = body.get("model", model)
            finish_reason = choice.get("finish_reason") or ""

        record = {
            "message": message,
            "usage": usage,
            "model": model_returned,
            "finish_reason": finish_reason,
            "provider": provider.name,
            "salt": salt,
            "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        self.cassette.put(key, record)
        return Completion(message, usage, model_returned, finish_reason, False)
