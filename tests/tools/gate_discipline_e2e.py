#!/usr/bin/env python3
"""End to end, for $0: the patton-gate plugin's discipline rules in real Claude Code.

    python3 tests/tools/gate_discipline_e2e.py --claude claude --plugin-dir plugins/patton-gate

A scripted Anthropic endpoint (tests/tools/fake_agents.py) plays the worker, in a scratch HOME and project, with
file protection off and three rules on: test reminders, a check before the stop, and the report contract. The
worker writes a test, says it is done, runs a check that fails, reports the work completed, then reports it
partial. Expected: the reminder reaches the model next to the write's result; the first stop is refused because no
check ran; the second because the report says completed after a failing check (Claude Code sends
PostToolUseFailure for it); the third, an honest partial report, ends the turn.
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

MAIN = "Add a test for the parser"
REPORT = """status: {status}
files_changed:
  - tests/test_parser.py
commands_run:
  - command: "python3 -m unittest"
    outcome: "failed"
evidence:
  - "the unittest run failed"
risks:
  - "the parser is still untested"
next_action: "fix the failing test"
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--claude", default="claude")
    parser.add_argument("--plugin-dir", required=True)
    args = parser.parse_args()
    plugin_dir = str(Path(args.plugin_dir).resolve())
    problems: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        home, root = Path(tmp) / "home", Path(tmp) / "project"
        (root / "tests").mkdir(parents=True)
        home.mkdir()
        config = home / "config" / "patton-gate" / "config.json"
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({"files": {"protect": False},
                                      "discipline": {"reminders": True, "check_before_stop": True, "report": "all",
                                                     "max_rounds": 2}}))
        write = {"tool": "Write", "input": {"file_path": str(root / "tests" / "test_parser.py"),
                                            "content": "import unittest\n\nclass T(unittest.TestCase):\n"
                                                       "    def test_x(self):\n        self.fail('todo')\n"}}
        check = {"tool": "Bash", "input": {"command": "python3 -m unittest", "description": "run the tests"}}
        script = [[write], {"text": "Done."}, [check], {"text": REPORT.format(status="completed")},
                  {"text": REPORT.format(status="partial")}]
        fake = FakeAgents({MAIN: script})
        port = fake.start()
        env = {**{k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC", "PATTON_GATE"))},
               "HOME": str(home), "XDG_CONFIG_HOME": str(home / "config"), "XDG_STATE_HOME": str(home / "state"),
               "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}", "ANTHROPIC_AUTH_TOKEN": "placeholder",
               "ANTHROPIC_DEFAULT_SONNET_MODEL": "fake", "ANTHROPIC_DEFAULT_OPUS_MODEL": "fake",
               "ANTHROPIC_DEFAULT_HAIKU_MODEL": "fake", "ANTHROPIC_SMALL_FAST_MODEL": "fake",
               "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}
        try:
            done = subprocess.run([args.claude, "-p", f"{MAIN}.", "--model", "fake", "--output-format", "stream-json",
                                   "--verbose", "--permission-mode", "bypassPermissions", "--plugin-dir", plugin_dir,
                                   "--no-session-persistence"], cwd=root, env=env, capture_output=True, text=True,
                                  timeout=300, stdin=subprocess.DEVNULL)
        finally:
            fake.stop()
        texts = [json.dumps(r["messages"]) for r in fake.requests if r["key"] == MAIN]
        entries = [json.loads(line) for d, _, files in os.walk(home) if "discipline.jsonl" in files
                   for line in Path(d, "discipline.jsonl").read_text().splitlines() if line.strip()]
        print(f"rc {done.returncode}, {len(texts)} worker requests, discipline decisions "
              f"{[e['decision'] for e in entries]}")
        if not any("is a test" in t for t in texts[1:2]):
            problems.append("the test reminder did not reach the model after the write")
        if not any("ran none of the project's checks" in t for t in texts[2:3]):
            problems.append("the first stop was not refused for the missing check")
        if not any("failed." in t and "status says completed" in t for t in texts[4:5]):
            problems.append("the completed report after a failing check was not refused")
        if [e["decision"] for e in entries] != ["block", "block"]:
            problems.append(f"expected two refusals and then a stop, saw {[e['decision'] for e in entries]}")
        if len(texts) != 5:
            problems.append(f"expected 5 worker requests, saw {len(texts)}")
    print("FAILURES:" if problems else "all checks passed")
    for p in problems:
        print("  -", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
