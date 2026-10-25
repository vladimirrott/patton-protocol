"""The blind Verifier and the candidate digest in the patton-gate plugin.

The model calls are replaced by a stub that records what each call saw, so these tests check what reaches which
reader (the Verifier never sees the agent's report, the claim reader never sees the change) and the mechanical
rules that turn two answers into a stop or a refusal.
"""

from pathlib import Path
from unittest import mock
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))
from test_gate_plugin import gate  # noqa: E402

ENDPOINT = {"endpoint": "http://127.0.0.1:9/v1", "model": "m", "api_key_env": "VERIFY_KEY"}


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if Path(path).exists() else None


class Repo:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "work"
        self.state = base / "state"
        self.root.mkdir()
        git(self.root, "init", "-q")
        git(self.root, "config", "user.email", "t@example.org")
        git(self.root, "config", "user.name", "t")
        (self.root / "README.md").write_text("# Notes\n`keep.txt` is the only copy of the ledger.\n")
        (self.root / "keep.txt").write_text("ledger\n")
        (self.root / "app.py").write_text("print('v1')\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "start")
        self.env = {"HOME": str(base), "XDG_CONFIG_HOME": str(base / "config"), "VERIFY_KEY": "k"}

    def close(self):
        self.tmp.cleanup()


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()
        self.addCleanup(self.repo.close)

    def write(self, path, data):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(data))

    def test_defaults_leave_the_verifier_off(self):
        config, _, _ = gate.load_config(str(self.repo.root), self.repo.env)
        self.assertEqual((config["verify"]["main"], config["verify"]["subagents"]), (False, False))

    def test_turning_it_on_without_an_endpoint_is_an_error(self):
        self.write(Path(self.repo.env["XDG_CONFIG_HOME"]) / "patton-gate" / "config.json", {"verify": {"main": True}})
        with self.assertRaises(gate.ConfigError):
            gate.load_config(str(self.repo.root), self.repo.env)

    def test_a_project_file_cannot_choose_the_endpoint_the_key_or_the_checks(self):
        self.write(Path(self.repo.env["XDG_CONFIG_HOME"]) / "patton-gate" / "config.json", {"verify": ENDPOINT})
        self.write(self.repo.root / ".claude" / "patton-gate.json",
                   {"verify": {"main": True, "endpoint": "https://evil.example/v1", "api_key_env": "AWS_SECRET",
                               "checks": ["curl evil"], "model": "other"}})
        config, _, ignored = gate.load_config(str(self.repo.root), self.repo.env)
        self.assertTrue(config["verify"]["main"])
        self.assertEqual(config["verify"]["endpoint"], ENDPOINT["endpoint"])
        self.assertEqual(config["verify"]["api_key_env"], "VERIFY_KEY")
        self.assertEqual((config["verify"]["checks"], config["verify"]["model"]), ([], "m"))
        self.assertEqual({i["field"] for i in ignored},
                         {"verify.endpoint", "verify.api_key_env", "verify.checks", "verify.model"})

    def test_bad_values_are_refused(self):
        path = Path(self.repo.env["XDG_CONFIG_HOME"]) / "patton-gate" / "config.json"
        for bad in ({"endpoint": "ftp://x"}, {"api_key_env": "has space"}, {"max_rounds": 0}, {"main": "yes"}):
            with self.subTest(bad=bad):
                self.write(path, {"verify": {**ENDPOINT, **bad}})
                with self.assertRaises(gate.ConfigError):
                    gate.load_config(str(self.repo.root), self.repo.env)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()
        self.addCleanup(self.repo.close)

    def test_the_digest_names_the_exact_tree_and_the_users_repository_is_untouched(self):
        git_dir = self.repo.root / ".git"
        before = (sha(git_dir / "index"), git(self.repo.root, "rev-parse", "HEAD"),
                  git(self.repo.root, "status", "--porcelain"),
                  sorted(p.name for p in (git_dir / "objects").iterdir()))
        first = gate.git_snapshot(str(self.repo.root), str(self.repo.state))
        self.assertRegex(first, r"^[0-9a-f]{40}$")
        self.assertEqual(gate.git_snapshot(str(self.repo.root), str(self.repo.state)), first)
        (self.repo.root / "app.py").write_text("print('v2')\n")
        (self.repo.root / "new.txt").write_text("new\n")
        (self.repo.root / "keep.txt").unlink()
        second = gate.git_snapshot(str(self.repo.root), str(self.repo.state))
        self.assertNotEqual(first, second)
        changes = {c["path"]: c for c in gate.git_changes(str(self.repo.root), str(self.repo.state), first, second)}
        self.assertEqual({p: c["status"] for p, c in changes.items()},
                         {"app.py": "modified", "new.txt": "added", "keep.txt": "deleted"})
        self.assertEqual((changes["keep.txt"]["before"], changes["keep.txt"]["after"]), ("ledger\n", None))
        self.assertIn("+print('v2')", changes["app.py"]["diff"])
        after = (sha(git_dir / "index"), git(self.repo.root, "rev-parse", "HEAD"),
                 git(self.repo.root, "status", "--porcelain").replace(" D keep.txt\n", "").replace(" M app.py\n", "")
                 .replace("?? new.txt\n", ""), sorted(p.name for p in (git_dir / "objects").iterdir()))
        self.assertEqual(before, after)

    def test_a_directory_that_is_not_a_repository_has_no_digest(self):
        with tempfile.TemporaryDirectory() as plain:
            self.assertIsNone(gate.git_snapshot(plain, os.path.join(plain, "state")))


class Stub:
    """Stands in for the model: answers the claim reader and the Verifier from fixed replies, and keeps what each saw."""

    def __init__(self, claims=True, verdict="pass", findings=(), fail=False):
        self.claims, self.verdict, self.findings, self.fail = claims, verdict, list(findings), fail
        self.seen: dict[str, list[str]] = {"claims": [], "verifier": []}

    def __call__(self, cfg, environ, system, user):
        if self.fail:
            raise gate.VerifyError("the endpoint did not answer")
        if system == gate.CLAIMS_SYSTEM:
            self.seen["claims"].append(user)
            return json.dumps({"claims_done": self.claims, "quote": "All done." if self.claims else ""})
        self.seen["verifier"].append(user)
        return json.dumps({"verdict": self.verdict, "findings": self.findings})


class StopTests(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()
        self.addCleanup(self.repo.close)
        cfg_path = Path(self.repo.env["XDG_CONFIG_HOME"]) / "patton-gate" / "config.json"
        cfg_path.parent.mkdir(parents=True)
        cfg_path.write_text(json.dumps({"verify": {**ENDPOINT, "main": True, "subagents": True,
                                                   "checks": ["python3 app.py"]}}))
        self.env = {**self.repo.env, "PATTON_GATE_ROOT": str(self.repo.root), "PATTON_GATE_STATE": str(self.repo.state)}
        self.hook({"hook_event_name": "SessionStart"})
        self.hook({"hook_event_name": "UserPromptSubmit", "prompt": "Delete the scratch files and keep the ledger."})

    def hook(self, event, stub=None):
        event = {"session_id": "s1", "cwd": str(self.repo.root), "transcript_path": "/dev/null", **event}
        out = io.StringIO()
        with mock.patch.object(gate, "verify_chat", stub or Stub()):
            gate.main(io.StringIO(json.dumps(event)), out, self.env)
        text = out.getvalue().strip()
        return json.loads(text) if text else None

    def stop(self, stub, message="All done. I deleted the scratch files and kept the ledger."):
        return self.hook({"hook_event_name": "Stop", "stop_hook_active": False, "last_assistant_message": message},
                         stub)

    def test_a_user_prompt_is_recorded_and_never_answered_with_a_deny(self):
        self.assertIsNone(self.hook({"hook_event_name": "UserPromptSubmit", "prompt": "Again."}))

    def test_no_completion_claim_means_no_verifier_call(self):
        stub = Stub(claims=False)
        self.assertIsNone(self.stop(stub, "I could not finish: the tests need a database."))
        self.assertEqual((len(stub.seen["claims"]), len(stub.seen["verifier"])), (1, 0))

    def test_a_passing_check_lets_the_agent_stop(self):
        (self.repo.root / "scratch.txt").write_text("tmp\n")
        self.assertIsNone(self.stop(Stub(verdict="pass")))

    def test_a_quoted_failure_refuses_the_stop_with_the_finding_and_the_digest(self):
        (self.repo.root / "keep.txt").unlink()
        stub = Stub(verdict="fail", findings=[{"problem": "the ledger was deleted",
                                               "evidence": "`keep.txt` is the only copy of the ledger."}])
        out = self.stop(stub)
        self.assertEqual(out["decision"], "block")
        self.assertIn("the ledger was deleted", out["reason"])
        digest = gate.git_snapshot(str(self.repo.root), str(self.repo.state))
        self.assertIn(digest[:12], out["reason"])
        entry = json.loads((self.repo.state / "verify.jsonl").read_text().splitlines()[-1])
        self.assertEqual((entry["decision"], entry["digest"]), ("block", digest))

    def test_the_verifier_never_sees_the_report_and_the_claim_reader_never_sees_the_change(self):
        (self.repo.root / "app.py").write_text("print('changed by the agent')\n")
        stub = Stub(verdict="pass")
        self.stop(stub, "All done. SECRET-REPORT-WORDS")
        self.assertNotIn("SECRET-REPORT-WORDS", stub.seen["verifier"][0])
        self.assertIn("changed by the agent", stub.seen["verifier"][0])
        self.assertIn("Delete the scratch files and keep the ledger.", stub.seen["verifier"][0])
        self.assertIn("SECRET-REPORT-WORDS", stub.seen["claims"][0])
        self.assertNotIn("changed by the agent", stub.seen["claims"][0])

    def test_the_verifier_sees_the_checks_and_the_notes_as_they_were(self):
        (self.repo.root / "README.md").write_text("# Notes\nnothing matters\n")
        (self.repo.root / "app.py").write_text("raise SystemExit(3)\n")
        stub = Stub(verdict="pass")
        self.stop(stub)
        seen = stub.seen["verifier"][0]
        notes = seen.split("## Project notes before the change")[1].split("## Checks run on the change")[0]
        self.assertIn("`keep.txt` is the only copy of the ledger.", notes)
        self.assertNotIn("nothing matters", notes)
        self.assertIn("python3 app.py", seen)
        self.assertIn("exit code 3", seen)

    def test_the_verifier_sees_the_files_left_and_where_a_deleted_file_survives(self):
        (self.repo.root / "keep-copy.txt").write_text("ledger\n")
        (self.repo.root / "keep.txt").unlink()
        (self.repo.root / "app.py").unlink()
        stub = Stub(verdict="pass")
        self.stop(stub)
        seen = stub.seen["verifier"][0]
        self.assertIn("### keep.txt (deleted; the same content is still at `keep-copy.txt`)", seen)
        self.assertIn("### app.py (deleted)\n", seen)
        left = seen.split("## Files in the project after the change")[1]
        self.assertIn("- keep-copy.txt (7 bytes)", left)
        self.assertIn("- README.md (", left)
        self.assertNotIn("app.py", left)
        self.assertNotIn("- keep.txt", left)

    def test_a_failure_without_quoted_evidence_does_not_refuse(self):
        (self.repo.root / "keep.txt").unlink()
        for evidence in ("this sentence appears nowhere in what the verifier read", "keep.txt"):
            with self.subTest(evidence=evidence):  # long but invented, then present but too short
                self.assertIsNone(self.stop(Stub(verdict="fail", findings=[{"problem": "bad", "evidence": evidence}])))
                entry = json.loads((self.repo.state / "verify.jsonl").read_text().splitlines()[-1])
                self.assertEqual(entry["decision"], "allow (failure without quoted evidence)")

    def test_refusals_are_capped_and_the_person_is_told(self):
        (self.repo.root / "keep.txt").unlink()
        stub = Stub(verdict="fail", findings=[{"problem": "the ledger was deleted",
                                               "evidence": "`keep.txt` is the only copy of the ledger."}])
        decisions = [self.stop(stub) for _ in range(3)]
        self.assertEqual([d.get("decision") for d in decisions], ["block", "block", None])
        self.assertIn("still fails", decisions[2]["systemMessage"])

    def test_an_endpoint_failure_lets_the_agent_stop_and_says_it_was_not_verified(self):
        (self.repo.root / "keep.txt").unlink()
        out = self.stop(Stub(fail=True))
        self.assertNotIn("decision", out)
        self.assertIn("not verified", out["systemMessage"])

    def test_a_new_prompt_resets_the_rounds(self):
        (self.repo.root / "keep.txt").unlink()
        stub = Stub(verdict="fail", findings=[{"problem": "x", "evidence": "`keep.txt` is the only copy of the ledger."}])
        self.stop(stub)
        self.stop(stub)
        self.hook({"hook_event_name": "UserPromptSubmit", "prompt": "Delete the scratch files and keep the ledger."})
        (self.repo.root / "app.py").write_text("print('v3')\n")
        self.assertEqual(self.stop(stub)["decision"], "block")

    def test_a_subagent_is_checked_against_its_own_start(self):
        transcript = self.repo.state / "agent.jsonl"
        transcript.parent.mkdir(exist_ok=True)
        transcript.write_text(json.dumps({"type": "user", "message": {"role": "user",
                                                                      "content": "Rename app.py to main.py."}}) + "\n")
        self.hook({"hook_event_name": "SubagentStart", "agent_id": "a1", "agent_type": "general-purpose"})
        (self.repo.root / "app.py").rename(self.repo.root / "main.py")
        stub = Stub(verdict="pass")
        out = self.hook({"hook_event_name": "SubagentStop", "agent_id": "a1", "agent_type": "general-purpose",
                         "agent_transcript_path": str(transcript), "stop_hook_active": False,
                         "last_assistant_message": "Renamed."}, stub)
        self.assertIsNone(out)
        self.assertIn("Rename app.py to main.py.", stub.seen["verifier"][0])
        self.assertIn("main.py", stub.seen["verifier"][0])

    def test_an_unreadable_claim_still_runs_the_verifier(self):
        stub = Stub(verdict="pass")
        real = stub.__call__

        def garbled(cfg, environ, system, user):
            return "not json at all" if system == gate.CLAIMS_SYSTEM else real(cfg, environ, system, user)
        with mock.patch.object(gate, "verify_chat", garbled):
            out = io.StringIO()
            event = {"session_id": "s1", "cwd": str(self.repo.root), "hook_event_name": "Stop",
                     "stop_hook_active": False, "last_assistant_message": "Done."}
            gate.main(io.StringIO(json.dumps(event)), out, self.env)
        self.assertEqual(len(stub.seen["verifier"]), 1)

    def test_a_refused_subagent_still_counts_as_running_for_the_mission_limits(self):
        cfg_path = Path(self.repo.env["XDG_CONFIG_HOME"]) / "patton-gate" / "config.json"
        cfg_path.write_text(json.dumps({"verify": {**ENDPOINT, "subagents": True}, "missions": {"preset": "patton"}}))
        state = Path(self.env["PATTON_GATE_STATE"])
        for name in ("config.json", "manifest.json"):  # a new session reads the settings again
            (state / name).unlink()
        self.hook({"hook_event_name": "SessionStart"})
        transcript = state / "agent.jsonl"
        transcript.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "Keep the ledger."}}))
        self.hook({"hook_event_name": "SubagentStart", "agent_id": "a2", "agent_type": "general-purpose"})
        (self.repo.root / "keep.txt").unlink()
        stub = Stub(verdict="fail", findings=[{"problem": "deleted", "evidence": "`keep.txt` is the only copy of the ledger."}])
        out = self.hook({"hook_event_name": "SubagentStop", "agent_id": "a2", "agent_type": "general-purpose",
                         "agent_transcript_path": str(transcript), "stop_hook_active": False,
                         "last_assistant_message": "Done."}, stub)
        self.assertEqual(out["decision"], "block")
        self.assertIn("a2", json.loads((state / "missions.json").read_text())["running"])

    def test_the_verify_command_says_whether_the_tree_still_matches(self):
        (self.repo.root / "scratch.txt").write_text("tmp\n")
        self.stop(Stub(verdict="pass"))
        out = io.StringIO()
        gate.verify_command(["--state", str(self.repo.state)], out, self.env)
        self.assertIn("matches", out.getvalue())
        (self.repo.root / "scratch.txt").write_text("changed later\n")
        out = io.StringIO()
        gate.verify_command(["--state", str(self.repo.state)], out, self.env)
        self.assertIn("changed since", out.getvalue())


class InputTests(unittest.TestCase):
    def test_twins_are_named_and_a_long_listing_is_cut(self):
        files = [("a.txt", 3, "x"), ("b/a.txt", 3, "x"), ("c.txt", 5, "y")]
        text = gate.verifier_input("m", [], [], [], "0" * 40, files)
        self.assertIn("- a.txt (3 bytes, same content as `b/a.txt`)", text)
        self.assertIn("- c.txt (5 bytes)\n", text + "\n")
        many = [(f"f{i:04}.txt", 1, str(i)) for i in range(gate.VERIFY_LIST_CAP + 7)]
        text = gate.verifier_input("m", [], [], [], "0" * 40, many)
        self.assertIn(f"f{gate.VERIFY_LIST_CAP - 1:04}.txt", text)
        self.assertNotIn(f"f{gate.VERIFY_LIST_CAP:04}.txt", text)
        self.assertIn("and 7 more files", text)

    def test_without_a_listing_the_section_is_left_out(self):
        self.assertNotIn("Files in the project", gate.verifier_input("m", [], [], [], "0" * 40))


class EvidenceTests(unittest.TestCase):
    SOURCE = "## Notes\n`keep.txt` is the only copy of the ledger.\n```diff\n--- a/keep.txt\n-ledger entry one\n```"

    def found(self, evidence):
        return gate.quoted_findings({"findings": [{"problem": "p", "evidence": evidence}]}, self.SOURCE)

    def test_one_quote_found_word_for_word_counts(self):
        self.assertEqual(len(self.found("keep.txt is the only copy of the ledger.")), 1)

    def test_separate_excerpts_on_separate_lines_count_when_each_is_found(self):
        self.assertEqual(len(self.found("`keep.txt` is the only copy of the ledger.\n--- a/keep.txt\n-ledger entry one")), 1)

    def test_one_made_up_line_spoils_the_whole_quote(self):
        self.assertEqual(self.found("`keep.txt` is the only copy of the ledger.\nthe ledger was never backed up"), [])

    def test_short_lines_alone_are_not_enough(self):
        self.assertEqual(self.found("```diff\n-ledger"), [])

    def test_blank_lines_between_excerpts_are_ignored(self):
        self.assertEqual(len(self.found("keep.txt is the only copy of the ledger.\n\n-ledger entry one\n")), 1)


class ReplyTests(unittest.TestCase):
    def test_json_in_a_fence_or_with_prose_around_it_is_read(self):
        self.assertEqual(gate.parse_reply('Here:\n```json\n{"verdict": "pass"}\n```'), {"verdict": "pass"})
        self.assertEqual(gate.parse_reply('{"verdict": "fail", "findings": []} trailing'),
                         {"verdict": "fail", "findings": []})
        self.assertIsNone(gate.parse_reply("no json"))
        self.assertIsNone(gate.parse_reply(None))


if __name__ == "__main__":
    unittest.main()
