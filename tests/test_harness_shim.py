"""The fail-closed shim in front of the bridge: a crash, a hang or a missing python3 blocks a tool call."""

from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

REPO = Path(__file__).resolve().parents[1]
SHIM = REPO / "lab" / "patton_lab" / "harness" / "shim" / "patton-hook.sh"
# what each dialect's refusal looks like: (exit code, a test on stdout and stderr)
REFUSALS = {
    "claude": (2, lambda out, err: out == "" and "could not check" in err),
    "dsh": (2, lambda out, err: out == "" and "could not check" in err),
    "gemini_cli": (0, lambda out, err: json.loads(out)["decision"] == "deny"),
    "open_interpreter": (0, lambda out, err: json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"),
    "goose": (0, lambda out, err: json.loads(out)["decision"] == "block"),
    "cline": (2, lambda out, err: json.loads(out)["cancel"] is True),
}


class ShimTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.root = base / "work"
        self.root.mkdir()
        (self.root / "keep.txt").write_text("only copy\n")
        self.env = {"PATH": os.environ["PATH"], "HOME": str(base), "PATTON_GATE_ROOT": str(self.root),
                    "PATTON_GATE_STATE": str(base / "state"), "XDG_CONFIG_HOME": str(base / "config")}
        self.crash = base / "crash.py"
        self.crash.write_text("raise SystemExit(1)\n")
        self.hang = base / "hang.py"
        self.hang.write_text("import time\ntime.sleep(60)\n")

    def run_shim(self, dialect, payload, mode=None, **env):
        argv = ["dash", str(SHIM), dialect] + ([mode] if mode else [])
        done = subprocess.run(argv, input=json.dumps(payload), capture_output=True, text=True, timeout=30,
                              env={**self.env, **env})
        return done.returncode, done.stdout.strip(), done.stderr

    def payload(self, command):
        return {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command},
                "cwd": str(self.root), "session_id": "s1"}

    def test_a_finished_answer_passes_through_without_the_mark(self):
        code, out, _ = self.run_shim("claude", self.payload("rm keep.txt"))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(self.run_shim("claude", self.payload("cat keep.txt"))[:2], (0, ""))

    def test_every_failure_blocks_in_each_dialects_own_form(self):
        failures = {"python3 missing": {"PATTON_PYTHON": "/nonexistent/python3"},
                    "crash": {"PATTON_SHIM_BRIDGE": str(self.crash)},
                    "hang": {"PATTON_SHIM_BRIDGE": str(self.hang), "PATTON_HOOK_LIMIT": "1"}}
        for dialect, (want, looks) in REFUSALS.items():
            for why, env in failures.items():
                with self.subTest(dialect=dialect, why=why):
                    start = time.monotonic()
                    code, out, err = self.run_shim(dialect, self.payload("rm keep.txt"), **env)
                    self.assertEqual(code, want)
                    self.assertTrue(looks(out, err), (out, err))
                    self.assertLess(time.monotonic() - start, 10)

    def test_a_stop_hook_that_fails_lets_the_agent_stop(self):
        for env in ({"PATTON_PYTHON": "/nonexistent/python3"}, {"PATTON_SHIM_BRIDGE": str(self.crash)}):
            with self.subTest(env=env):
                self.assertEqual(self.run_shim("gemini_cli", {}, "stop", **env)[:2], (0, ""))


if __name__ == "__main__":
    unittest.main()
