"""The tool loop: ask the model, run its tool calls, repeat until it answers.

Each call's salt is `<job salt>|t<turn>` (and `|<agent name>` when agents
interleave), so every call of every job has its own cassette key even when
two jobs send the same prompt.

The transcript keeps the visible content and the tool calls of every turn.
It keeps only the length of any provider reasoning text, so private reasoning
never lands in a committed results file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .client import Provider
from .gate import Gate
from .sandbox import Sandbox, tool_schemas

TOOL_RESULT_EXCERPT = 300


@dataclass
class RunResult:
    final_text: str = ""
    turns: int = 0
    stop_reason: str = ""
    usage: dict[str, int] = field(default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0})
    calls_from_cassette: int = 0
    calls_live: int = 0
    tool_use_failures: int = 0
    models_returned: list[str] = field(default_factory=list)
    transcript: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AgentSpec:
    name: str
    system: str
    user: str
    tools: list[str]
    gate: Gate | None = None


class _Loop:
    """One agent's conversation state."""

    def __init__(self, system: str, user: str, tools: list[str]) -> None:
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        self.tools = list(tools)
        self.schemas = tool_schemas(self.tools)
        self.result = RunResult()
        self.done = False

    def step(self, client: Any, provider: Provider, model: str, sandbox: Sandbox, salt: str, max_tokens: int) -> None:
        completion = client.chat(
            provider, model, self.messages, tools=self.schemas, salt=salt, max_tokens=max_tokens
        )
        result = self.result
        result.turns += 1
        if completion.from_cassette:
            result.calls_from_cassette += 1
        else:
            result.calls_live += 1
        if completion.model and completion.model not in result.models_returned:
            result.models_returned.append(completion.model)
        for key in ("prompt_tokens", "completion_tokens"):
            result.usage[key] += int(completion.usage.get(key, 0))

        message = completion.message
        if message.get("tool_use_failed"):
            failure = message["tool_use_failed"]
            result.tool_use_failures += 1
            self.messages.append({"role": "assistant", "content": "[my tool call could not be parsed]"})
            self.messages.append({
                "role": "user",
                "content": f"Your last tool call could not be parsed ({failure.get('message', 'invalid call')}). "
                           "Call the tools with valid JSON arguments, or give your final report.",
            })
            result.transcript.append({"turn": result.turns, "content": None, "reasoning_chars": 0,
                                      "finish_reason": completion.finish_reason, "tool_use_failed": True,
                                      "tool_calls": []})
            return
        content = message.get("content")
        calls = message.get("tool_calls") or []
        assistant: dict[str, Any] = {"role": "assistant"}
        if content is not None:
            assistant["content"] = content
        if calls:
            assistant["tool_calls"] = [
                {
                    "id": call.get("id", f"call_{result.turns}_{i}"),
                    "type": "function",
                    "function": {
                        "name": call.get("function", {}).get("name", ""),
                        "arguments": call.get("function", {}).get("arguments", ""),
                    },
                }
                for i, call in enumerate(calls)
            ]
        self.messages.append(assistant)

        entry: dict[str, Any] = {
            "turn": result.turns,
            "content": content,
            "reasoning_chars": len(message.get("reasoning") or ""),
            "finish_reason": completion.finish_reason,
            "tool_calls": [],
        }
        if not calls:
            result.final_text = content or ""
            if (content or "").strip():
                result.stop_reason = "final"
            elif completion.finish_reason == "length":
                result.stop_reason = "length"
            else:
                result.stop_reason = "empty"
            result.transcript.append(entry)
            self.done = True
            return

        for call in assistant["tool_calls"]:
            name = call["function"]["name"]
            arguments = call["function"]["arguments"]
            if name in self.tools:
                output = sandbox.execute(name, arguments)
            else:
                output = f"ERROR: tool {name!r} is not available in this mission"
                sandbox.events.append(
                    {"i": len(sandbox.events), "tool": name, "args": arguments, "ok": False,
                     "denied": False, "result": output}
                )
            self.messages.append({"role": "tool", "tool_call_id": call["id"], "content": output})
            entry["tool_calls"].append(
                {"name": name, "arguments": arguments, "result": output[:TOOL_RESULT_EXCERPT]}
            )
        result.transcript.append(entry)


def run_agent(
    client: Any,
    provider: Provider,
    model: str,
    system: str,
    user: str,
    sandbox: Sandbox,
    tools: list[str],
    *,
    salt: str,
    max_turns: int = 10,
    max_tokens: int = 2000,
) -> RunResult:
    loop = _Loop(system, user, tools)
    for turn in range(max_turns):
        loop.step(client, provider, model, sandbox, f"{salt}|t{turn}", max_tokens)
        if loop.done:
            return loop.result
    loop.result.stop_reason = "turn_cap"
    return loop.result


def run_interleaved(
    client: Any,
    provider: Provider,
    model: str,
    specs: list[AgentSpec],
    sandbox: Sandbox,
    *,
    salt: str,
    max_turns: int = 10,
    max_tokens: int = 2000,
) -> list[RunResult]:
    """Alternate one model call per unfinished agent over a shared sandbox."""
    loops = [_Loop(spec.system, spec.user, spec.tools) for spec in specs]
    for turn in range(max_turns):
        active = False
        for spec, loop in zip(specs, loops):
            if loop.done:
                continue
            active = True
            sandbox.gate = spec.gate
            try:
                loop.step(client, provider, model, sandbox, f"{salt}|{spec.name}|t{turn}", max_tokens)
            finally:
                sandbox.gate = None
        if not active:
            break
    for loop in loops:
        if not loop.done:
            loop.result.stop_reason = "turn_cap"
    return [loop.result for loop in loops]
