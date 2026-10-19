"""The arms of the gate study, the mission text, and how runs are ordered.

Section 6 of docs/plans/2026-10-19-real-harness-gate-design.md is the source. R2 and R2S have no entry here
because they have no live runs: the diff gate is evaluated offline over R0 and R0S (see diffgate.py).
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from ..experiments import e1_mission
from ..fixtures import e1

SONNET, HAIKU, GPT_OSS = "claude-sonnet-5-5", "claude-haiku-5-5", "gpt-oss-120b"


@dataclass(frozen=True)
class Arm:
    name: str
    gate: str  # none or hook
    approver: str  # none, oracle or deny-all (the live approver of a hook arm)
    skill: bool  # the current skill package is installed as a user skill
    backend: str  # anthropic (the subscription) or groq (a local LiteLLM proxy)
    model: str
    permission_mode: str = "bypassPermissions"


def _arm(name: str, gate: str, approver: str, skill: bool, backend: str, model: str, mode: str = "bypassPermissions") -> Arm:
    return Arm(name, gate, approver, skill, backend, model, mode)


ARMS: dict[str, Arm] = {a.name: a for a in (
    _arm("R0", "none", "none", False, "anthropic", SONNET),
    _arm("R0S", "none", "none", True, "anthropic", SONNET),
    _arm("R1", "hook", "oracle", False, "anthropic", SONNET),
    _arm("R1S", "hook", "oracle", True, "anthropic", SONNET),
    _arm("R1D", "hook", "deny-all", False, "anthropic", SONNET),
    _arm("R3", "none", "none", False, "anthropic", SONNET, "auto"),
    _arm("H-R0", "none", "none", False, "anthropic", HAIKU),
    _arm("H-R1", "hook", "oracle", False, "anthropic", HAIKU),
    _arm("O-R0", "none", "none", False, "groq", GPT_OSS),
    _arm("O-R0S", "none", "none", True, "groq", GPT_OSS),
    _arm("O-R1", "hook", "oracle", False, "groq", GPT_OSS),
    _arm("O-R1S", "hook", "oracle", True, "groq", GPT_OSS),
    _arm("O-R1D", "hook", "deny-all", False, "groq", GPT_OSS),
)}


def mission(fixture_id: str) -> str:
    """The lab's mission contract, so the task is the one the open models saw."""
    return e1_mission(e1.fixture_by_id(fixture_id))


def plan_blocks(arm_names: list[str], fixtures: list[str], reps: int, seed: int) -> list[list[tuple[str, str, int]]]:
    """One block per (fixture, rep), holding every arm in a shuffled order. A rep finishes before the next starts,
    so a study that stops early still holds complete reps."""
    rng = random.Random(seed)
    blocks: list[list[tuple[str, str, int]]] = []
    for rep in range(reps):
        order = list(fixtures)
        rng.shuffle(order)
        for fixture in order:
            names = list(arm_names)
            rng.shuffle(names)
            blocks.append([(name, fixture, rep) for name in names])
    return blocks
