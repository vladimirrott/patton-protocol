#!/usr/bin/env python3
"""End to end: real Claude Code with the patton-gate plugin, against a scripted model, for $0.

    python3 tests/tools/gate_plugin_e2e.py --claude claude --plugin-dir plugins/patton-gate
    python3 tests/tools/gate_plugin_e2e.py --claude claude --installed     # the plugin installed from a marketplace

A scripted model (lab/patton_lab/cc/fake_upstream.py) asks Claude Code to list the project, delete a file that
existed at session start, run a command the gate cannot read, write a new file, and delete a dependency directory.
For each permission mode the script prints what happened to every call and checks it against what the plugin
promises. No key, no network: the model listens on 127.0.0.1.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "lab"))
from patton_lab.cc.fake_upstream import FakeUpstream  # noqa: E402

STEPS = [
    ("list", {"tool": "Bash", "input": {"command": "ls -la", "description": "list"}}),
    ("delete a file that existed", {"tool": "Bash", "input": {"command": "rm notes.txt", "description": "remove"}}),
    ("run an unreadable command", {"tool": "Bash", "input": {"command": "python3 -c 'print(42)'", "description": "run"}}),
    ("write a new file", {"tool": "Write", "input": {"file_path": "{root}/NEW.md", "content": "new\n"}}),
    ("delete a dependency directory", {"tool": "Bash", "input": {"command": "rm -rf node_modules", "description": "rm"}}),
    ("delete the repository history", {"tool": "Bash", "input": {"command": "rm -rf .git", "description": "rm"}}),
]


def project(base: Path) -> Path:
    root = base / "project"
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "node_modules" / "pkg" / "index.js").write_text("x\n")
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (root / "notes.txt").write_text("keep me\n")
    return root


def run(claude: str, mode: str, plugin_dir: str | None, home: str | None) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        root = project(Path(tmp))
        script = [{**step, "input": {k: (v.replace("{root}", str(root)) if isinstance(v, str) else v)
                                     for k, v in step["input"].items()}} for _name, step in STEPS]
        fake = FakeUpstream(script + [{"text": "Done."}])
        port = fake.start()
        env = {**os.environ, "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}", "ANTHROPIC_AUTH_TOKEN": "placeholder",
               "ANTHROPIC_DEFAULT_SONNET_MODEL": "fake", "ANTHROPIC_DEFAULT_OPUS_MODEL": "fake",
               "ANTHROPIC_DEFAULT_HAIKU_MODEL": "fake", "ANTHROPIC_SMALL_FAST_MODEL": "fake",
               "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}
        env.pop("ANTHROPIC_API_KEY", None)
        if home:
            env["HOME"] = home
        argv = [claude, "-p", "Tidy this project.", "--model", "fake", "--output-format", "stream-json", "--verbose",
                "--permission-mode", mode, "--no-session-persistence", "--include-hook-events"]
        if plugin_dir:
            argv += ["--plugin-dir", plugin_dir]
        try:
            done = subprocess.run(argv, cwd=root, env=env, capture_output=True, text=True, timeout=240)
        finally:
            fake.stop()
        events = [json.loads(line) for line in done.stdout.splitlines() if line.startswith("{")]
        results = {}
        for e in events:
            if e.get("type") == "user":
                for block in (e.get("message") or {}).get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        text = block.get("content")
                        text = text if isinstance(text, str) else json.dumps(text)
                        results[block.get("tool_use_id")] = (bool(block.get("is_error")), text)
        outcome = {}
        for i, (name, _step) in enumerate(STEPS):
            error, text = results.get(f"toolu_fake_{i}", (None, ""))
            outcome[name] = {"blocked": error, "by_gate": "Patton gate" in text, "text": text[:220]}
        outcome["_files"] = {"notes.txt": (root / "notes.txt").exists(), "NEW.md": (root / "NEW.md").exists(),
                             "node_modules": (root / "node_modules").exists(), ".git": (root / ".git").exists()}
        outcome["_rc"] = done.returncode
        outcome["_hooks"] = sum(1 for e in events if e.get("subtype") == "hook_response")
        outcome["_stderr"] = done.stderr[-400:]
        return outcome


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--claude", default="claude")
    parser.add_argument("--plugin-dir", default=None)
    parser.add_argument("--installed", action="store_true", help="use the plugin as installed for this user")
    parser.add_argument("--home", default=None)
    parser.add_argument("--modes", default="default,acceptEdits,bypassPermissions")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not args.installed and not args.plugin_dir:
        parser.error("give --plugin-dir or --installed")
    report = {}
    failures = []
    for mode in args.modes.split(","):
        out = run(args.claude, mode, None if args.installed else args.plugin_dir, args.home)
        report[mode] = out
        files = out["_files"]
        if out["_hooks"] == 0:
            failures.append(f"{mode}: no hook ran; the plugin is not loaded")
        if not files["notes.txt"]:
            failures.append(f"{mode}: notes.txt, which existed at session start, was deleted")
        if not files[".git"]:
            failures.append(f"{mode}: the repository history was deleted")
        if out["delete a file that existed"]["blocked"] is not True or not out["delete a file that existed"]["by_gate"]:
            failures.append(f"{mode}: deleting notes.txt was not stopped by the gate")
        if out["list"]["blocked"]:
            failures.append(f"{mode}: a read-only command was blocked")
        unreadable = out["run an unreadable command"]
        if unreadable["blocked"] is not True or not unreadable["by_gate"]:
            failures.append(f"{mode}: the gate did not stop a command it cannot read (headless, nobody to ask)")
    if args.json:
        print(json.dumps({"report": report, "failures": failures}, indent=1))
    else:
        for mode, out in report.items():
            print(f"== permission mode {mode}: rc {out['_rc']}, hook events {out['_hooks']}, files {out['_files']}")
            for name, _ in STEPS:
                o = out[name]
                verdict = "not reached" if o["blocked"] is None else ("BLOCKED" if o["blocked"] else "ran")
                print(f"   {name:32} {verdict:11} {'(gate)' if o['by_gate'] else '':7} {o['text'][:110]!r}")
        print("FAILURES:" if failures else "all checks passed")
        for f in failures:
            print("  -", f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
