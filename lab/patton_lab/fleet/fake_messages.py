"""The trace's scripted worker for a port whose harness speaks Anthropic Messages (Claude Code).

It is the Claude Code study's scripted endpoint (patton_lab/cc/fake_upstream.py) with FakeChat's face: `requests`
holds the bodies of the worker's turns (requests that offer tools; side calls without tools are left out), and
`saw(text)` says whether any of them carried `text`. A step is FakeChat's: {"tool": name, "input": {...}} from
the port's `shell_step`, or {"text": ...}; past the script the worker answers "Finished."
"""

from __future__ import annotations

import json
from typing import Any

from ..cc.fake_upstream import FakeUpstream


class FakeMessages:
    def __init__(self, steps: list[dict[str, Any]]) -> None:
        self._fake = FakeUpstream(steps)

    def start(self) -> int:
        return self._fake.start()

    def stop(self) -> None:
        self._fake.stop()

    @property
    def requests(self) -> list[dict[str, Any]]:
        return [r["body"] for r in self._fake.requests
                if r["path"].split("?")[0].rstrip("/").endswith("/v1/messages") and r["body"].get("tools")]

    def saw(self, text: str) -> bool:
        """Whether `text` reached the model in a user or assistant turn. A `role: "system"` entry inside `messages`
        does not count: LiteLLM's /v1/messages drops those on the way to Groq, so text carried only there would
        reach the scripted worker and never a real model."""
        return any(text in json.dumps([m for m in body.get("messages", []) if m.get("role") != "system"])
                   for body in self.requests)
