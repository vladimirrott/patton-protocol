"""The patton-gate plugin keeps the session-start copy of every protected file a call it lets through changes.

An operator who approves the wrong deletion loses nothing that `patton_gate.py restore` cannot put back. The study
measured the gate with a careful operator; this is the net under a careless one.
"""

from io import StringIO
from pathlib import Path
import json
import os
import unittest

from test_gate_config import ConfigCase
from test_gate_plugin import gate


class OriginalsCase(ConfigCase):
    def restore(self, *args):
        out = StringIO()
        rc = gate.restore_command(list(args), out, {**self.env})
        return rc, out.getvalue()

    def originals(self):
        return self.state_dir() / "originals"


class KeepTests(OriginalsCase):
    def test_an_asked_change_keeps_the_file_as_it_was_and_the_question_says_so(self):
        self.start()
        result = self.bash("rm notes.txt")
        self.assertEqual(self.decision(result), "ask")
        self.assertEqual((self.originals() / "notes.txt").read_text(), "keep me")
        self.assertIn("restore", self.reason(result))

    def test_a_careless_approval_can_be_undone(self):
        self.start()
        self.bash("rm -rf src notes.txt")
        os.remove(self.root / "notes.txt")
        os.remove(self.root / "src" / "app.py")
        rc, text = self.restore("--all")
        self.assertEqual(rc, 0, text)
        self.assertEqual((self.root / "notes.txt").read_text(), "keep me")
        self.assertEqual((self.root / "src" / "app.py").read_text(), "print(1)")

    def test_only_the_first_version_is_kept(self):
        self.start()
        self.write(self.root / "notes.txt", "w1")
        (self.root / "notes.txt").write_text("second")
        self.hook({"hook_event_name": "PostToolUse", "tool_name": "Write", "tool_use_id": "w1",
                   "tool_input": {"file_path": str(self.root / "notes.txt")}})
        self.assertIsNone(self.decision(self.write(self.root / "notes.txt", "w2")))  # approved earlier: no question
        self.assertEqual((self.originals() / "notes.txt").read_text(), "keep me")

    def test_a_file_created_during_the_session_is_not_kept(self):
        self.start()
        (self.root / "late.txt").write_text("x")
        self.assertIsNone(self.decision(self.bash("rm late.txt")))
        self.assertFalse((self.originals() / "late.txt").exists())

    def test_a_refused_change_keeps_nothing(self):
        self.start()
        self.assertEqual(self.decision(self.bash("rm notes.txt", mode="bypassPermissions")), "deny")
        self.assertFalse((self.originals() / "notes.txt").exists())

    def test_past_the_size_limit_nothing_is_kept_and_the_question_does_not_promise_a_copy(self):
        (self.root / "big.bin").write_bytes(b"x" * (2 << 20))
        self.user_config({"files": {"keep_originals_mb": 1}})
        self.start()
        result = self.bash("rm big.bin")
        self.assertFalse((self.originals() / "big.bin").exists())
        self.assertNotIn("restore", self.reason(result))
        self.assertIn("too large", json.dumps(json.loads((self.state_dir() / "originals.json").read_text())))

    def test_zero_turns_keeping_off(self):
        self.user_config({"files": {"keep_originals_mb": 0}})
        self.start()
        self.bash("rm notes.txt")
        self.assertFalse(self.originals().exists())

    def test_a_project_file_may_keep_more_but_not_less(self):
        self.project_config({"files": {"keep_originals_mb": 0}})
        config, _s, ignored = gate.load_config(str(self.root), {**self.env})
        self.assertEqual(config["files"]["keep_originals_mb"], 200)
        self.assertEqual([i["field"] for i in ignored], ["files.keep_originals_mb"])

    def test_a_symlink_is_kept_as_a_link(self):
        os.symlink("notes.txt", self.root / "link")
        self.start()
        self.bash("rm link")
        self.assertEqual(os.readlink(self.originals() / "link"), "notes.txt")


class RestoreTests(OriginalsCase):
    def test_without_arguments_it_lists_what_it_can_restore(self):
        self.start()
        self.bash("rm notes.txt")
        os.remove(self.root / "notes.txt")
        rc, text = self.restore()
        self.assertEqual(rc, 0)
        self.assertIn("notes.txt", text)
        self.assertIn("missing", text)
        self.assertFalse((self.root / "notes.txt").exists())

    def test_restore_never_overwrites_newer_work_without_keeping_it(self):
        self.start()
        self.bash("rm notes.txt")
        (self.root / "notes.txt").write_text("newer work")
        rc, _text = self.restore("notes.txt")
        self.assertEqual(rc, 0)
        self.assertEqual((self.root / "notes.txt").read_text(), "keep me")
        self.assertEqual((self.root / "notes.txt.before-restore").read_text(), "newer work")

    def test_a_path_that_was_not_kept_is_an_error(self):
        self.start()
        rc, text = self.restore("nothing.txt")
        self.assertEqual(rc, 1)
        self.assertIn("nothing.txt", text)

    def test_a_path_that_was_not_kept_in_a_session_with_copies_is_an_error(self):
        self.start()
        self.bash("rm notes.txt")
        rc, text = self.restore("notes.txt", "other.txt")
        self.assertEqual(rc, 1)
        self.assertIn("not kept: other.txt", text)
        self.assertIn("restored notes.txt", text)

    def test_a_kept_path_cannot_escape_the_project(self):
        self.start()
        self.bash("rm notes.txt")
        index = self.state_dir() / "originals.json"
        data = json.loads(index.read_text())
        data["kept"]["../escape.txt"] = data["kept"].pop("notes.txt")
        index.write_text(json.dumps(data))
        (self.state_dir() / "escape.txt").write_text("x")  # the copy exists, so only the escape check can stop it
        rc, text = self.restore("--all")
        self.assertEqual(rc, 1)
        self.assertFalse((self.root.parent / "escape.txt").exists())


if __name__ == "__main__":
    unittest.main()
