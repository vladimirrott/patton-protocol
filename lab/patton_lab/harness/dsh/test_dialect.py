"""The dsh dialect through the real bridge and gate: python3 -m unittest patton_lab.harness.dsh.test_dialect"""

from pathlib import Path
import io
import json
import os
import tempfile
import unittest

from patton_lab.harness import bridge
from patton_lab.harness.dialects import dsh


class DshDialectTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = os.path.join(tmp.name, "work")
        os.makedirs(os.path.join(self.root, "sub"))
        Path(self.root, "keep.txt").write_text("only copy\n")
        Path(self.root, "sub", "inner.txt").write_text("inner\n")
        self.env = {"HOME": tmp.name, "PATTON_GATE_ROOT": self.root,
                    "PATTON_GATE_STATE": os.path.join(tmp.name, "state"),
                    "XDG_CONFIG_HOME": os.path.join(tmp.name, "config")}

    def call(self, tool, tool_input, raw=None):
        payload = raw if raw is not None else json.dumps(
            {"session_id": "s1", "transcript_path": "", "cwd": self.root, "hook_event_name": "PreToolUse",
             "tool_name": tool, "tool_input": tool_input, "tool_use_id": "c1"})
        out = io.StringIO()
        code = bridge.main(["dsh"], io.StringIO(payload), out, self.env)
        return code, out.getvalue()

    def assert_denied(self, result, name):
        code, out = result
        self.assertEqual(code, 0)
        decision = json.loads(out)["hookSpecificOutput"]
        self.assertEqual((decision["hookEventName"], decision["permissionDecision"]), ("PreToolUse", "deny"))
        self.assertIn(name, decision["permissionDecisionReason"])

    def test_bash_rm_of_a_starting_file_is_denied(self):
        self.assert_denied(self.call("bash", {"description": "d", "command": "rm keep.txt"}), "keep.txt")

    def test_bash_workdir_is_the_cwd_the_gate_resolves_against(self):
        self.assert_denied(self.call("bash", {"description": "d", "command": "rm inner.txt",
                                              "workdir": os.path.join(self.root, "sub")}), "sub/inner.txt")

    def test_bash_read_and_new_file_pass(self):
        self.assertEqual(self.call("bash", {"description": "d", "command": "ls"}), (0, ""))
        self.assertEqual(self.call("bash", {"description": "d", "command": "echo hi > new.txt"}), (0, ""))

    def test_write_and_edit_of_a_starting_file_are_denied(self):
        self.assert_denied(self.call("write", {"file_path": f"{self.root}/keep.txt", "content": "x"}), "keep.txt")
        self.assert_denied(self.call("edit", {"file_path": f"{self.root}/keep.txt", "old_string": "only",
                                              "new_string": "no"}), "keep.txt")

    def test_reads_pass_without_the_gate(self):
        self.assertIsNone(dsh.parse({"tool_name": "read", "tool_input": {"file_path": "x"}, "cwd": "/w"}))

    def test_garbage_blocks_with_exit_2(self):
        self.assertEqual(self.call("bash", {}, raw="{nope")[0], 2)


if __name__ == "__main__":
    unittest.main()
