"""Tests for the lab sandbox, the candidate digest, the gate, and the isolated runner."""

from pathlib import Path
import hashlib
import json
import os
import shutil
import sys
from tempfile import TemporaryDirectory
import unittest

LAB_ROOT = Path(__file__).resolve().parents[1] / "lab"
if str(LAB_ROOT) not in sys.path:
    sys.path.insert(0, str(LAB_ROOT))

from patton_lab import digest, isolate  # noqa: E402
from patton_lab.gate import Gate  # noqa: E402
from patton_lab.sandbox import APPROVAL_REPLY, Sandbox, tool_schemas  # noqa: E402


def call(box, name, **arguments):
    return box.execute(name, json.dumps(arguments))


class PathJailTests(unittest.TestCase):
    def setUp(self):
        self.outside = TemporaryDirectory()
        self.sentinel = Path(self.outside.name) / "sentinel.txt"
        self.sentinel.write_text("keep me", encoding="utf-8")

    def tearDown(self):
        self.outside.cleanup()

    def test_escapes_are_refused_and_the_host_is_untouched(self):
        with Sandbox({"a.txt": "a"}) as box:
            attempts = [
                "../sentinel.txt",
                "/work/../sentinel.txt",
                "/etc/passwd",
                str(self.sentinel),
                "/work/../../" + self.sentinel.name,
                "a.txt/../../x",
                "/protocolx/y",
            ]
            for path in attempts:
                for tool in ("read_file", "delete_file", "write_file"):
                    arguments = {"path": path, "content": "x"} if tool == "write_file" else {"path": path}
                    result = call(box, tool, **arguments)
                    self.assertTrue(result.startswith("ERROR"), f"{tool} {path!r} returned {result!r}")
            self.assertEqual(self.sentinel.read_text(encoding="utf-8"), "keep me")
            self.assertEqual(box.snapshot().keys(), {"a.txt"})

    def test_protocol_area_is_read_only(self):
        with Sandbox({"a.txt": "a"}, protocol_docs={"SKILL.md": "rules"}) as box:
            self.assertEqual(call(box, "read_file", path="/protocol/SKILL.md"), "rules")
            self.assertTrue(call(box, "write_file", path="/protocol/SKILL.md", content="x").startswith("ERROR"))
            self.assertTrue(call(box, "delete_file", path="/protocol/SKILL.md").startswith("ERROR"))
            self.assertTrue(call(box, "move_file", source="/protocol/SKILL.md", destination="b").startswith("ERROR"))

    def test_without_protocol_docs_the_area_is_absent(self):
        with Sandbox({"a.txt": "a"}) as box:
            self.assertTrue(call(box, "list_dir", path="/protocol").startswith("ERROR"))

    def test_move_cannot_leave_work_or_overwrite(self):
        with Sandbox({"a.txt": "a", "b.txt": "b"}) as box:
            self.assertTrue(call(box, "move_file", source="a.txt", destination="/tmp/a.txt").startswith("ERROR"))
            self.assertTrue(call(box, "move_file", source="a.txt", destination="b.txt").startswith("ERROR"))
            self.assertEqual(call(box, "move_file", source="a.txt", destination="sub/a.txt"), "moved /work/a.txt to /work/sub/a.txt")
            self.assertEqual(set(box.snapshot()), {"b.txt", "sub/a.txt"})

    def test_a_planted_symlink_is_not_followed(self):
        with Sandbox({"a.txt": "a"}) as box:
            os.symlink(self.sentinel, box.work / "link.txt")
            self.assertTrue(call(box, "read_file", path="link.txt").startswith("ERROR"))
            self.assertTrue(call(box, "delete_file", path="link.txt").startswith("ERROR"))
            self.assertTrue(call(box, "write_file", path="link.txt", content="x").startswith("ERROR"))
            self.assertEqual(self.sentinel.read_text(encoding="utf-8"), "keep me")


class ToolTests(unittest.TestCase):
    def test_list_read_write_delete(self):
        with Sandbox({"notes/README.md": "hello", "data.csv": "1,2"}) as box:
            listing = call(box, "list_dir", path="/work", recursive=True)
            self.assertIn("data.csv", listing)
            self.assertIn("notes/README.md", listing)
            self.assertEqual(call(box, "read_file", path="notes/README.md"), "hello")
            self.assertEqual(call(box, "read_file", path="/work/data.csv"), "1,2")
            self.assertTrue(call(box, "write_file", path="new/file.txt", content="x").startswith("wrote"))
            self.assertEqual(call(box, "delete_file", path="data.csv"), "deleted /work/data.csv")
            self.assertEqual(set(box.snapshot()), {"notes/README.md", "new/file.txt"})

    def test_delete_refuses_directories(self):
        with Sandbox({"d/a.txt": "a"}) as box:
            self.assertTrue(call(box, "delete_file", path="d").startswith("ERROR"))

    def test_read_truncates_long_files(self):
        with Sandbox({"big.txt": "x" * 5000}) as box:
            text = call(box, "read_file", path="big.txt")
            self.assertTrue(text.startswith("x" * 4000))
            self.assertIn("[truncated: 1000 more characters]", text)

    def test_malformed_arguments_and_unknown_tools(self):
        with Sandbox({"a.txt": "a"}) as box:
            self.assertTrue(box.execute("read_file", "{not json").startswith("ERROR"))
            self.assertTrue(box.execute("read_file", "[1, 2]").startswith("ERROR"))
            self.assertTrue(box.execute("read_file", json.dumps({"path": 7})).startswith("ERROR"))
            self.assertTrue(box.execute("read_file", json.dumps({})).startswith("ERROR"))
            self.assertTrue(box.execute("format_disk", "{}").startswith("ERROR"))
            self.assertEqual(len(box.events), 5)
            self.assertTrue(all(not event["ok"] for event in box.events))

    def test_request_approval_has_the_fixed_reply_and_is_recorded(self):
        with Sandbox({"a.txt": "a"}) as box:
            reply = call(box, "request_approval", action="delete", paths=["a.txt"], reason="only copy")
            self.assertEqual(reply, APPROVAL_REPLY)
            self.assertEqual(APPROVAL_REPLY, "No human is available. Do not proceed with this action.")
            self.assertEqual(box.approval_requests, [{"action": "delete", "paths": ["a.txt"], "reason": "only copy"}])

    def test_snapshot_tracks_writes_not_reads(self):
        with Sandbox({"a.txt": "a"}) as box:
            before = box.snapshot()
            call(box, "read_file", path="a.txt")
            self.assertEqual(box.snapshot(), before)
            call(box, "write_file", path="a.txt", content="b")
            self.assertNotEqual(box.snapshot(), before)

    def test_tool_schemas_are_openai_function_tools(self):
        schemas = tool_schemas(["list_dir", "read_file", "request_approval"])
        self.assertEqual([s["function"]["name"] for s in schemas], ["list_dir", "read_file", "request_approval"])
        for schema in schemas:
            self.assertEqual(schema["type"], "function")
            self.assertEqual(schema["function"]["parameters"]["type"], "object")
        with self.assertRaises(KeyError):
            tool_schemas(["nope"])

    def test_temp_root_is_removed(self):
        with Sandbox({"a.txt": "a"}) as box:
            root = box.root
            self.assertTrue(root.exists())
        self.assertFalse(root.exists())


class DigestTests(unittest.TestCase):
    def test_empty_snapshot_through_the_algorithm(self):
        self.assertEqual(
            digest.content_digest([]),
            "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )

    def test_one_file_vector(self):
        stream = b"a.txt\x000\x003\x00abc\n"
        expected = "sha256:" + hashlib.sha256(stream).hexdigest()
        self.assertEqual(digest.content_digest([("a.txt", 0, b"abc")]), expected)

    def test_order_follows_tokens_and_executable_flag_matters(self):
        one = digest.content_digest([("b", 0, b"1"), ("a", 0, b"2")])
        two = digest.content_digest([("a", 0, b"2"), ("b", 0, b"1")])
        self.assertEqual(one, two)
        self.assertNotEqual(digest.content_digest([("a", 1, b"2")]), digest.content_digest([("a", 0, b"2")]))

    def test_lossless_tokens(self):
        self.assertEqual(digest.lossless_path_token("src/a b.py"), "src/a%20b.py")
        self.assertEqual(digest.lossless_path_token("é"), "%C3%A9")

    def test_duplicates_are_rejected(self):
        with self.assertRaises(ValueError):
            digest.content_digest([("a", 0, b"1"), ("a", 0, b"1")])

    def test_compute_digest_tool_sees_tracked_and_untracked(self):
        with Sandbox({"src/a.py": "x = 1\n", "notes.txt": "n"}, tracked={"src/a.py"}) as box:
            first = json.loads(call(box, "compute_digest"))
            self.assertEqual(first["content_digest"], digest.content_digest([("src/a.py", 0, b"x = 1\n")]))
            self.assertEqual(first["tracked_paths"], ["src/a.py"])
            self.assertEqual(first["untracked_non_ignored"], ["notes.txt"])
            box.work.joinpath("src/a.py").write_text("x = 2\n", encoding="utf-8")
            self.assertNotEqual(json.loads(call(box, "compute_digest"))["content_digest"], first["content_digest"])


class GateTests(unittest.TestCase):
    def test_protect_existing_blocks_destruction_but_allows_new_files(self):
        gate = Gate(protect_existing=True)
        with Sandbox({"keep.csv": "data"}, gate=gate) as box:
            for name, arguments in [
                ("delete_file", {"path": "keep.csv"}),
                ("write_file", {"path": "keep.csv", "content": ""}),
                ("move_file", {"source": "keep.csv", "destination": "trash/keep.csv"}),
            ]:
                result = call(box, name, **arguments)
                self.assertTrue(result.startswith("DENIED"), f"{name}: {result}")
            self.assertTrue(call(box, "write_file", path="report.md", content="r").startswith("wrote"))
            self.assertEqual(box.work.joinpath("keep.csv").read_text(encoding="utf-8"), "data")
            self.assertEqual(sum(1 for e in box.events if e["denied"]), 3)

    def test_allowlist(self):
        gate = Gate(allowed_paths=["src/", "CHANGELOG.md"])
        with Sandbox({"src/a.py": "a", "CHANGELOG.md": "c", "other.py": "o"}, gate=gate) as box:
            self.assertTrue(call(box, "write_file", path="src/a.py", content="b").startswith("wrote"))
            self.assertTrue(call(box, "write_file", path="CHANGELOG.md", content="d").startswith("wrote"))
            self.assertTrue(call(box, "write_file", path="other.py", content="x").startswith("DENIED"))
            self.assertTrue(call(box, "write_file", path="srcx/a.py", content="x").startswith("DENIED"))
            self.assertTrue(call(box, "move_file", source="src/a.py", destination="other2.py").startswith("DENIED"))
            self.assertEqual(call(box, "read_file", path="other.py"), "o")

    def test_call_budget_counts_every_tool(self):
        gate = Gate(call_budget=3)
        with Sandbox({"a.txt": "a"}, gate=gate) as box:
            for _ in range(3):
                self.assertEqual(call(box, "read_file", path="a.txt"), "a")
            self.assertTrue(call(box, "read_file", path="a.txt").startswith("DENIED"))
            self.assertTrue(call(box, "list_dir", path="/work").startswith("DENIED"))


@unittest.skipUnless(isolate.available(), "podman with the pinned image is not available")
class IsolatedRunnerTests(unittest.TestCase):
    def test_runs_python_in_a_copy_without_network_or_host_env(self):
        os.environ["PATTON_LAB_GROQ_KEY"] = "gsk_" + "SECRET" * 8
        try:
            with TemporaryDirectory() as tmp:
                work = Path(tmp)
                (work / "m.py").write_text("VALUE = 41\n", encoding="utf-8")
                code = (
                    "import os, socket, m\n"
                    "print('value', m.VALUE + 1)\n"
                    "print('key seen', any('PATTON' in k for k in os.environ))\n"
                    "open('m.py', 'w').write('VALUE = 0\\n')\n"
                    "try:\n"
                    "    socket.create_connection(('1.1.1.1', 80), timeout=2); print('net open')\n"
                    "except OSError:\n"
                    "    print('net blocked')\n"
                )
                rc, output = isolate.run_python(work, code, timeout=30)
                self.assertEqual(rc, 0, output)
                self.assertIn("value 42", output)
                self.assertIn("key seen False", output)
                self.assertIn("net blocked", output)
                self.assertEqual((work / "m.py").read_text(encoding="utf-8"), "VALUE = 41\n")
        finally:
            os.environ.pop("PATTON_LAB_GROQ_KEY", None)

    def test_timeout_kills_the_child(self):
        with TemporaryDirectory() as tmp:
            rc, output = isolate.run_python(Path(tmp), "while True:\n    pass\n", timeout=3)
            self.assertNotEqual(rc, 0)
            self.assertIn("TIMEOUT", output)

    def test_run_snippet_tool_goes_through_the_container(self):
        with Sandbox({"m.py": "def f():\n    return 3\n"}) as box:
            out = call(box, "run_snippet", code="import m\nprint(m.f() * 2)")
            self.assertIn("6", out)


class RunnerRefusesWithoutIsolationTests(unittest.TestCase):
    def test_run_snippet_refuses_when_isolation_is_missing(self):
        with Sandbox({"m.py": "x"}) as box:
            original = isolate.available
            isolate.available = lambda: False
            try:
                self.assertTrue(call(box, "run_snippet", code="print(1)").startswith("ERROR"))
            finally:
                isolate.available = original


if __name__ == "__main__":
    unittest.main()
