"""The gate's decision rules, its failure behavior, its ledger and its two approval transports."""

from io import StringIO
from pathlib import Path
from unittest import mock
import http.server
import json
import os
import shlex
import sys
import tempfile
import threading
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE_DIR = os.environ.get("PATTON_GATE_DIR", str(REPO_ROOT / "gate"))
if GATE_DIR not in sys.path:
    sys.path.insert(0, GATE_DIR)

import patton_gate  # noqa: E402

STOP_SENTENCE = "Do not try another command to reach the same file. State what you need and stop."


_SCRIPTS = iter(range(10_000))


def approver_script(directory: Path, body: str) -> str:
    path = directory / f"approver{next(_SCRIPTS)}.py"
    path.write_text("import json, sys, time\nreq = json.load(sys.stdin)\n" + body)
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(path))}"


class GateCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(os.path.realpath(self.tmp.name))
        self.root, self.state, self.aux = base / "work", base / "state", base / "aux"
        for d in (self.root, self.state, self.aux):
            d.mkdir()
        (self.root / "a.csv").write_text("only copy\n")
        (self.root / "notes.txt").write_text("notes\n")
        (self.root / "dir").mkdir()
        (self.root / "dir" / "b.log").write_text("log\n")
        patton_gate.write_manifest(str(self.root), str(self.state))
        self.env = {"PATTON_GATE_STATE": str(self.state), "PATTON_GATE_ROOT": str(self.root)}

    def tearDown(self):
        self.tmp.cleanup()

    def event(self, tool, **tool_input):
        return {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input,
                "cwd": str(self.root), "tool_use_id": f"id-{tool}"}

    def run_hook(self, event, **extra_env):
        out = StringIO()
        code = patton_gate.main(StringIO(event if isinstance(event, str) else json.dumps(event)), out,
                                {**self.env, **extra_env})
        self.assertEqual(code, 0, "the hook must always exit 0 and say its decision in JSON")
        return json.loads(out.getvalue()) if out.getvalue().strip() else {}

    def verdict(self, result):
        return result["hookSpecificOutput"]["permissionDecision"], result["hookSpecificOutput"]["permissionDecisionReason"]

    def ledger(self):
        path = self.state / "ledger.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


class DecisionTests(GateCase):
    def test_write_to_a_new_path_is_allowed(self):
        decision = patton_gate.decide(self.event("Write", file_path=str(self.root / "new.txt"), content="x"),
                                      patton_gate.GateState.load(str(self.state), str(self.root)))
        self.assertEqual((decision.verdict, decision.paths), ("allow", ()))

    def test_write_to_a_protected_path_needs_approval(self):
        decision = patton_gate.decide(self.event("Write", file_path=str(self.root / "a.csv"), content="x"),
                                      patton_gate.GateState.load(str(self.state), str(self.root)))
        self.assertEqual((decision.verdict, decision.paths), ("approve", ("a.csv",)))

    def test_edit_multiedit_notebookedit_on_protected_need_approval(self):
        state = patton_gate.GateState.load(str(self.state), str(self.root))
        for tool, key in (("Edit", "file_path"), ("MultiEdit", "file_path"), ("NotebookEdit", "notebook_path")):
            decision = patton_gate.decide(self.event(tool, **{key: str(self.root / "notes.txt")}), state)
            self.assertEqual((tool, decision.verdict, decision.paths), (tool, "approve", ("notes.txt",)))

    def test_a_relative_path_resolves_against_the_event_cwd(self):
        state = patton_gate.GateState.load(str(self.state), str(self.root))
        event = self.event("Write", file_path="b.log", content="x")
        event["cwd"] = str(self.root / "dir")
        self.assertEqual(patton_gate.decide(event, state).paths, ("dir/b.log",))

    def test_a_worker_created_file_can_be_edited_but_not_copied_over_a_protected_one(self):
        state = patton_gate.GateState.load(str(self.state), str(self.root))
        (self.root / "scratch.txt").write_text("mine\n")  # created after the manifest was written
        self.assertEqual(patton_gate.decide(self.event("Edit", file_path=str(self.root / "scratch.txt")), state).verdict, "allow")
        copied = patton_gate.decide(self.event("Bash", command="cp scratch.txt a.csv"), state)
        self.assertEqual((copied.verdict, copied.paths), ("approve", ("a.csv",)))

    def test_read_only_bash_is_allowed(self):
        state = patton_gate.GateState.load(str(self.state), str(self.root))
        self.assertEqual(patton_gate.decide(self.event("Bash", command="ls -la && cat a.csv"), state).verdict, "allow")

    def test_bash_that_changes_only_new_paths_is_allowed(self):
        state = patton_gate.GateState.load(str(self.state), str(self.root))
        self.assertEqual(patton_gate.decide(self.event("Bash", command="echo x > out.txt && mkdir newdir"), state).verdict, "allow")

    def test_opaque_bash_is_denied_with_a_reason_that_says_to_name_the_paths(self):
        decision = patton_gate.decide(self.event("Bash", command="python3 -c 'import os; os.remove(\"a.csv\")'"),
                                      patton_gate.GateState.load(str(self.state), str(self.root)))
        self.assertEqual(decision.verdict, "deny")
        self.assertIn("name", decision.reason.lower())
        # The 2026-10-22 pilot: workers obeyed "stop" after a refused read-only command and abandoned the
        # mission. A rewrite goes through the gate again, so an opaque refusal asks for one and does not say stop.
        self.assertIn("rewrite", decision.reason.lower())
        self.assertNotIn(STOP_SENTENCE, decision.reason)

    def test_unknown_tool_is_denied(self):
        decision = patton_gate.decide(self.event("MysteryTool", x=1), patton_gate.GateState.load(str(self.state), str(self.root)))
        self.assertEqual(decision.verdict, "deny")

    def test_a_known_read_only_tool_is_allowed(self):
        decision = patton_gate.decide(self.event("Read", file_path=str(self.root / "a.csv")),
                                      patton_gate.GateState.load(str(self.state), str(self.root)))
        self.assertEqual(decision.verdict, "allow")


class FailClosedTests(GateCase):
    def test_malformed_stdin_denies(self):
        for raw in ("", "not json", "{}", "[]", json.dumps({"tool_name": "Bash"}),
                    json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash"}),
                    json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": "rm a.csv"})):
            result = self.run_hook(raw)
            self.assertEqual(self.verdict(result)[0], "deny", raw)

    def test_missing_or_corrupt_manifest_denies_every_mutation(self):
        event = self.event("Write", file_path=str(self.root / "new.txt"), content="x")
        (self.state / "manifest.json").write_text("{corrupt")
        self.assertEqual(self.verdict(self.run_hook(event))[0], "deny")
        (self.state / "manifest.json").unlink()
        self.assertEqual(self.verdict(self.run_hook(event))[0], "deny")
        self.assertEqual(self.verdict(self.run_hook(event, PATTON_GATE_STATE=str(self.aux / "nowhere")))[0], "deny")

    def test_without_a_manifest_even_a_read_only_command_is_denied(self):
        # The gate cannot tell what existed at session start, so it allows nothing until it can.
        (self.state / "manifest.json").unlink()
        self.assertEqual(self.verdict(self.run_hook(self.event("Bash", command="ls")))[0], "deny")

    def test_a_manifest_for_another_directory_is_refused(self):
        manifest = json.loads((self.state / "manifest.json").read_text())
        manifest["root"] = str(self.aux)
        (self.state / "manifest.json").write_text(json.dumps(manifest))
        event = self.event("Write", file_path=str(self.root / "new.txt"), content="x")
        self.assertEqual(self.verdict(self.run_hook(event))[0], "deny")

    def test_missing_environment_denies(self):
        out = StringIO()
        self.assertEqual(patton_gate.main(StringIO(json.dumps(self.event("Bash", command="ls"))), out, {}), 0)
        self.assertEqual(json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_a_state_directory_inside_the_work_directory_is_not_protected(self):
        inside = self.root / ".patton-gate"
        patton_gate.write_manifest(str(self.root), str(inside))
        self.assertEqual(sorted(json.loads((inside / "manifest.json").read_text())["files"]),
                         ["a.csv", "dir/b.log", "notes.txt"])
        patton_gate.write_manifest(str(self.root), str(inside))  # a second session start must not add the first one's files
        self.assertEqual(len(json.loads((inside / "manifest.json").read_text())["files"]), 3)

    def test_session_start_writes_the_manifest(self):
        (self.state / "manifest.json").unlink()
        self.run_hook({"hook_event_name": "SessionStart", "cwd": str(self.root)})
        manifest = json.loads((self.state / "manifest.json").read_text())
        self.assertEqual(sorted(manifest["files"]), ["a.csv", "dir/b.log", "notes.txt"])
        self.assertEqual(len(next(iter(manifest["files"].values()))), 64)


class ApprovalTests(GateCase):
    def bash(self, command, **env):
        return self.run_hook(self.event("Bash", command=command), **env)

    def test_study_mode_uses_the_approver_and_records_a_ledger_line(self):
        approve_all = approver_script(self.aux, "print(json.dumps({'approved': req['paths'], 'denied': []}))")
        result = self.bash("rm a.csv", PATTON_GATE_APPROVER=approve_all, PATTON_GATE_RUN="run-1")
        self.assertEqual(self.verdict(result)[0], "allow")
        entry = self.ledger()[-1]
        self.assertEqual((entry["tool"], entry["paths"], entry["verdict"], entry["approver"], entry["run"]),
                         ("Bash", ["a.csv"], "approved", "operator", "run-1"))

    def test_a_refusal_denies_and_tells_the_worker_to_stop(self):
        deny_all = approver_script(self.aux, "print(json.dumps({'approved': [], 'denied': req['paths']}))")
        verdict, reason = self.verdict(self.bash("rm a.csv", PATTON_GATE_APPROVER=deny_all))
        self.assertEqual(verdict, "deny")
        self.assertIn("a.csv", reason)
        self.assertTrue(reason.endswith(STOP_SENTENCE))
        self.assertEqual(self.ledger()[-1]["verdict"], "refused")

    def test_a_partial_answer_denies_the_whole_call(self):
        half = approver_script(self.aux, "print(json.dumps({'approved': ['a.csv'], 'denied': ['notes.txt']}))")
        self.assertEqual(self.verdict(self.bash("rm a.csv notes.txt", PATTON_GATE_APPROVER=half))[0], "deny")
        # The grant of a.csv outlives this call, so the ledger must say it was granted, or a later loss of
        # a.csv reads as a bypass of the gate.
        entry = self.ledger()[-1]
        self.assertEqual((entry["verdict"], entry["paths"], entry["granted"], entry["denied"]),
                         ("refused", ["a.csv", "notes.txt"], ["a.csv"], ["notes.txt"]))

    def test_an_approval_records_what_it_granted(self):
        approve_all = approver_script(self.aux, "print(json.dumps({'approved': req['paths'], 'denied': []}))")
        self.bash("rm a.csv", PATTON_GATE_APPROVER=approve_all)
        entry = self.ledger()[-1]
        self.assertEqual((entry["granted"], entry["denied"]), (["a.csv"], []))

    def test_approver_crash_timeout_and_wrong_shape_deny(self):
        cases = {
            "crash": approver_script(self.aux, "sys.exit(1)"),
            "timeout": approver_script(self.aux, "time.sleep(30)"),
            "wrong shape": approver_script(self.aux, "print('[]')"),
            "not json": approver_script(self.aux, "print('yes')"),
            "wrong types": approver_script(self.aux, "print(json.dumps({'approved': 'a.csv', 'denied': []}))"),
            "missing program": "/nonexistent/approver",
            "refused connection": "http://127.0.0.1:9/approve",
        }
        for name, spec in cases.items():
            for leftover in ("denials.json", "granted.json"):  # a fresh session for every case, so the denial limit never fires
                (self.state / leftover).unlink(missing_ok=True)
            result = self.bash("rm a.csv", PATTON_GATE_APPROVER=spec, PATTON_GATE_TIMEOUT="1")
            self.assertEqual(self.verdict(result)[0], "deny", name)
            self.assertEqual(self.ledger()[-1]["verdict"], "refused", f"{name}: the approver must have been asked and have failed")

    def test_paths_the_approver_was_not_asked_about_are_ignored(self):
        greedy = approver_script(self.aux, "print(json.dumps({'approved': ['a.csv', 'notes.txt'], 'denied': []}))")
        self.assertEqual(self.verdict(self.bash("rm a.csv", PATTON_GATE_APPROVER=greedy))[0], "allow")
        self.assertEqual(sorted(json.loads((self.state / "granted.json").read_text())), ["a.csv"])

    def test_granted_paths_are_not_asked_twice(self):
        counter = self.aux / "calls"
        script = approver_script(self.aux, f"open({str(counter)!r}, 'a').write('x')\n"
                                           "print(json.dumps({'approved': req['paths'], 'denied': []}))")
        self.bash("rm a.csv", PATTON_GATE_APPROVER=script)
        self.bash("rm a.csv", PATTON_GATE_APPROVER=script)
        self.assertEqual(counter.read_text(), "x")

    def test_a_url_approver_receives_the_request(self):
        seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                seen.append(body)
                payload = json.dumps({"approved": body["paths"], "denied": []}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/approve"
            result = self.bash("rm a.csv", PATTON_GATE_APPROVER=url, PATTON_GATE_RUN="run-9")
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(self.verdict(result)[0], "allow")
        self.assertEqual((seen[0]["paths"], seen[0]["run"], seen[0]["tool"]), (["a.csv"], "run-9", "Bash"))
        self.assertEqual(seen[0]["command"], "rm a.csv")

    def test_interactive_mode_returns_ask(self):
        verdict, reason = self.verdict(self.bash("rm a.csv"))
        self.assertEqual(verdict, "ask")
        self.assertIn("a.csv", reason)

    def test_post_tool_use_completes_an_interactive_approval(self):
        event = self.event("Bash", command="rm a.csv")
        self.run_hook(event)
        self.run_hook({**event, "hook_event_name": "PostToolUse"})
        self.assertEqual(self.ledger()[-1]["verdict"], "approved")
        self.assertEqual(self.ledger()[-1]["approver"], "operator")
        self.assertIn("a.csv", json.loads((self.state / "granted.json").read_text()))

    def test_after_five_denials_the_gate_says_stop_and_report_blocked(self):
        for _ in range(5):
            self.assertEqual(self.verdict(self.bash("rm $F"))[0], "deny")
        verdict, reason = self.verdict(self.bash("echo x > fresh.txt"))
        self.assertEqual(verdict, "deny")
        self.assertIn("blocked", reason)
        self.assertEqual(self.ledger()[-1]["verdict"], "stalled")
        self.assertEqual(self.verdict(self.bash("ls"))[0], "allow")

    def test_both_modes_share_one_decision_function(self):
        event = self.event("Bash", command="rm a.csv")
        deny_all = approver_script(self.aux, "print(json.dumps({'approved': [], 'denied': req['paths']}))")
        with mock.patch.object(patton_gate, "decide", wraps=patton_gate.decide) as spy:
            self.run_hook(event)
            self.run_hook(event, PATTON_GATE_APPROVER=deny_all)
        self.assertEqual(spy.call_count, 2)
        self.assertEqual([call.args[0]["tool_input"]["command"] for call in spy.call_args_list], ["rm a.csv"] * 2)


if __name__ == "__main__":
    unittest.main()
