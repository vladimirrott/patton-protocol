"""The goose dialect through the real bridge and gate: python3 -m unittest patton_lab.harness.goose.test_dialect"""

from pathlib import Path
import io
import json
import os
import tempfile
import unittest

from patton_lab.harness import bridge
from patton_lab.harness.dialects import goose


class GooseDialectTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = os.path.join(tmp.name, "work")
        os.makedirs(self.root)
        Path(self.root, "keep.txt").write_text("only copy\n")
        self.env = {"HOME": tmp.name, "PATTON_GATE_ROOT": self.root,
                    "PATTON_GATE_STATE": os.path.join(tmp.name, "state"),
                    "XDG_CONFIG_HOME": os.path.join(tmp.name, "config")}

    def call(self, tool, tool_input, raw=None):
        payload = raw if raw is not None else json.dumps(
            {"event": "PreToolUse", "session_id": "s1", "matcher_context": tool, "tool_name": tool,
             "tool_input": tool_input, "working_dir": self.root, "tool_call_id": "c1"})
        out = io.StringIO()
        code = bridge.main(["goose"], io.StringIO(payload), out, self.env)
        return code, out.getvalue()

    def assert_blocked(self, result, name):
        code, out = result
        self.assertEqual(code, 0)
        decision = json.loads(out)
        self.assertEqual(decision["decision"], "block")
        self.assertIn(name, decision["reason"])
        self.assertTrue(decision["reason"].startswith("Patton gate"))

    def test_shell_rm_of_a_starting_file_blocks(self):
        self.assert_blocked(self.call("shell", {"command": "rm /work/keep.txt".replace("/work", self.root)}), "keep.txt")

    def test_shell_read_and_new_file_pass(self):
        self.assertEqual(self.call("shell", {"command": "ls " + self.root}), (0, ""))
        self.assertEqual(self.call("shell", {"command": f"echo hi > {self.root}/new.txt"}), (0, ""))

    def test_write_and_edit_of_a_starting_file_block(self):
        self.assert_blocked(self.call("write", {"path": f"{self.root}/keep.txt", "content": "x"}), "keep.txt")
        self.assert_blocked(self.call("edit", {"path": "keep.txt", "before": "only", "after": "no"}), "keep.txt")

    def test_write_of_a_new_file_passes(self):
        self.assertEqual(self.call("write", {"path": f"{self.root}/other.txt", "content": "x"}), (0, ""))

    def test_reads_and_unknown_tools_pass_without_the_gate(self):
        self.assertEqual(goose.parse({"tool_name": "tree", "tool_input": {"path": "."}, "working_dir": "/w"}), None)
        self.assertEqual(goose.parse({"event": "Stop", "session_id": "s"}), None)

    def test_garbage_blocks_with_exit_2(self):
        self.assertEqual(self.call("shell", {}, raw="{nope")[0], 2)


if __name__ == "__main__":
    unittest.main()
