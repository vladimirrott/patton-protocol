#!/usr/bin/env python3
"""Probe, for $0: which hook events and fields real Claude Code gives when the main agent runs two subagents at once.

    python3 tests/tools/probe_subagent_hooks.py --claude claude

A scripted model (tests/tools/fake_agents.py) makes the main agent dispatch two subagents in one message, and each
subagent runs two Bash commands. A logging hook on every event appends its input to a file, together with whether
the subagent's transcript exists at that moment and how many output tokens it holds. Prints the log, one line per
event. Scratch HOME and project; no key, no network.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_agents import FakeAgents  # noqa: E402

LOGGER = r'''
import json, os, sys, glob
event = json.load(sys.stdin)
note = {}
agent = event.get("agent_id")
if agent and event.get("transcript_path"):
    base = os.path.splitext(event["transcript_path"])[0]
    path = os.path.join(base, "subagents", f"agent-{agent}.jsonl")
    note["sub_transcript_exists"] = os.path.exists(path)
    seen = {}
    if os.path.exists(path):
        for line in open(path):
            e = json.loads(line)
            m = e.get("message") or {}
            if e.get("type") == "assistant" and isinstance(m, dict) and m.get("usage"):
                seen[m.get("id")] = m["usage"].get("output_tokens", 0)
    note["sub_output_tokens"] = sum(seen.values())
with open(os.environ["PROBE_LOG"], "a") as h:
    h.write(json.dumps({"event": event, "note": note}) + "\n")
'''

MAIN = [[{"tool": "Agent", "input": {"description": "mission A", "prompt": "MISSION-A list and write a.txt",
                                     "subagent_type": "general-purpose"}},
         {"tool": "Agent", "input": {"description": "mission B", "prompt": "MISSION-B list and write b.txt",
                                     "subagent_type": "general-purpose"}}],
        {"text": "Both missions reported."}]


def sub(name: str) -> list:
    return [[{"tool": "Bash", "input": {"command": "ls -A", "description": "list"}}],
            [{"tool": "Bash", "input": {"command": f"echo {name} > {name}.txt", "description": "write"}}],
            {"text": f"{name} done."}]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--claude", default="claude")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        home, root, log = Path(tmp) / "home", Path(tmp) / "project", Path(tmp) / "probe.jsonl"
        (home / ".claude").mkdir(parents=True)
        root.mkdir()
        (root / "notes.txt").write_text("keep\n")
        logger = Path(tmp) / "logger.py"
        logger.write_text(LOGGER)
        hook = [{"hooks": [{"type": "command", "command": f"{sys.executable} {logger}"}]}]
        settings = {"hooks": {name: ([{"matcher": "*", **hook[0]}] if "Tool" in name else hook)
                              for name in ("SessionStart", "PreToolUse", "PostToolUse", "PostToolUseFailure",
                                           "SubagentStart", "SubagentStop", "Stop")}}
        (home / ".claude" / "settings.json").write_text(json.dumps(settings))
        fake = FakeAgents({"MISSION-A": sub("a"), "MISSION-B": sub("b"), "Tidy this project": MAIN})
        port = fake.start()
        env = {**os.environ, "HOME": str(home), "PROBE_LOG": str(log),
               "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}", "ANTHROPIC_AUTH_TOKEN": "placeholder",
               "ANTHROPIC_DEFAULT_SONNET_MODEL": "fake", "ANTHROPIC_DEFAULT_OPUS_MODEL": "fake",
               "ANTHROPIC_DEFAULT_HAIKU_MODEL": "fake", "ANTHROPIC_SMALL_FAST_MODEL": "fake",
               "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}
        env.pop("ANTHROPIC_API_KEY", None)
        argv = [args.claude, "-p", "Tidy this project.", "--model", "fake", "--output-format", "stream-json",
                "--verbose", "--permission-mode", "bypassPermissions"]
        try:
            done = subprocess.run(argv, cwd=root, env=env, capture_output=True, text=True, timeout=240)
        finally:
            fake.stop()
        print(f"rc {done.returncode}; model requests {fake.requests}")
        print("files:", sorted(p.name for p in root.iterdir()))
        for line in log.read_text().splitlines() if log.exists() else []:
            entry = json.loads(line)
            e = entry["event"]
            keep = {k: e.get(k) for k in ("hook_event_name", "tool_name", "agent_id", "agent_type", "tool_use_id")
                    if e.get(k) is not None}
            if isinstance(e.get("tool_input"), dict):
                keep["input"] = {k: str(v)[:40] for k, v in e["tool_input"].items()}
            extra = sorted(set(e) - {"hook_event_name", "tool_name", "agent_id", "agent_type", "tool_use_id",
                                     "tool_input", "session_id", "transcript_path", "cwd", "permission_mode",
                                     "tool_response"})
            print(json.dumps(keep), "extra:", extra, entry["note"] or "")
        if done.returncode != 0:
            print(done.stderr[-800:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
