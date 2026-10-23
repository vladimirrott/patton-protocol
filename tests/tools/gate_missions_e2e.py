#!/usr/bin/env python3
"""End to end, for $0: the patton-gate plugin's settings and mission limits in real Claude Code, with subagents.

    python3 tests/tools/gate_missions_e2e.py --claude claude --plugin-dir plugins/patton-gate

A scripted model (tests/tools/fake_agents.py) plays the main agent and each subagent. Every scenario runs in a scratch
HOME and project with its own settings file, in bypassPermissions so the only refusals come from the gate, and
checks which tool calls the gate refused. No key, no network: the model listens on 127.0.0.1.
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

MAIN = "Tidy this project"


def agent(name: str, description: str | None = None) -> dict:
    return {"tool": "Agent", "input": {"description": description or f"mission {name}",
                                       "prompt": f"MISSION-{name} do your part", "subagent_type": "general-purpose"}}


def bash(command: str) -> dict:
    return {"tool": "Bash", "input": {"command": command, "description": "run"}}


def write(root: str, rel: str) -> dict:
    return {"tool": "Write", "input": {"file_path": f"{root}/{rel}", "content": "x\n"}}


def scenarios(root: str) -> list[dict]:
    two = [[agent("A"), agent("B")], {"text": "Both reported."}]
    return [
        {"name": "no settings: two subagents run as before", "settings": None,
         "scripts": {MAIN: two, "MISSION-A": [[bash("ls")], [write(root, "a.txt")], {"text": "A done"}],
                     "MISSION-B": [[bash("ls")], [write(root, "b.txt")], {"text": "B done"}]},
         "refused": {}, "files": {"a.txt": True, "b.txt": True, "notes.txt": True}},
        {"name": "max_workers 1 refuses the second of two parallel dispatches", "settings": {"missions": {"max_workers": 1}},
         "scripts": {MAIN: two, "MISSION-A": [[bash("ls")], {"text": "A done"}],
                     "MISSION-B": [[bash("ls")], {"text": "B done"}]},
         # Claude Code runs the two PreToolUse hooks at once, so either dispatch may be the one refused.
         "refused_one_of": ([f"toolu_{MAIN}_0_0", f"toolu_{MAIN}_0_1"], "missions.max_workers")},
        {"name": "max_commands 1 stops a subagent at its second command",
         "settings": {"missions": {"max_commands": 1}},
         "scripts": {MAIN: [[agent("A")], {"text": "ok"}], "MISSION-A": [[bash("ls")], [bash("ls -A")], {"text": "A"}]},
         "refused": {"toolu_MISSION-A_1_0": "missions.max_commands"}},
        {"name": "max_output_tokens 30 stops a subagent once its transcript holds 40",
         "settings": {"missions": {"max_output_tokens": 30}},
         "scripts": {MAIN: [[agent("A")], {"text": "ok"}],
                     "MISSION-A": [[bash("ls")], [bash("ls -A")], [bash("ls -l")], {"text": "A"}]},
         # The message that makes a call is in the transcript before the hook runs: 20 + 20 at the second call.
         # Every later call stays refused.
         "refused": {"toolu_MISSION-A_1_0": "missions.max_output_tokens",
                     "toolu_MISSION-A_2_0": "missions.max_output_tokens"}, "persist": True},
        {"name": "timeout_minutes 0.05 stops a subagent after 3 s",
         "settings": {"missions": {"timeout_minutes": 0.05}},
         "scripts": {MAIN: [[agent("A")], {"text": "ok"}],
                     "MISSION-A": [[bash("ls")], {"calls": [bash("ls -A")], "delay": 4}, {"text": "A"}]},
         "refused": {"toolu_MISSION-A_1_0": "missions.timeout_minutes"}},
        {"name": "max_retries 0 refuses a second dispatch of the same mission",
         "settings": {"missions": {"max_retries": 0}},
         "scripts": {MAIN: [[agent("A", "fix it")], [agent("B", "fix it")], {"text": "ok"}],
                     "MISSION-A": [{"text": "A failed"}], "MISSION-B": [{"text": "B"}]},
         "refused": {f"toolu_{MAIN}_1_0": "missions.max_retries"}},
        {"name": "exclusive_paths lets one of two subagents change a shared file",
         "settings": {"missions": {"exclusive_paths": True}},
         "scripts": {MAIN: two, "MISSION-A": [[write(root, "shared.txt")], {"calls": [bash("ls")], "delay": 2}, {"text": "A"}],
                     "MISSION-B": [[write(root, "shared.txt")], {"calls": [bash("ls")], "delay": 2}, {"text": "B"}]},
         "refused_one_of": (["toolu_MISSION-A_0_0", "toolu_MISSION-B_0_0"], "missions.exclusive_paths")},
        {"name": "files.on_change deny refuses a protected change without asking",
         "settings": {"files": {"on_change": "deny"}},
         "scripts": {MAIN: [[bash("rm notes.txt")], {"text": "ok"}]},
         "refused": {f"toolu_{MAIN}_0_0": "files.on_change"}, "files": {"notes.txt": True}},
        {"name": "a broken settings file refuses every call and names the file",
         "settings": "{broken",
         "scripts": {MAIN: [[bash("ls")], {"text": "ok"}]},
         "refused": {f"toolu_{MAIN}_0_0": "config.json"}},
    ]


def run(claude: str, plugin_dir: str, case: dict, root: Path, home: Path) -> dict:
    config = home / "config" / "patton-gate" / "config.json"
    if case["settings"] is not None:
        config.parent.mkdir(parents=True)
        config.write_text(case["settings"] if isinstance(case["settings"], str) else json.dumps(case["settings"]))
    fake = FakeAgents(case["scripts"])
    port = fake.start()
    env = {**os.environ, "HOME": str(home), "XDG_CONFIG_HOME": str(home / "config"),
           "XDG_STATE_HOME": str(home / "state"), "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}",
           "ANTHROPIC_AUTH_TOKEN": "placeholder", "ANTHROPIC_DEFAULT_SONNET_MODEL": "fake",
           "ANTHROPIC_DEFAULT_OPUS_MODEL": "fake", "ANTHROPIC_DEFAULT_HAIKU_MODEL": "fake",
           "ANTHROPIC_SMALL_FAST_MODEL": "fake", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}
    for name in ("ANTHROPIC_API_KEY", "PATTON_GATE_CONFIG", "PATTON_GATE_STATE", "PATTON_GATE_ROOT"):
        env.pop(name, None)
    argv = [claude, "-p", f"{MAIN}.", "--model", "fake", "--output-format", "stream-json", "--verbose",
            "--permission-mode", "bypassPermissions", "--plugin-dir", plugin_dir, "--include-hook-events"]
    if not case.get("persist"):
        argv.append("--no-session-persistence")
    try:
        done = subprocess.run(argv, cwd=root, env=env, capture_output=True, text=True, timeout=300)
    finally:
        fake.stop()
    results = {}
    for line in done.stdout.splitlines():
        if not line.startswith("{"):
            continue
        event = json.loads(line)
        if event.get("type") != "user":
            continue
        for block in (event.get("message") or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                text = block.get("content")
                text = text if isinstance(text, str) else json.dumps(text)
                results[block.get("tool_use_id")] = (bool(block.get("is_error")), text)
    return {"rc": done.returncode, "results": results, "requests": fake.requests, "stderr": done.stderr[-400:]}


def check(case: dict, out: dict, root: Path) -> list[str]:
    problems = []
    gate_refusals = {k: t for k, (err, t) in out["results"].items() if err and "Patton gate" in t}
    for tool_use_id, words in case.get("refused", {}).items():
        error, text = out["results"].get(tool_use_id, (None, ""))
        if not (error and "Patton gate" in text and words in text):
            problems.append(f"{tool_use_id} was not refused for {words}: {text[:160]!r}")
    expected = set(case.get("refused", {}))
    if "refused_one_of" in case:
        ids, words = case["refused_one_of"]
        hit = [i for i in ids if i in gate_refusals and words in gate_refusals[i]]
        if len(hit) != 1:
            problems.append(f"expected exactly one of {sorted(ids)} refused for {words}, got {hit}")
        expected |= set(hit)
    extra = set(gate_refusals) - expected
    if extra:
        problems.append("unexpected gate refusals: " + "; ".join(f"{k}: {gate_refusals[k][:120]!r}" for k in extra))
    for rel, present in case.get("files", {}).items():
        if (root / rel).exists() != present:
            problems.append(f"{rel} {'missing' if present else 'present'}")
    if not out["results"]:
        problems.append(f"no tool ran (rc {out['rc']}): {out['stderr']!r}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--claude", default="claude")
    parser.add_argument("--plugin-dir", required=True)
    parser.add_argument("--only", default="")
    args = parser.parse_args()
    plugin_dir = str(Path(args.plugin_dir).resolve())
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        for i, case in enumerate(scenarios("{root}")):
            if args.only and args.only not in case["name"]:
                continue
            base = Path(tmp) / f"case{i}"
            root, home = base / "project", base / "home"
            root.mkdir(parents=True)
            home.mkdir()
            (root / "notes.txt").write_text("keep me\n")
            case = json.loads(json.dumps(case).replace("{root}", str(root)))
            if "refused_one_of" in case:
                case["refused_one_of"] = (set(case["refused_one_of"][0]), case["refused_one_of"][1])
            out = run(args.claude, plugin_dir, case, root, home)
            problems = check(case, out, root)
            failures += bool(problems)
            print(f"{'ok  ' if not problems else 'FAIL'} {case['name']}  (rc {out['rc']}, {len(out['results'])} tool results)")
            for p in problems:
                print("      -", p)
    print("all scenarios passed" if not failures else f"{failures} scenario(s) failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
