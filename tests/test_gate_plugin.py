"""The patton-gate plugin: packaging, the product gate's behaviour in daily use, and its fail-closed paths.

The studied gate (gate/patton_gate.py) is frozen. The plugin ships a product gate that keeps its classifier and
decision rules; tests/test_gate_corpus.py runs the whole bypass corpus against both (PATTON_GATE_DIR).
"""

from io import StringIO
from pathlib import Path
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN = REPO_ROOT / "plugins" / "patton-gate"
SCRIPT = PLUGIN / "scripts" / "patton_gate.py"


def load_gate():
    spec = importlib.util.spec_from_file_location("patton_gate_plugin", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["patton_gate_plugin"] = module  # dataclasses look their module up here
    spec.loader.exec_module(module)
    return module


gate = load_gate()


def corpus_failures(gate_dir):
    env = {**os.environ, "PATTON_GATE_DIR": str(gate_dir), "PYTHONDONTWRITEBYTECODE": "1"}
    done = subprocess.run([sys.executable, "-B", "-m", "unittest", "tests.test_gate_corpus"], cwd=REPO_ROOT,
                          capture_output=True, text=True, env=env, timeout=300)
    return done.returncode, done.stderr[-2000:]


class PackagingTests(unittest.TestCase):
    def test_the_marketplace_lists_the_plugin_from_its_directory(self):
        market = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text())
        self.assertEqual(market["name"], "patton-protocol")
        entry = next(p for p in market["plugins"] if p["name"] == "patton-gate")
        self.assertEqual((REPO_ROOT / entry["source"]).resolve(), PLUGIN.resolve())

    def test_the_manifest_and_the_marketplace_agree_on_name_and_version(self):
        manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())
        market = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text())
        entry = next(p for p in market["plugins"] if p["name"] == "patton-gate")
        self.assertEqual(manifest["name"], "patton-gate")
        where = ("bump VERSION in plugins/patton-gate/scripts/patton_gate.py, version in "
                 "plugins/patton-gate/.claude-plugin/plugin.json and the plugin's version in "
                 ".claude-plugin/marketplace.json together")
        self.assertEqual(manifest["version"], gate.VERSION, where)
        self.assertEqual(entry.get("version", manifest["version"]), manifest["version"], where)

    def test_the_hooks_cover_every_tool_that_changes_files_and_name_a_script_that_exists(self):
        hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text())["hooks"]
        for event in ("PreToolUse", "PostToolUse"):
            self.assertEqual(hooks[event][0]["matcher"], "Bash|Write|Edit|MultiEdit|NotebookEdit|Agent|Task", event)
        self.assertEqual(hooks["PostToolUseFailure"][0]["matcher"], "Bash|Agent|Task")  # Bash: a failed check
        self.assertIn("SessionStart", hooks)
        guarded = {h["command"] for event in ("SessionStart", "PreToolUse", "PostToolUse")
                   for group in hooks[event] for h in group["hooks"]}
        self.assertEqual(len(guarded), 1, "one command for the events that decide")
        command = guarded.pop()
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/scripts/patton_gate.py", command)
        # The events that only count missions must never block: on SubagentStop a block keeps the subagent running.
        for event in ("SubagentStart", "SubagentStop", "PostToolUseFailure"):
            for group in hooks[event]:
                for h in group["hooks"]:
                    self.assertNotIn("onFailure", h, event)
                    self.assertIn("|| exit 0; exec python3", h["command"], event)
                    self.assertIn("${CLAUDE_PLUGIN_ROOT}/scripts/patton_gate.py", h["command"], event)

    def test_the_plugin_needs_nothing_outside_its_directory(self):
        for path in PLUGIN.rglob("*"):
            self.assertFalse(path.is_symlink(), f"{path} is a link; an installed plugin is a copy")

    def test_every_readme_gives_the_same_working_install_command(self):
        market = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text())
        command = f"claude plugin install patton-gate --marketplace vladimirrott/{market['name']}"
        self.assertEqual(market["name"], "patton-protocol", "the marketplace name is the repository name")
        for readme in (REPO_ROOT / "README.md", PLUGIN / "README.md", REPO_ROOT / "gate" / "README.md"):
            self.assertIn(command, readme.read_text(), readme)

    def test_the_readme_lists_exactly_the_directories_the_gate_skips(self):
        text = (PLUGIN / "README.md").read_text()
        line = next(l for l in text.split("\n- ") if l.startswith("Files inside"))
        listed = set(re.findall(r"`([^`]+)`", line.split(": tools rebuild")[0]))
        self.assertEqual(listed, set(gate.SKIP_DIRS))

    @unittest.skipUnless(shutil.which("claude"), "Claude Code is not installed here")
    def test_claude_codes_own_validator_passes(self):
        for target in (PLUGIN, REPO_ROOT):
            done = subprocess.run(["claude", "plugin", "validate", str(target), "--strict"], capture_output=True,
                                  text=True, timeout=120)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_the_whole_bypass_corpus_passes_against_the_product_gate(self):
        rc, tail = corpus_failures(SCRIPT.parent)
        self.assertEqual(rc, 0, tail)


def hook_command():
    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text())["hooks"]
    return hooks["PreToolUse"][0]["hooks"][0]["command"]


class GateCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "project"
        self.home = Path(self.tmp.name) / "home"
        (self.root / "src").mkdir(parents=True)
        self.home.mkdir()
        (self.root / "notes.txt").write_text("keep me")
        (self.root / "src" / "app.py").write_text("print(1)")
        self.env = {"CLAUDE_PROJECT_DIR": str(self.root), "XDG_STATE_HOME": str(self.home / "state"),
                    "HOME": str(self.home)}
        self.session = "sess-1"

    def hook(self, event, env=None):
        out = StringIO()
        payload = {"session_id": self.session, "cwd": str(self.root), **event}
        rc = gate.main(StringIO(json.dumps(payload)), out, {**self.env, **(env or {})})
        text = out.getvalue()
        return rc, (json.loads(text) if text.strip() else None)

    def start(self, source="startup"):
        return self.hook({"hook_event_name": "SessionStart", "source": source})

    def bash(self, command, tool_use_id="t1", env=None, mode="default"):
        return self.hook({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command},
                          "tool_use_id": tool_use_id, "permission_mode": mode}, env)

    def decision(self, result):
        _rc, out = result
        return None if out is None else out["hookSpecificOutput"]["permissionDecision"]

    def state_dir(self):
        return self.home / "state" / "patton-gate" / "sessions" / self.session

    def ledger(self):
        return [json.loads(line) for line in (self.state_dir() / "ledger.jsonl").read_text().splitlines()]


class DailyUseTests(GateCase):
    def test_with_no_configuration_the_gate_protects_the_project_and_keeps_state_per_session(self):
        self.start()
        self.assertTrue((self.state_dir() / "manifest.json").exists())
        self.assertEqual(self.decision(self.bash("rm notes.txt")), "ask")

    def test_nothing_protected_gives_no_decision_so_the_users_own_permissions_apply(self):
        self.start()
        self.assertIsNone(self.decision(self.bash("ls -la && cat notes.txt")))
        self.assertIsNone(self.decision(self.bash("echo hi > new.txt")))

    def test_a_command_the_gate_cannot_read_asks_the_person_instead_of_refusing(self):
        self.start()
        rc, out = self.bash("pytest -q")
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "ask")
        self.assertIn("cannot tell which files", out["hookSpecificOutput"]["permissionDecisionReason"])
        entry = self.ledger()[-1]
        self.assertEqual((entry["verdict"], entry["opaque"]), ("asked", True))

    def test_asking_about_unreadable_commands_never_stalls_the_session(self):
        self.start()
        for i in range(gate.DENIAL_LIMIT + 2):
            self.assertEqual(self.decision(self.bash("make test", tool_use_id=f"t{i}")), "ask")
        self.assertEqual(self.decision(self.bash("rm notes.txt", tool_use_id="last")), "ask")

    def test_an_unreadable_command_the_person_approved_is_not_asked_again_this_session(self):
        self.start()
        self.assertEqual(self.decision(self.bash("pytest -q", tool_use_id="a1")), "ask")
        self.hook({"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "pytest -q"},
                   "tool_use_id": "a1"})
        self.assertIsNone(self.decision(self.bash("pytest -q", tool_use_id="a2")), "the same command runs on")
        self.assertEqual(self.decision(self.bash("pytest -q -x", tool_use_id="a3")), "ask", "only the exact command")

    def test_an_unreadable_command_that_was_declined_is_asked_again(self):
        self.start()
        self.bash("make clean", tool_use_id="d1")  # declined: no PostToolUse follows
        self.assertEqual(self.decision(self.bash("make clean", tool_use_id="d2")), "ask")

    def test_the_studied_refusal_is_one_setting_away(self):
        self.start()
        self.assertEqual(self.decision(self.bash("pytest -q", env={"PATTON_GATE_OPAQUE": "deny"})), "deny")

    def test_an_approved_unreadable_command_is_recorded_as_run(self):
        self.start()
        self.bash("pytest -q", tool_use_id="p1")
        self.hook({"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "pytest -q"},
                   "tool_use_id": "p1"})
        self.assertEqual(self.ledger()[-1]["verdict"], "approved")

    def test_a_resumed_session_keeps_its_first_manifest(self):
        self.start()
        (self.root / "made-later.txt").write_text("new")
        self.start(source="resume")
        self.assertIsNone(self.decision(self.bash("rm made-later.txt")), "a file made in the session stays unprotected")

    def test_a_file_created_after_the_session_started_is_not_protected(self):
        self.start()
        (self.root / "scratch.log").write_text("tmp")
        self.assertIsNone(self.decision(self.bash("rm scratch.log")))

    def test_the_repositorys_history_is_protected(self):
        (self.root / ".git").mkdir()
        (self.root / ".git" / "HEAD").write_text("ref: refs/heads/main")
        self.start()
        self.assertEqual(self.decision(self.bash("rm -rf .git")), "ask")

    def test_dependency_and_build_directories_are_not_protected(self):
        (self.root / "node_modules" / "left-pad").mkdir(parents=True)
        (self.root / "node_modules" / "left-pad" / "index.js").write_text("x")
        (self.root / "target" / "debug").mkdir(parents=True)
        (self.root / "target" / "debug" / "app").write_text("x")
        self.start()
        self.assertIsNone(self.decision(self.bash("rm -rf node_modules target")))

    def test_study_mode_behaves_as_the_study_measured(self):
        approver = Path(self.tmp.name) / "approve.py"
        approver.write_text("import json,sys\nreq=json.load(sys.stdin)\nprint(json.dumps({'approved': [], 'denied': req['paths']}))\n")
        env = {"PATTON_GATE_APPROVER": f"{sys.executable} {approver}"}
        self.hook({"hook_event_name": "SessionStart"}, env)
        self.assertEqual(self.decision(self.bash("ls", env=env)), "allow")
        self.assertEqual(self.decision(self.bash("pytest", tool_use_id="t2", env=env)), "deny")
        self.assertEqual(self.decision(self.bash("rm notes.txt", tool_use_id="t3", env=env)), "deny")


class BypassModeTests(GateCase):
    """Under bypassPermissions Claude Code auto-approved a hook's ask for `rm -rf .git` (2.1.303, headless), while it
    honoured the same ask for an ordinary file. The gate cannot rely on ask there, so it refuses, as studied."""

    def test_a_protected_change_is_refused_with_a_way_forward(self):
        self.start()
        rc, out = self.bash("rm -rf .git notes.txt", mode="bypassPermissions")
        decision = out["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("bypassPermissions", decision["permissionDecisionReason"])
        self.assertIn("notes.txt", decision["permissionDecisionReason"])

    def test_an_unreadable_command_is_refused_and_asks_for_a_rewrite(self):
        self.start()
        rc, out = self.bash("pytest -q", mode="bypassPermissions")
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("Rewrite", out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_nothing_protected_still_gives_no_decision(self):
        self.start()
        self.assertIsNone(self.decision(self.bash("ls", mode="bypassPermissions")))

    def test_the_other_modes_ask(self):
        self.start()
        for i, mode in enumerate(("default", "acceptEdits", "plan", "auto")):
            self.assertEqual(self.decision(self.bash("rm notes.txt", tool_use_id=f"m{i}", mode=mode)), "ask", mode)


class ManifestTests(GateCase):
    def test_special_files_are_never_opened(self):
        sock = socket.socket(socket.AF_UNIX)
        sock_path = self.root / "agent.sock"
        sock.bind(str(sock_path))
        self.addCleanup(sock.close)
        os.mkfifo(self.root / "pipe")
        started = time.monotonic()
        self.start()
        self.assertLess(time.monotonic() - started, 5, "a FIFO must not block the session start")
        files = json.loads((self.state_dir() / "manifest.json").read_text())["files"]
        self.assertIn("notes.txt", files)
        self.assertNotIn("pipe", files)
        self.assertNotIn("agent.sock", files)

    def test_a_large_tree_starts_fast(self):
        big = self.root / "data"
        for i in range(200):
            d = big / f"d{i}"
            d.mkdir(parents=True)
            for j in range(100):
                (d / f"f{j}").write_bytes(b"x" * 4096)
        started = time.monotonic()
        self.start()
        self.assertLess(time.monotonic() - started, 5, "20 000 files must list in seconds; the studied gate hashed them")

    def test_past_the_listing_cap_every_existing_file_is_protected(self):
        saved = gate.MAX_LISTED
        gate.MAX_LISTED = 1
        try:
            self.start()
            self.assertEqual(self.decision(self.bash("rm notes.txt")), "ask")
            self.assertEqual(self.decision(self.bash("rm src/app.py", tool_use_id="t2")), "ask")
        finally:
            gate.MAX_LISTED = saved


class FailClosedTests(GateCase):
    def test_a_crash_in_the_gate_denies_the_call(self):
        def boom(*_a, **_k):
            raise RuntimeError("broken")
        saved = gate.decide
        gate.decide = boom
        try:
            self.start()
            rc, out = self.bash("ls")
        finally:
            gate.decide = saved
        self.assertEqual(rc, 0)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("RuntimeError", out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_without_a_session_id_or_a_project_the_gate_refuses(self):
        out = StringIO()
        gate.main(StringIO(json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                       "tool_input": {"command": "rm x"}})), out, {"HOME": str(self.home)})
        self.assertEqual(json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_a_missing_python_blocks_the_call_and_says_why(self):
        bin_dir = Path(self.tmp.name) / "bin"
        bin_dir.mkdir()
        for tool in ("sh", "env", "printf", "cat"):
            found = shutil.which(tool)
            if found:
                os.symlink(found, bin_dir / tool)
        command = hook_command().replace("${CLAUDE_PLUGIN_ROOT}", str(PLUGIN))
        done = subprocess.run(["/bin/sh", "-c", command], input="{}", capture_output=True, text=True,
                              env={"PATH": str(bin_dir)})
        self.assertEqual(done.returncode, 2, "exit 2 is the code that blocks a tool call")
        self.assertIn("python3", done.stderr)

    def test_the_hook_command_runs_the_gate_when_python_is_there(self):
        command = hook_command().replace("${CLAUDE_PLUGIN_ROOT}", str(PLUGIN))
        env = {**os.environ, **self.env}
        for key in ("PATTON_GATE_STATE", "PATTON_GATE_ROOT", "PATTON_GATE_APPROVER", "CLAUDE_PLUGIN_DATA"):
            env.pop(key, None)
        start = json.dumps({"hook_event_name": "SessionStart", "session_id": "s", "cwd": str(self.root)})
        self.assertEqual(subprocess.run(["/bin/sh", "-c", command], input=start, capture_output=True, text=True,
                                        env=env).returncode, 0)
        payload = json.dumps({"hook_event_name": "PreToolUse", "session_id": "s", "cwd": str(self.root),
                              "tool_name": "Bash", "tool_input": {"command": "pytest"}, "tool_use_id": "x"})
        done = subprocess.run(["/bin/sh", "-c", command], input=payload, capture_output=True, text=True, env=env)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)["hookSpecificOutput"]["permissionDecision"], "ask")


class CommandLineTests(unittest.TestCase):
    def test_run_from_a_terminal_it_explains_itself_instead_of_waiting_for_a_hook_event(self):
        done = subprocess.run(["script", "-qec", f"{sys.executable} {SCRIPT}", "/dev/null"], capture_output=True,
                              text=True, timeout=20, stdin=subprocess.DEVNULL)
        self.assertIn("patton-gate", done.stdout)
        self.assertIn("log", done.stdout)

    def test_log_works_when_a_glob_expands_to_two_installed_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            done = subprocess.run([sys.executable, str(SCRIPT), str(SCRIPT), "log"], capture_output=True, text=True,
                                  timeout=20, stdin=subprocess.DEVNULL, env={"PATH": os.environ["PATH"], "HOME": tmp})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("No gate activity", done.stdout)

    def test_the_hook_refuses_a_python_older_than_310(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "python3"
            # A stand-in for Python 3.9: the real interpreter, reporting 3.9 to whatever code it runs, so the test
            # fails if the hook stops asking the version.
            fake.write_text(f"#!/bin/sh\nif [ \"$1\" = -c ]; then shift; exec {sys.executable} -S -c "
                            f"'import sys; sys.version_info = (3, 9, 18, \"final\", 0); exec(sys.argv[1])' \"$@\"; fi\n"
                            f"exec {sys.executable} \"$@\"\n")
            fake.chmod(0o755)
            for tool in ("sh", "printf", "cat"):
                found = shutil.which(tool)
                if found:
                    os.symlink(found, Path(tmp) / tool)
            command = hook_command().replace("${CLAUDE_PLUGIN_ROOT}", str(PLUGIN))
            done = subprocess.run(["/bin/sh", "-c", command], input="{}", capture_output=True, text=True,
                                  env={"PATH": tmp})
        self.assertEqual(done.returncode, 2)
        self.assertIn("3.10", done.stderr)


class LogTests(GateCase):
    def test_the_log_finds_the_installed_plugins_data_without_being_told(self):
        data = self.home / ".claude" / "plugins" / "data" / "patton-gate-patton-protocol"
        env = {"CLAUDE_PROJECT_DIR": str(self.root), "CLAUDE_PLUGIN_DATA": str(data), "HOME": str(self.home)}
        out = StringIO()
        gate.main(StringIO(json.dumps({"hook_event_name": "SessionStart", "session_id": "s9", "cwd": str(self.root)})),
                  out, env)
        gate.main(StringIO(json.dumps({"hook_event_name": "PreToolUse", "session_id": "s9", "cwd": str(self.root),
                                       "tool_name": "Bash", "tool_input": {"command": "rm notes.txt"},
                                       "tool_use_id": "q"})), out, env)
        done = subprocess.run([sys.executable, str(SCRIPT), "log"], capture_output=True, text=True,
                              env={"PATH": os.environ["PATH"], "HOME": str(self.home)})
        self.assertIn("session s9", done.stdout)
        self.assertIn("rm notes.txt", done.stdout)

    def test_the_log_command_shows_what_the_gate_did_this_session(self):
        self.start()
        self.bash("rm notes.txt")
        done = subprocess.run([sys.executable, str(SCRIPT), "log"], capture_output=True, text=True,
                              env={**os.environ, **self.env})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("rm notes.txt", done.stdout)
        self.assertIn("asked", done.stdout)

    def test_with_no_sessions_yet_the_log_says_how_to_get_one(self):
        done = subprocess.run([sys.executable, str(SCRIPT), "log"], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, env={"PATH": os.environ["PATH"], "HOME": str(self.home)})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("No gate activity", done.stdout)
        self.assertIn("start a Claude Code session", done.stdout)


class UnwritableStateTests(GateCase):
    def test_a_manifest_that_could_not_be_written_is_refused_with_the_place_and_the_cause(self):
        blocker = self.home / "state"
        blocker.write_text("a file where the state directory should be")  # every write under it fails
        self.start()
        _rc, out = self.bash("rm notes.txt")
        reason = out["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("manifest is missing or unreadable", reason)
        self.assertIn(str(self.state_dir()), reason)
        self.assertIn("could not write", reason)


if __name__ == "__main__":
    unittest.main()
