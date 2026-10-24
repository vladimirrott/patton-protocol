"""The Cline and OpenCode dialects through the real bridge and gate, on the host (no harness, no container).

    python3 -m unittest patton_lab.harness.cline.test_dialect      # from lab/
"""

import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from patton_lab.harness import bridge


class DialectTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = os.path.join(tmp.name, "work")
        os.makedirs(self.root)
        Path(self.root, "keep.txt").write_text("only copy\n")
        self.env = {"HOME": tmp.name, "PATTON_GATE_ROOT": self.root, "PATTON_GATE_STATE": os.path.join(tmp.name, "s")}

    def call(self, dialect, payload):
        out = io.StringIO()
        code = bridge.main([dialect], io.StringIO(json.dumps(payload)), out, self.env)
        return code, json.loads(out.getvalue())

    def cline(self, name, tool_input):
        return self.call("cline", {"hookName": "tool_call", "taskId": "t", "workspaceRoots": [self.root],
                                   "tool_call": {"id": "1", "name": name, "input": tool_input}})

    def opencode(self, tool, args):
        return self.call("opencode", {"tool": tool, "args": args, "sessionID": "s", "directory": self.root})

    def test_cline_rewrites_a_refused_command_to_print_the_refusal(self):
        for shape in ({"commands": ["rm keep.txt"]}, {"commands": "rm keep.txt"}, {"command": "rm keep.txt"},
                      ["ls", "rm keep.txt"], {"commands": [{"command": "rm", "args": ["keep.txt"]}]}):
            code, out = self.cline("run_commands", shape)
            self.assertEqual(code, 0, shape)
            command = out["overrideInput"]["commands"][0]
            self.assertTrue(command.startswith("printf ") and command.endswith("; exit 1"), shape)
            self.assertIn("Patton gate", command)

    def test_cline_allows_reads_and_new_files_and_cancels_edits_and_bad_input(self):
        self.assertEqual(self.cline("run_commands", {"commands": ["ls"]}), (0, {"cancel": False}))
        self.assertEqual(self.cline("read_files", {"files": [{"path": "/x"}]}), (0, {"cancel": False}))
        self.assertEqual(self.cline("editor", {"path": "new.txt", "new_text": "x"}), (0, {"cancel": False}))
        self.assertTrue(self.cline("editor", {"path": "keep.txt", "new_text": "x"})[1]["cancel"])
        self.assertTrue(self.cline("apply_patch", {"input": "*** Begin Patch\n*** Delete File: keep.txt\n"
                                                            "*** End Patch"})[1]["cancel"])
        code, out = self.cline("apply_patch", {"input": "not a patch"})
        self.assertEqual((code, out["cancel"]), (2, True))
        self.assertTrue(self.cline("unknown_tool", {})[1]["cancel"])

    def test_opencode_blocks_changes_and_allows_the_rest(self):
        self.assertEqual(self.opencode("bash", {"command": "ls"}), (0, {"allow": True}))
        self.assertEqual(self.opencode("read", {"filePath": "keep.txt"}), (0, {"allow": True}))
        self.assertFalse(self.opencode("bash", {"command": "rm keep.txt"})[1]["allow"])
        self.assertFalse(self.opencode("edit", {"filePath": os.path.join(self.root, "keep.txt")})[1]["allow"])
        self.assertFalse(self.opencode("write", {"filePath": "keep.txt", "content": ""})[1]["allow"])
        self.assertFalse(self.opencode("apply_patch", {"patchText": "*** Begin Patch\n*** Update File: keep.txt\n"
                                                                    "*** End Patch"})[1]["allow"])
        self.assertEqual(self.opencode("apply_patch", {"patchText": "junk"})[0], 2)
        self.assertFalse(self.opencode("mystery", {})[1]["allow"])


if __name__ == "__main__":
    unittest.main()
