"""What the Codex port relies on Codex itself to do, checked against the installed binary for $0.

Each test runs the real `codex exec` against the scripted Responses endpoint with a hook, on the host, in a scratch
CODEX_HOME and work directory. The scripted commands only touch that directory. Skipped when the binary is not
installed (CI) or reports another version than the runner pins, because the facts are version-specific: a new
Codex release means rerunning these before any paid run.
"""

from pathlib import Path
import json
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lab"))

from patton_lab.codex import runner  # noqa: E402
from patton_lab.codex.fake_upstream import FakeUpstream  # noqa: E402

BINARY = runner.default_binary()
WRAP = 'async function t(f){ try { return JSON.stringify(await f()); } catch (e) { return "refused: " + e; } }\n'


def _version() -> str:
    try:
        return subprocess.run([str(BINARY), "--version"], capture_output=True, text=True, timeout=30).stdout
    except OSError:
        return ""


HOOK = r'''
import json, os, sys, time
ev = json.load(sys.stdin)
open(os.environ["CONTRACT_LOG"], "a").write(json.dumps(ev) + "\n")
cmd = str((ev.get("tool_input") or {}).get("command", ""))
if ev.get("hook_event_name") != "PreToolUse":
    sys.exit(0)
if "DENY" in cmd:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": "contract deny"}}))
elif "CRASH" in cmd:
    sys.exit(1)
elif "SLOW" in cmd:
    time.sleep(4)
elif "BAREALLOW" in cmd:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}}))
elif "REWRITE" in cmd:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                             "updatedInput": {"command": "cd " + ev["cwd"] + " && " + cmd.replace(" REWRITE", "")}}}))
'''


@unittest.skipUnless(runner.CODEX_VERSION in _version(), f"Codex {runner.CODEX_VERSION} is not installed here")
class CodexContractTests(unittest.TestCase):
    def run_codex(self, cells: list[str]) -> tuple[Path, list[dict], list[dict]]:
        """Run one session of JavaScript cells; return the work dir, the hook events and the model requests."""
        tmp = Path(tempfile.mkdtemp(prefix="codex-contract-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        home, work = tmp / "home", tmp / "work"
        home.mkdir()
        (work / "sub").mkdir(parents=True)
        (work / "keep.txt").write_text("keep\n")
        (work / "sub" / "deep.txt").write_text("deep\n")
        (tmp / "hook.py").write_text(HOOK)
        hook = [{"type": "command", "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(tmp / 'hook.py'))}",
                 "timeout": 2}]
        (home / "hooks.json").write_text(json.dumps({"hooks": {"PreToolUse": [{"hooks": hook}],
                                                               "PostToolUse": [{"hooks": hook}]}}))
        (home / "catalog.json").write_text(json.dumps({"models": [runner.catalog_entry(runner.MODELS[0])]}))
        fake = FakeUpstream([{"exec": WRAP + cell} for cell in cells] + [{"text": "done"}])
        port = fake.start()
        self.addCleanup(fake.stop)
        config = runner.codex_config(runner.MODELS[0], f"http://127.0.0.1:{port}")
        (home / "config.toml").write_text(config.replace("/home/user/.codex/catalog.json", str(home / "catalog.json")))
        env = {"HOME": str(home), "CODEX_HOME": str(home), "PATH": "/usr/bin:/bin", runner.PLACEHOLDER_ENV: "x",
               "CONTRACT_LOG": str(tmp / "hooks.jsonl")}
        subprocess.run([str(BINARY), "exec", "--json", "--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox",
                        "--dangerously-bypass-hook-trust", "go"], cwd=work, env=env, capture_output=True, text=True,
                       timeout=180, stdin=subprocess.DEVNULL)
        log = tmp / "hooks.jsonl"
        events = [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []
        return work, events, fake.requests

    def cell(self, cmd: str, **extra) -> str:
        args = json.dumps({"cmd": cmd, **extra})
        return f"text(await t(() => tools.exec_command({args})));"

    def test_nested_shell_and_patch_calls_reach_the_hook_with_the_names_the_adapter_reads(self):
        patch = "*** Begin Patch\n*** Add File: added.txt\n+x\n*** End Patch\n"
        _work, events, _ = self.run_codex([self.cell("ls"), f"text(await t(() => tools.apply_patch({json.dumps(patch)})));"])
        pre = [(e["tool_name"], e["tool_input"]["command"]) for e in events if e["hook_event_name"] == "PreToolUse"]
        self.assertEqual(pre, [("Bash", "ls"), ("apply_patch", patch)])
        self.assertTrue(all(e["permission_mode"] == "bypassPermissions" for e in events))

    def test_a_deny_blocks_the_call(self):
        work, events, _ = self.run_codex([self.cell("rm keep.txt # DENY")])
        self.assertIn("rm keep.txt # DENY", [e["tool_input"]["command"] for e in events], "the hook never ran")
        self.assertTrue((work / "keep.txt").exists())

    def test_a_crash_a_timeout_and_a_bare_allow_all_let_the_call_run(self):
        # Codex hooks fail open: the adapter must answer, every time, before its timeout.
        work, events, _ = self.run_codex([self.cell("touch crashed # CRASH"), self.cell("touch slow # SLOW"),
                                           self.cell("touch bare # BAREALLOW")])
        seen = [e["tool_input"]["command"] for e in events if e["hook_event_name"] == "PreToolUse"]
        self.assertEqual(seen, ["touch crashed # CRASH", "touch slow # SLOW", "touch bare # BAREALLOW"],
                         "a file made without its hook running proves nothing about failing open")
        for name in ("crashed", "slow", "bare"):
            self.assertTrue((work / name).exists(), name)

    def test_the_hook_never_sees_workdir_and_a_rewrite_brings_the_command_back(self):
        work, events, _ = self.run_codex([self.cell("rm deep.txt", workdir="sub"),
                                           self.cell("touch here.txt REWRITE", workdir="sub")])
        pre = [e for e in events if e["hook_event_name"] == "PreToolUse"]
        self.assertEqual(pre[0]["tool_input"], {"command": "rm deep.txt"})
        self.assertEqual(pre[0]["cwd"], str(work))
        self.assertFalse((work / "sub" / "deep.txt").exists(), "workdir was honored without the hook seeing it")
        self.assertTrue((work / "here.txt").exists(), "the rewrite did not move the command back to cwd")

    def test_the_study_config_leaves_only_the_shell_and_patch_tools(self):
        _work, _events, requests = self.run_codex([self.cell("ls")])
        entry = {"request": requests[0]}
        tools = runner.offered_tools([entry])
        for wanted in ("exec", "exec_command", "apply_patch"):
            self.assertIn(wanted, tools)
        for unwanted in ("spawn_agent", "create_goal", "web_search"):
            self.assertNotIn(unwanted, tools)


if __name__ == "__main__":
    unittest.main()
