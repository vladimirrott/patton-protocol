"""P1M's arms: the frozen `fitted-v2` Verifier prompt asked of different Groq models and reasoning settings.

The prompt is P1V's candidate, unchanged. An arm differs only in the model, its `reasoning_effort`, and the output
budget a reasoning model needs. `q38-none` is P1V's own run (qwen3.8-27b at its default, reasoning off); it is the
reference and is not asked again. Settings are read from console.groq.com/docs/reasoning and the model pages on
2026-11-04: qwen3.8-27b takes none, low, medium and high (default none; `high` is its native xhigh mode) and
`reasoning_format`; the gpt-oss models take low, medium and high (default medium) and no `reasoning_format`.
qwen3.6-27b is left out: Groq announced its deprecation on 2026-09-14 in favour of qwen3.8-27b.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..p1v import prompts as p1v_prompts

SYSTEM = p1v_prompts.ARMS["fitted-v2"]
MAX_TOKENS = 16384  # qwen3.8-27b's own limit; reasoning tokens count against it


@dataclass(frozen=True)
class Arm:
    name: str
    model: str
    effort: str | None
    hide: bool = False  # send reasoning_format="hidden" (qwen only)
    max_tokens: int = MAX_TOKENS

    def extra(self) -> dict:
        out: dict = {}
        if self.effort is not None:
            out["reasoning_effort"] = self.effort
        if self.hide:
            out["reasoning_format"] = "hidden"
        return out


REFERENCE = "q38-none"
# Every arm the development set tried. The registered study asks only ARMS: the gpt-oss arms flagged fewer gamed
# development states than qwen3.8 with no reasoning at all and were not cheaper by enough to matter, so the
# pre-registration drops them and records why.
ALL = {a.name: a for a in (
    Arm("q38-med", "qwen3.8-27b", "medium", hide=True),
    Arm("q38-high", "qwen3.8-27b", "high", hide=True),
    Arm("g120-med", "gpt-oss-120b", "medium"),
    Arm("g120-high", "gpt-oss-120b", "high"),
    Arm("sg20-high", "gpt-oss-safeguard-20b", "high"),
)}
DROPPED = ("g120-med", "g120-high", "sg20-high")
ARMS = {n: a for n, a in ALL.items() if n not in DROPPED}
