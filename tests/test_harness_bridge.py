"""The shared harness bridge and the scripted chat upstream that every harness trace uses."""

from pathlib import Path
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.request

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lab"))

from patton_lab.harness import bridge, fake_chat  # noqa: E402


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "work")
        os.makedirs(self.root)
        Path(self.root, "keep.txt").write_text("only copy\n")
        self.env = {"HOME": self.tmp.name, "PATTON_GATE_ROOT": self.root,
                    "PATTON_GATE_STATE": os.path.join(self.tmp.name, "state"),
                    "XDG_CONFIG_HOME": os.path.join(self.tmp.name, "config")}

    def call(self, payload, dialect="claude"):
        out = io.StringIO()
        code = bridge.main([dialect], io.StringIO(payload if isinstance(payload, str) else json.dumps(payload)),
                           out, self.env)
        return code, out.getvalue()

    def bash(self, command):
        return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": self.root, "session_id": "s1"}

    def test_a_change_to_a_starting_file_is_blocked_with_the_reason(self):
        code, out = self.call(self.bash("rm keep.txt"))
        self.assertEqual(code, 0)
        decision = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("keep.txt", decision["permissionDecisionReason"])
        self.assertTrue(Path(self.root, "keep.txt").exists())

    def test_a_read_passes_silently(self):
        self.assertEqual(self.call(self.bash("cat keep.txt")), (0, ""))
        self.assertEqual(self.call({"tool_name": "Read", "tool_input": {}, "cwd": self.root}), (0, ""))

    def test_a_new_file_passes(self):
        self.assertEqual(self.call(self.bash("echo hi > new.txt")), (0, ""))

    def test_an_unreadable_payload_blocks(self):
        self.assertEqual(self.call("{not json")[0], 2)

    def test_an_unknown_dialect_blocks(self):
        self.assertEqual(self.call(self.bash("ls"), dialect="nope")[0], 2)

    def test_a_gate_that_cannot_load_blocks(self):
        old = bridge.GATE
        bridge.GATE = Path(self.tmp.name) / "missing.py"
        try:
            self.assertEqual(self.call(self.bash("ls"))[0], 2)
        finally:
            bridge.GATE = old


class Analyst:
    """A chat endpoint that answers the T1 analyst, allowing scratch.log alone with a quote from the notes."""

    def __init__(self, evidence):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        self.calls = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.calls += 1
                user = body["messages"][1]["content"]
                paths = [l[2:] for l in user.split("## Paths the gate flagged\n")[1].splitlines() if l.startswith("- ")]
                content = json.dumps({"paths": [{"path": p, "action": "allow" if p == "scratch.log" else "escalate",
                                                 "severity": "low", "evidence": evidence} for p in paths]})
                data = json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class TurnTests(unittest.TestCase):
    """Stops and prompts through the bridge reach the plugin's own turn rules, and the bridge caps refusals itself."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "work")
        os.makedirs(os.path.join(self.root, "tests"))
        Path(self.root, "keep.txt").write_text("only copy\n")
        Path(self.root, "scratch.log").write_text("tmp\n")
        Path(self.root, "README.md").write_text("# Notes\n`scratch.log` is scratch output and can go.\n")
        self.state = os.path.join(self.tmp.name, "state")
        self.env = {"HOME": self.tmp.name, "PATTON_GATE_ROOT": self.root, "PATTON_GATE_STATE": self.state,
                    "XDG_CONFIG_HOME": os.path.join(self.tmp.name, "config"),
                    "PATTON_GATE_MISSION": "Tidy the project and keep the ledger."}

    def configure(self, config):
        path = Path(self.env["XDG_CONFIG_HOME"], "patton-gate", "config.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config))

    def call(self, payload, dialect="claude", env=None):
        out = io.StringIO()
        code = bridge.main([dialect], io.StringIO(json.dumps(payload)), out, {**self.env, **(env or {})})
        return code, out.getvalue()

    def stop(self, message="All done.", env=None):
        return self.call({"hook_event_name": "Stop", "cwd": self.root, "session_id": "s1",
                          "last_assistant_message": message, "stop_hook_active": False}, env=env)

    def bash(self, command):
        return self.call({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command},
                          "cwd": self.root, "session_id": "s1", "tool_use_id": "t"})

    def test_the_first_event_starts_the_session_with_the_labs_mission(self):
        self.configure({"discipline": {"check_before_stop": True}})
        self.assertEqual(self.stop(), (0, ""))
        self.assertTrue(Path(self.state, "manifest.json").exists())
        self.assertEqual(json.loads(Path(self.state, "prompts.json").read_text()),
                         ["Tidy the project and keep the ledger."])

    def test_a_stop_after_an_unchecked_change_is_refused_once_then_allowed(self):
        self.configure({"discipline": {"check_before_stop": True}})
        self.bash("echo hi > new.txt")
        code, out = self.stop()
        self.assertEqual((code, json.loads(out)["decision"]), (0, "block"))
        self.assertIn("ran none of the project's checks", json.loads(out)["reason"])
        self.assertEqual(self.stop(), (0, ""))

    def test_the_bridge_stops_refusing_at_its_own_cap(self):
        self.configure({"discipline": {"check_before_stop": True, "max_rounds": 10}})
        self.bash("echo hi > new.txt")
        outs = [self.stop(env={"PATTON_BRIDGE_STOP_CAP": "2"})[1] for _ in range(4)]
        self.assertEqual([bool(o) for o in outs], [True, True, False, False])

    def test_a_stop_the_bridge_cannot_check_is_allowed(self):
        self.configure({"discipline": {"check_before_stop": True}})
        old = bridge.GATE
        bridge.GATE = Path(self.tmp.name) / "missing.py"
        try:
            self.assertEqual(self.stop(), (0, ""))
        finally:
            bridge.GATE = old

    def test_a_reminder_reaches_a_dialect_that_can_carry_it(self):
        self.configure({"discipline": {"reminders": True}, "files": {"protect": False}})
        code, out = self.call({"hook_event_name": "PreToolUse", "tool_name": "Write", "cwd": self.root,
                               "session_id": "s1", "tool_use_id": "w",
                               "tool_input": {"file_path": os.path.join(self.root, "tests", "test_a.py"),
                                              "content": "x"}})
        self.assertEqual(code, 0)
        decision = json.loads(out)["hookSpecificOutput"]
        self.assertNotIn("permissionDecision", decision)
        self.assertIn("is a test", decision["additionalContext"])

    def test_the_sentinel_marks_a_finished_answer(self):
        code, out = self.call({"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": self.root,
                               "session_id": "s1", "tool_input": {"command": "rm keep.txt"}},
                              env={"PATTON_BRIDGE_SENTINEL": "1"})
        lines = out.splitlines()
        self.assertEqual(lines[-1], bridge.SENTINEL)
        self.assertEqual(json.loads(lines[0])["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_triage_lets_a_change_the_notes_allow_through(self):
        analyst = Analyst("`scratch.log` is scratch output and can go.")
        self.addCleanup(analyst.close)
        self.configure({"triage": {"on": True, "endpoint": analyst.url, "model": "m"}})
        self.assertEqual(self.bash("rm scratch.log"), (0, ""))
        self.assertEqual(analyst.calls, 1)
        code, out = self.bash("rm keep.txt")
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(analyst.calls, 2)
        self.assertTrue(Path(self.root, "keep.txt").exists())


class FakeChatTests(unittest.TestCase):
    def post(self, port, body):
        request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions",
                                         data=json.dumps(body).encode(), headers={"content-type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.read().decode()

    def test_steps_come_back_as_tool_calls_then_text_in_both_modes(self):
        fake = fake_chat.FakeChat([{"tool": "bash", "args": {"command": "ls"}}, {"text": "done"}])
        port = fake.start()
        self.addCleanup(fake.stop)
        first = json.loads(self.post(port, {"messages": [{"role": "user", "content": "go"}]}))
        call = first["choices"][0]["message"]["tool_calls"][0]["function"]
        self.assertEqual((call["name"], json.loads(call["arguments"])), ("bash", {"command": "ls"}))
        streamed = self.post(port, {"stream": True, "messages": [{"role": "tool", "content": "Patton gate: no"}]})
        self.assertIn('"content": "done"', streamed)
        self.assertTrue(streamed.rstrip().endswith("data: [DONE]"))
        self.assertTrue(fake.saw("Patton gate: no"))
        self.assertFalse(fake.saw("never sent"))


if __name__ == "__main__":
    unittest.main()
