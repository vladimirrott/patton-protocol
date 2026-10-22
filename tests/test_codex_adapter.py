"""The Codex hook adapter: the frozen gate's decisions, translated to what Codex 0.159 accepts.

Codex runs a hook's allow only with a rewrite, ignores an ask, and lets a call run when the hook crashes or times
out, so every test here checks the exact JSON the adapter prints, and that it prints a refusal on every error.
"""

from io import StringIO
from pathlib import Path
import json
import os
import shlex
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "gate"))
sys.path.insert(0, str(REPO / "lab"))

import patton_gate  # noqa: E402
from patton_lab.codex import adapter  # noqa: E402

PATCH_ADD = "*** Begin Patch\n*** Add File: new.txt\n+hello\n*** End Patch\n"


def approver(directory: Path, approve: tuple[str, ...]) -> str:
    """A command approver that grants exactly the paths in `approve` and logs each request."""
    script = directory / "approver.py"
    script.write_text(
        "import json, sys\nreq = json.load(sys.stdin)\n"
        f"open({str(directory / 'requests.jsonl')!r}, 'a').write(json.dumps(req) + '\\n')\n"
        f"ok = {list(approve)!r}\n"
        "paths = req['paths']\n"
        "print(json.dumps({'approved': [p for p in paths if p in ok], 'denied': [p for p in paths if p not in ok]}))\n")
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"


class AdapterCase(unittest.TestCase):
    approve: tuple[str, ...] = ()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(os.path.realpath(self.tmp.name))
        self.root, self.state, self.aux = base / "work", base / "state", base / "aux"
        for d in (self.root, self.state, self.aux):
            d.mkdir()
        (self.root / "keep.csv").write_text("only copy\n")
        (self.root / "junk.log").write_text("log\n")
        (self.root / "sub").mkdir()
        (self.root / "sub" / "deep.md").write_text("deep\n")
        patton_gate.write_manifest(str(self.root), str(self.state))
        self.env = {"PATTON_GATE_STATE": str(self.state), "PATTON_GATE_ROOT": str(self.root),
                    "PATTON_GATE_APPROVER": approver(self.aux, self.approve)}

    def tearDown(self):
        self.tmp.cleanup()

    def event(self, tool, command, name="PreToolUse", tool_use_id="exec-1"):
        return {"hook_event_name": name, "tool_name": tool, "tool_input": {"command": command},
                "cwd": str(self.root), "tool_use_id": tool_use_id, "session_id": "s", "turn_id": "t",
                "model": "gpt-6.1-sol", "permission_mode": "bypassPermissions", "transcript_path": None}

    def run_adapter(self, event, env=None, gate=patton_gate):
        out = StringIO()
        code = adapter.main(StringIO(event if isinstance(event, str) else json.dumps(event)), out,
                            {**self.env, **(env or {})}, gate)
        self.assertEqual(code, 0, "Codex reads exit 1 as no answer and runs the call, so the adapter exits 0")
        return json.loads(out.getvalue()) if out.getvalue().strip() else None

    def decision(self, out):
        spec = out["hookSpecificOutput"]
        self.assertEqual(spec["hookEventName"], "PreToolUse")
        return spec["permissionDecision"]

    def requests(self):
        path = self.aux / "requests.jsonl"
        return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []


class BashTests(AdapterCase):
    def test_an_allowed_command_runs_from_the_directory_the_gate_checked(self):
        out = self.run_adapter(self.event("Bash", "ls -A"))
        self.assertEqual(self.decision(out), "allow")
        self.assertEqual(out["hookSpecificOutput"]["updatedInput"],
                         {"command": f"cd {shlex.quote(str(self.root))} && ls -A"})

    def test_a_refusal_passes_through_with_its_reason(self):
        out = self.run_adapter(self.event("Bash", "rm keep.csv"))
        self.assertEqual(self.decision(out), "deny")
        self.assertIn("keep.csv", out["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertNotIn("updatedInput", out["hookSpecificOutput"])
        self.assertTrue((self.root / "keep.csv").exists())

    def test_a_command_the_gate_cannot_read_is_refused(self):
        out = self.run_adapter(self.event("Bash", "python3 -c 'import os'"))
        self.assertEqual(self.decision(out), "deny")


class ApprovedBashTests(AdapterCase):
    approve = ("junk.log",)

    def test_an_approved_change_is_allowed_with_the_rewrite(self):
        out = self.run_adapter(self.event("Bash", "rm junk.log"))
        self.assertEqual(self.decision(out), "allow")
        self.assertTrue(out["hookSpecificOutput"]["updatedInput"]["command"].endswith("&& rm junk.log"))
        self.assertEqual([r["paths"] for r in self.requests()], [["junk.log"]])


class PatchTests(AdapterCase):
    approve = ("junk.log",)

    def test_the_paths_of_every_operation_are_read(self):
        patch = ("*** Begin Patch\n*** Update File: keep.csv\n@@\n-only copy\n+changed\n*** Delete File: junk.log\n"
                 "*** Add File: new.txt\n+x\n*** Update File: sub/deep.md\n*** Move to: sub/moved.md\n@@\n-deep\n+d\n"
                 "*** End Patch\n")
        self.assertEqual(adapter.patch_paths(patch), ["keep.csv", "junk.log", "new.txt", "sub/deep.md", "sub/moved.md"])

    def test_a_patch_that_touches_a_protected_file_goes_to_the_operator_as_one_request(self):
        patch = "*** Begin Patch\n*** Delete File: junk.log\n*** Delete File: keep.csv\n*** End Patch\n"
        out = self.run_adapter(self.event("apply_patch", patch))
        self.assertEqual(self.decision(out), "deny")
        self.assertEqual([sorted(r["paths"]) for r in self.requests()], [["junk.log", "keep.csv"]])
        self.assertIn("keep.csv", out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_an_approved_patch_gets_no_answer_so_it_runs_unchanged(self):
        out = self.run_adapter(self.event("apply_patch", "*** Begin Patch\n*** Delete File: junk.log\n*** End Patch\n"))
        self.assertIsNone(out)

    def test_a_patch_that_only_adds_new_files_runs_without_a_request(self):
        self.assertIsNone(self.run_adapter(self.event("apply_patch", PATCH_ADD)))
        self.assertEqual(self.requests(), [])

    def test_adding_a_file_over_one_that_existed_is_a_change_to_it(self):
        patch = "*** Begin Patch\n*** Add File: keep.csv\n+overwritten\n*** End Patch\n"
        self.assertEqual(self.decision(self.run_adapter(self.event("apply_patch", patch))), "deny")

    def test_a_patch_the_adapter_cannot_read_is_refused_with_a_way_forward(self):
        for patch in ("", "delete keep.csv please", "*** Begin Patch\n*** End Patch\n",
                      "*** Add File: unheaded.txt\n+x\n"):  # no header: Codex would reject it, so the gate does
            out = self.run_adapter(self.event("apply_patch", patch))
            self.assertEqual(self.decision(out), "deny", patch)
            self.assertIn("Rewrite", out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_a_heredoc_patch_through_the_shell_is_read_as_a_patch_and_rewritten(self):
        command = "apply_patch <<'EOF'\n*** Begin Patch\n*** Delete File: keep.csv\n*** End Patch\nEOF\n"
        self.assertEqual(self.decision(self.run_adapter(self.event("Bash", command))), "deny")
        self.assertEqual([r["paths"] for r in self.requests()], [["keep.csv"]])
        added = "apply_patch <<'EOF'\n" + PATCH_ADD + "EOF\n"
        out = self.run_adapter(self.event("Bash", added))
        self.assertEqual(self.decision(out), "allow")
        self.assertEqual(out["hookSpecificOutput"]["updatedInput"]["command"],
                         f"cd {shlex.quote(str(self.root))} && {added}")

    def test_a_heredoc_patch_after_a_cd_resolves_its_paths_in_that_directory(self):
        command = "cd sub && apply_patch <<\"PATCH\"\n*** Begin Patch\n*** Delete File: deep.md\n*** End Patch\nPATCH"
        self.assertEqual(self.decision(self.run_adapter(self.event("Bash", command))), "deny")
        self.assertEqual([r["paths"] for r in self.requests()], [["sub/deep.md"]])


class FailClosedTests(AdapterCase):
    def test_a_crash_in_the_gate_is_a_refusal(self):
        class Broken:
            @staticmethod
            def main(stdin, stdout, environ):
                raise RuntimeError("boom")
        out = self.run_adapter(self.event("Bash", "ls"), gate=Broken)
        self.assertEqual(self.decision(out), "deny")
        self.assertIn("boom", out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_a_gate_answer_that_is_neither_allow_nor_deny_is_a_refusal(self):
        for printed in ("", json.dumps({"hookSpecificOutput": {"permissionDecision": "ask",
                                                              "permissionDecisionReason": "asks"}})):
            class Odd:
                @staticmethod
                def main(stdin, stdout, environ, printed=printed):
                    stdout.write(printed)
                    return 0
            out = self.run_adapter(self.event("Bash", "ls"), gate=Odd)
            self.assertEqual(self.decision(out), "deny", printed)
            self.assertNotIn("updatedInput", out["hookSpecificOutput"])

    def test_unreadable_gate_output_is_a_refusal(self):
        class Garbled:
            @staticmethod
            def main(stdin, stdout, environ):
                stdout.write("not json")
                return 0
        self.assertEqual(self.decision(self.run_adapter(self.event("Bash", "ls"), gate=Garbled)), "deny")

    def test_unreadable_input_is_a_refusal(self):
        self.assertEqual(self.decision(self.run_adapter("{not json")), "deny")

    def test_a_tool_the_adapter_does_not_know_is_refused(self):
        self.assertEqual(self.decision(self.run_adapter(self.event("spawn_agent", "x"))), "deny")

    def test_a_missing_gate_file_is_a_refusal(self):
        out = StringIO()
        adapter.main(StringIO(json.dumps(self.event("Bash", "ls"))), out,
                     {**self.env, "PATTON_GATE_DIR": str(self.aux / "nowhere")})
        self.assertEqual(json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecision"], "deny")


class OtherEventTests(AdapterCase):
    def test_post_tool_use_reaches_the_gate_and_prints_nothing(self):
        seen = []

        class Spy:
            @staticmethod
            def main(stdin, stdout, environ):
                seen.append(json.loads(stdin.read()))
                stdout.write(json.dumps({"hookSpecificOutput": {"permissionDecision": "allow"}}))
                return 0
        self.assertIsNone(self.run_adapter(self.event("apply_patch", PATCH_ADD, name="PostToolUse"), gate=Spy))
        self.assertEqual(seen[0]["tool_name"], "Bash")
        self.assertEqual(seen[0]["tool_input"]["command"], "rm -f -- new.txt")

    def test_session_start_writes_the_manifest(self):
        (self.state / "manifest.json").unlink()
        self.assertIsNone(self.run_adapter({"hook_event_name": "SessionStart", "cwd": str(self.root),
                                            "session_id": "s", "source": "startup"}))
        self.assertTrue((self.state / "manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
