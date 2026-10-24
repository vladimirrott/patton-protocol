#!/usr/bin/env python3
"""End to end, for $0: the patton-gate plugin's blind Verifier in real Claude Code.

    python3 tests/tools/gate_verify_e2e.py --claude claude --plugin-dir plugins/patton-gate

A scripted Anthropic endpoint (tests/tools/fake_agents.py) plays the worker, and a scripted chat-completions
endpoint plays the claim reader and the Verifier, so no key and no network are needed. Each scenario runs in a
scratch HOME and a git repository of its own, with file protection off so the only hook decisions are the
Verifier's, and checks the verdict ledger, what reached the worker after a refused stop, and the files.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_agents import FakeAgents  # noqa: E402

MAIN = "Tidy this project"
NOTE = "`keep.txt` is the only copy of the ledger."


class FakeVerifier:
    """Claim reader: a final message that says "could not" claims nothing. Verifier: answers by the scenario."""

    def __init__(self, verdict: str) -> None:
        self.verdict = verdict
        self.calls: list[tuple[str, str]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                system, user = body["messages"][0]["content"], body["messages"][1]["content"]
                kind = "claims" if "final message of a coding agent" in system else "verifier"
                outer.calls.append((kind, user))
                if kind == "claims":
                    content = {"claims_done": "could not" not in user.split("## The agent's final message")[-1],
                               "quote": ""}
                elif outer.verdict == "fail":
                    content = {"verdict": "fail", "findings": [{"problem": "the ledger was deleted", "evidence": NOTE}]}
                else:
                    content = {"verdict": "pass", "findings": []}
                data = json.dumps({"choices": [{"message": {"role": "assistant", "content": json.dumps(content)}}]})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data.encode())

            def log_message(self, format, *args):  # noqa: A002
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.port = self.server.server_address[1]

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def bash(command: str) -> dict:
    return {"tool": "Bash", "input": {"command": command, "description": "run"}}


def scenarios() -> list[dict]:
    return [
        {"name": "a false claim is refused, then the honest report is let through", "verdict": "fail",
         "script": [[bash("rm keep.txt")], {"text": "All done."},
                    {"text": "I could not keep keep.txt: I deleted it, and it was the only copy."}],
         "decisions": ["block", "allow (no completion claimed)"], "reason_reached": True, "files": {"keep.txt": False}},
        {"name": "a change that passes ends the turn with no refusal", "verdict": "pass",
         "script": [[bash("rm scratch.txt")], {"text": "All done."}],
         "decisions": ["allow (passed)"], "reason_reached": False, "files": {"scratch.txt": False, "keep.txt": True}},
        {"name": "refusals stop at the round cap and the person is told", "verdict": "fail",
         "script": [[bash("rm keep.txt")], {"text": "All done."}, {"text": "All done, really."},
                    {"text": "Done, I promise."}],
         "decisions": ["block", "block", "allow (round cap)"], "reason_reached": True, "system_message": "still fails"},
        {"name": "an endpoint that does not answer lets the turn end, marked not verified", "verdict": "down",
         "script": [[bash("rm scratch.txt")], {"text": "All done."}],
         "decisions": ["allow (not verified)"], "reason_reached": False, "system_message": "not verified"},
    ]


def run(claude: str, plugin_dir: str, case: dict, root: Path, home: Path) -> dict:
    verifier = FakeVerifier(case["verdict"])
    port = verifier.port if case["verdict"] != "down" else 9  # nothing listens on the discard port
    config = home / "config" / "patton-gate" / "config.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"files": {"protect": False},
                                  "verify": {"main": True, "endpoint": f"http://127.0.0.1:{port}/v1", "model": "m",
                                             "timeout_s": 5}}))
    fake = FakeAgents({MAIN: case["script"]})
    model_port = fake.start()
    env = {**os.environ, "HOME": str(home), "XDG_CONFIG_HOME": str(home / "config"),
           "XDG_STATE_HOME": str(home / "state"), "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{model_port}",
           "ANTHROPIC_AUTH_TOKEN": "placeholder", "ANTHROPIC_DEFAULT_SONNET_MODEL": "fake",
           "ANTHROPIC_DEFAULT_OPUS_MODEL": "fake", "ANTHROPIC_DEFAULT_HAIKU_MODEL": "fake",
           "ANTHROPIC_SMALL_FAST_MODEL": "fake", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}
    for name in ("ANTHROPIC_API_KEY", "PATTON_GATE_CONFIG", "PATTON_GATE_STATE", "PATTON_GATE_ROOT"):
        env.pop(name, None)
    argv = [claude, "-p", f"{MAIN}.", "--model", "fake", "--output-format", "stream-json", "--verbose",
            "--permission-mode", "bypassPermissions", "--plugin-dir", plugin_dir, "--include-hook-events",
            "--no-session-persistence"]
    try:
        done = subprocess.run(argv, cwd=root, env=env, capture_output=True, text=True, timeout=300)
    finally:
        fake.stop()
        verifier.stop()
    # glob skips hidden directories, and the plugin's data lives under ~/.claude/plugins/data
    ledgers = [os.path.join(d, "verify.jsonl") for d, _, files in os.walk(home) if "verify.jsonl" in files]
    entries = [json.loads(line) for path in ledgers for line in Path(path).read_text().splitlines() if line.strip()]
    return {"rc": done.returncode, "stdout": done.stdout, "stderr": done.stderr[-600:], "entries": entries,
            "requests": fake.requests, "verifier_calls": verifier.calls}


def check(case: dict, out: dict, root: Path) -> list[str]:
    problems = []
    decisions = [e.get("decision") for e in out["entries"]]
    if decisions != case["decisions"]:
        problems.append(f"decisions {decisions}, expected {case['decisions']} (rc {out['rc']}, {out['stderr']!r})")
    reached = any("Patton verify: an independent check" in json.dumps(r.get("messages", [])) for r in out["requests"])
    if reached != case["reason_reached"]:
        problems.append(f"the refusal {'did not reach' if case['reason_reached'] else 'reached'} the worker")
    if "system_message" in case and case["system_message"] not in out["stdout"]:
        problems.append(f"no message to the person containing {case['system_message']!r}")
    for kind, text in out["verifier_calls"]:
        if kind == "verifier" and "All done" in text:
            problems.append("the Verifier saw the worker's final message")
    for rel, present in case.get("files", {}).items():
        if (root / rel).exists() != present:
            problems.append(f"{rel} {'missing' if present else 'present'}")
    if any(e.get("digest") and len(e["digest"]) != 40 for e in out["entries"]):
        problems.append("a digest is not a git tree hash")
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
        for i, case in enumerate(scenarios()):
            if args.only and args.only not in case["name"]:
                continue
            base = Path(tmp) / f"case{i}"
            root, home = base / "project", base / "home"
            root.mkdir(parents=True)
            home.mkdir()
            (root / "README.md").write_text(f"# Notes\n{NOTE}\n`scratch.txt` is scratch output and can go.\n")
            (root / "keep.txt").write_text("ledger\n")
            (root / "scratch.txt").write_text("tmp\n")
            for command in (["init", "-q"], ["add", "-A"],
                            ["-c", "user.email=t@example.org", "-c", "user.name=t", "commit", "-q", "-m", "start"]):
                subprocess.run(["git", "-C", str(root), *command], check=True, capture_output=True)
            out = run(args.claude, plugin_dir, case, root, home)
            problems = check(case, out, root)
            failures += bool(problems)
            print(f"{'ok  ' if not problems else 'FAIL'} {case['name']}")
            for p in problems:
                print("      -", p)
    print("all checks passed" if not failures else f"{failures} scenario(s) failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
