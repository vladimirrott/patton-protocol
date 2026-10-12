"""The only module that talks to a model provider.

Every call goes through `Client.chat`, which keys the request, serves it from
the cassette when it can, asks the meter before spending, and records the
response. Replay mode never reaches `HttpTransport`.

The key comes from an environment variable named by the provider. It goes
into the Authorization header and nowhere else: not into the cassette, not
into an exception message, not into a log line.
"""

from __future__ import annotations

import json
import os
import re
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


_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|s|m|h)")


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


def _is_quota_error(body_text: str) -> bool:
    return "insufficient_quota" in body_text or "quota" in body_text.lower() and "exceeded" in body_text.lower()


def _error_excerpt(body_text: str) -> str:
    try:
        parsed = json.loads(body_text)
        error = parsed.get("error", parsed)
        if isinstance(error, dict):
            return str(error.get("message") or error.get("code") or error)[:300]
        return str(error)[:300]
    except (json.JSONDecodeError, AttributeError):
        return body_text[:300]


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

    def _pace(self, estimated_tokens: int) -> None:
        if self._remaining_tokens is None or self._reset_seconds is None:
            return
        if estimated_tokens > self._remaining_tokens:
            self._sleep(self._reset_seconds)

    def _note_window(self, headers: dict[str, str]) -> None:
        remaining = headers.get("x-ratelimit-remaining-tokens")
        reset = parse_duration(headers.get("x-ratelimit-reset-tokens"))
        try:
            self._remaining_tokens = int(remaining) if remaining is not None else None
        except ValueError:
            self._remaining_tokens = None
        self._reset_seconds = reset

    def post(self, payload: dict[str, Any], estimated_tokens: int) -> tuple[dict[str, Any], dict[str, str]]:
        data = json.dumps(payload).encode("utf-8")
        url = self.provider.base_url.rstrip("/") + "/chat/completions"
        rate_waits = 0
        server_failures = 0
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
                    body = json.loads(response.read().decode("utf-8"))
                self._note_window(headers)
                return body, headers
            except urllib.error.HTTPError as exc:
                headers = {k.lower(): v for k, v in (exc.headers or {}).items()}
                body_text = exc.read().decode("utf-8", errors="replace")
                status = exc.code
                if status in (401, 403):
                    raise AuthError(f"{self.provider.name} rejected the key (HTTP {status}): {_error_excerpt(body_text)}") from None
                if status == 402 or _is_quota_error(body_text):
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
                    if server_failures >= len(SERVER_RETRY_WAITS):
                        raise ProviderError(f"{self.provider.name} HTTP {status} after retries: {_error_excerpt(body_text)}") from None
                    self._sleep(SERVER_RETRY_WAITS[server_failures])
                    server_failures += 1
                    continue
                raise ProviderError(f"{self.provider.name} HTTP {status}: {_error_excerpt(body_text)}") from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                if server_failures >= len(SERVER_RETRY_WAITS):
                    raise ProviderError(f"{self.provider.name} unreachable after retries: {type(exc).__name__}") from None
                self._sleep(SERVER_RETRY_WAITS[server_failures])
                server_failures += 1


def clean_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copy of `messages` without the provider-specific `reasoning` field."""
    cleaned = []
    for message in messages:
        if "reasoning" in message:
            message = {k: v for k, v in message.items() if k != "reasoning"}
        cleaned.append(message)
    return cleaned


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
        self.meter.authorize(call_bound(provider.name, model, estimated, max_tokens))
        transport = self.transports[provider.name]
        body, _headers = transport.post(payload, estimated)
        try:
            choice = body["choices"][0]
            message = choice["message"]
            usage = body["usage"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"{provider.name} returned an unexpected body shape: {sorted(body)}") from exc
        self.meter.charge(provider.name, model, usage)
        record = {
            "message": message,
            "usage": usage,
            "model": body.get("model", model),
            "finish_reason": choice.get("finish_reason") or "",
            "provider": provider.name,
            "salt": salt,
            "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        self.cassette.put(key, record)
        return Completion(message, usage, record["model"], record["finish_reason"], False)
