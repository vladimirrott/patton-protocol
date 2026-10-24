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
