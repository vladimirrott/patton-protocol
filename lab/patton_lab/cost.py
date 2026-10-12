"""Prices, cost bounds, and the spend meter.

The meter is the guarantee. Bounds are estimates: a run can use more turns
than the bound assumes, so `dry-run` prints a bound and the meter refuses any
call that could carry actual spend past `abort_at_usd`.

Actual spend comes from the `usage` block of each response. Cached prompt
tokens are charged at the full input price, which overstates the bill and
never understates it. The ledger persists across processes, so a resumed
study cannot forget what an earlier run spent.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

# USD per 1M tokens (input, output). List prices as recorded in July 2026 in
# WhyStuck's cost table. An unknown model raises rather than costing zero.
PRICES: dict[tuple[str, str], tuple[float, float]] = {
    ("groq", "openai/gpt-oss-120b"): (0.15, 0.60),
    ("openai", "gpt-4.1-mini"): (0.40, 1.60),
}
PRICES_AS_OF = "2026-07"

EPSILON = 1e-9


class BudgetExceeded(Exception):
    """A call would push spend past the meter's abort line."""


def price(provider: str, model: str) -> tuple[float, float]:
    try:
        return PRICES[(provider, model)]
    except KeyError:
        raise KeyError(f"no price recorded for {provider}/{model}; add it to PRICES first") from None


def usd(prompt_tokens: int, completion_tokens: int, unit_price: tuple[float, float]) -> float:
    return prompt_tokens / 1e6 * unit_price[0] + completion_tokens / 1e6 * unit_price[1]


def estimate_tokens(obj: Any) -> int:
    """Approximate tokens as bytes of compact JSON divided by 4, rounded up."""
    # ensure_ascii escapes every non-ASCII character, so a lone surrogate cannot raise, and the
    # escaped form is longer than UTF-8, which keeps the estimate conservative.
    raw = json.dumps(obj, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return max(1, math.ceil(len(raw) / 4))


def run_tokens(first_call_tokens: int, turns: int, growth: int, out_per_turn: int) -> tuple[int, int]:
    """Input and output tokens of a run whose prompt grows by `growth` each turn."""
    tokens_in = sum(first_call_tokens + growth * i for i in range(turns))
    return tokens_in, turns * out_per_turn


def call_bound(provider: str, model: str, prompt_tokens: int, max_tokens: int) -> float:
    """Upper estimate for one call: the estimated prompt plus every allowed output token."""
    return usd(prompt_tokens, max_tokens, price(provider, model))


class Meter:
    def __init__(self, ledger: Path | str, abort_at_usd: float = 9.0, cap_usd: float = 10.0) -> None:
        if abort_at_usd > cap_usd:
            raise ValueError(f"abort line {abort_at_usd} is above the cap {cap_usd}")
        self.ledger = Path(ledger)
        self.abort_at_usd = abort_at_usd
        self.cap_usd = cap_usd
        self._state = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.ledger.exists():
            return {"version": 1, "spent": {}, "calls": 0}
        try:
            state = json.loads(self.ledger.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"spend ledger {self.ledger} is unreadable; refusing to guess the spend") from exc
        if not isinstance(state, dict) or not isinstance(state.get("spent"), dict):
            raise ValueError(f"spend ledger {self.ledger} has no 'spent' table")
        state.setdefault("calls", 0)
        return state

    def _save(self) -> None:
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.ledger.with_suffix(self.ledger.suffix + ".tmp")
        tmp.write_text(json.dumps(self._state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, self.ledger)

    def spent(self, provider: str | None = None) -> float:
        table = self._state["spent"]
        if provider is not None:
            return float(table.get(provider, 0.0))
        return float(sum(table.values()))

    def calls(self) -> int:
        return int(self._state["calls"])

    def reload(self) -> None:
        """Re-read the ledger, so a check never trusts a stale copy of the spend."""
        self._state = self._load()

    def authorize(self, bound_usd: float) -> None:
        self.reload()
        if self.spent() + bound_usd > self.abort_at_usd + EPSILON:
            raise BudgetExceeded(
                f"spent ${self.spent():.4f} + this call's bound ${bound_usd:.4f} "
                f"would pass the abort line ${self.abort_at_usd:.2f}"
            )

    def charge(self, provider: str, model: str, usage: dict[str, Any]) -> float:
        if "prompt_tokens" not in usage or "completion_tokens" not in usage:
            raise ValueError(f"usage block lacks token counts: {sorted(usage)}")
        amount = usd(int(usage["prompt_tokens"]), int(usage["completion_tokens"]), price(provider, model))
        return self.charge_usd(provider, amount)

    def charge_usd(self, provider: str, amount: float) -> float:
        """Add a dollar amount directly, for calls whose usage is unknown (charged at their bound)."""
        if amount < 0:
            raise ValueError("a charge cannot be negative")
        self.reload()
        table = self._state["spent"]
        table[provider] = float(table.get(provider, 0.0)) + amount
        self._state["calls"] = self.calls() + 1
        self._save()
        return amount
