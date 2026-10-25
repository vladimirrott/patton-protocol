"""Process discipline in the patton-gate plugin: reminders at the call, scope and budget, and the stop rules.

No model is called: every rule here is mechanical, read from the hook events and the gate's own record of the turn.
"""

from pathlib import Path
import io
import json
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))
from test_gate_plugin import gate  # noqa: E402

GOOD_REPORT = """Done.

status: completed
files_changed:
  - src/app.py
commands_run:
  - command: "python3 -m pytest -q"
    outcome: "3 passed"
evidence:
  - "pytest printed 3 passed"
risks: []
next_action: "merge"
"""


class Project:
    def __init__(self, discipline, files=None):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root, self.state = base / "work", base / "state"
        (self.root / "src").mkdir(parents=True)
        (self.root / "tests").mkdir()
        (self.root / "src" / "app.py").write_text("def add(a, b):\n    return a - b\n")
        (self.root / "tests" / "test_app.py").write_text("from src.app import add\n\ndef test_add():\n"
                                                         "    assert add(1, 2) == 3\n")
        (self.root / "docs.txt").write_text("docs\n")
        path = base / "config" / "patton-gate" / "config.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"discipline": discipline, "files": {"protect": False, **(files or {})}}))
        self.env = {"HOME": str(base), "XDG_CONFIG_HOME": str(base / "config"),
                    "PATTON_GATE_ROOT": str(self.root), "PATTON_GATE_STATE": str(self.state)}
        self.hook({"hook_event_name": "SessionStart"})
        self.hook({"hook_event_name": "UserPromptSubmit", "prompt": "Fix add() so the test passes."})
        self.ids = 0

    def hook(self, event, agent=None):
        event = {"session_id": "s1", "cwd": str(self.root), "transcript_path": "/dev/null", **event}
        if agent:
            event["agent_id"] = agent
        out = io.StringIO()
        gate.main(io.StringIO(json.dumps(event)), out, self.env)
        text = out.getvalue().strip()
        return json.loads(text) if text else None

    def call(self, tool, agent=None, **tool_input):
        self.ids += 1
        out = self.hook({"hook_event_name": "PreToolUse", "tool_name": tool, "permission_mode": "default",
                         "tool_input": tool_input, "tool_use_id": f"t{self.ids}"}, agent)
        return (out or {}).get("hookSpecificOutput") or {}

    def write(self, rel, agent=None):
        return self.call("Write", agent, file_path=str(self.root / rel), content="x\n")

    def bash(self, command, agent=None, outcome="ok"):
        out = self.call("Bash", agent, command=command)
        if outcome != "none":
            event = "PostToolUse" if outcome == "ok" else "PostToolUseFailure"
            self.hook({"hook_event_name": event, "tool_name": "Bash", "tool_use_id": f"t{self.ids}",
                       "tool_input": {"command": command}, "tool_response": {"stdout": "", "stderr": ""}}, agent)
        return out

    def stop(self, message="All done.", agent=None):
        name = "SubagentStop" if agent else "Stop"
        return self.hook({"hook_event_name": name, "stop_hook_active": False, "last_assistant_message": message},
                         agent)

    def close(self):
        self.tmp.cleanup()


def project(test, discipline, files=None):
    p = Project(discipline, files)
    test.addCleanup(p.close)
    return p


class OffTests(unittest.TestCase):
    def test_the_defaults_add_nothing(self):
        p = project(self, {})
        self.assertEqual(p.write("tests/test_app.py"), {})
        self.assertIsNone(p.stop())
        self.assertFalse((p.state / "discipline.json").exists())


class ReminderTests(unittest.TestCase):
    def test_changing_a_test_brings_a_reminder_next_to_the_result(self):
        p = project(self, {"reminders": True})
        out = p.write("tests/test_app.py")
        self.assertIn("tests/test_app.py is a test", out["additionalContext"])
        self.assertNotIn("permissionDecision", out)
        self.assertEqual(p.write("src/app.py"), {})

    def test_a_shell_command_that_changes_a_test_is_caught_too(self):
        p = project(self, {"reminders": True})
        self.assertIn("is a test", p.bash("rm tests/test_app.py", outcome="none")["additionalContext"])

    def test_the_reminder_rides_along_with_the_gates_own_answer(self):
        p = project(self, {"reminders": True}, files={"protect": True})
        out = p.write("tests/test_app.py")
        self.assertEqual(out["permissionDecision"], "ask")
        self.assertIn("is a test", out["additionalContext"])

    def test_the_test_patterns_come_from_the_settings(self):
        p = project(self, {"reminders": True, "tests": ["checks/**"]})
        self.assertEqual(p.write("tests/test_app.py"), {})
        self.assertIn("is a test", p.write("checks/run.sh")["additionalContext"])


class ScopeTests(unittest.TestCase):
    def test_a_file_outside_the_scope_is_refused_when_set_to_deny(self):
        p = project(self, {"scope": ["src/**"], "out_of_scope": "deny"})
        self.assertEqual(p.write("src/app.py"), {})
        out = p.write("docs.txt")
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn("outside the files this task may change (src/**)", out["permissionDecisionReason"])
        self.assertEqual(p.bash("rm docs.txt", outcome="none")["permissionDecision"], "deny")
        self.assertEqual(p.write("/tmp/elsewhere.txt")["permissionDecision"], "deny")

    def test_a_reminder_only_when_set_to_remind(self):
        p = project(self, {"scope": ["src/**"], "out_of_scope": "remind"})
        out = p.write("docs.txt")
        self.assertNotIn("permissionDecision", out)
        self.assertIn("outside the files this task may change", out["additionalContext"])

    def test_reading_outside_the_scope_is_fine(self):
        p = project(self, {"scope": ["src/**"], "out_of_scope": "deny"})
        self.assertEqual(p.bash("cat docs.txt", outcome="none"), {})


class BudgetTests(unittest.TestCase):
    def test_a_warning_at_four_fifths_then_refusals_past_the_budget(self):
        p = project(self, {"max_calls": 5})
        outs = [p.bash("cat docs.txt", outcome="none") for _ in range(6)]
        self.assertEqual([o.get("permissionDecision") for o in outs], [None] * 5 + ["deny"])
        self.assertEqual([bool(o.get("additionalContext")) for o in outs], [False, False, False, True, False, False])
        self.assertIn("4 of the 5 commands", outs[3]["additionalContext"])
        self.assertIn("budget of 5 commands is used up", outs[5]["permissionDecisionReason"])

    def test_a_new_prompt_starts_a_new_budget(self):
        p = project(self, {"max_calls": 2})
        p.bash("cat docs.txt", outcome="none")
        p.bash("cat docs.txt", outcome="none")
        self.assertEqual(p.bash("cat docs.txt", outcome="none")["permissionDecision"], "deny")
        p.hook({"hook_event_name": "UserPromptSubmit", "prompt": "Next."})
        self.assertEqual(p.bash("cat docs.txt", outcome="none"), {})


class CheckBeforeStopTests(unittest.TestCase):
    def test_a_change_with_no_check_after_it_refuses_the_stop_once(self):
        p = project(self, {"check_before_stop": True})
        p.write("src/app.py")
        out = p.stop()
        self.assertEqual(out["decision"], "block")
        self.assertIn("ran none of the project's checks", out["reason"])
        capped = p.stop()
        self.assertNotIn("decision", capped)
        self.assertIn("left open", capped["systemMessage"])

    def test_a_check_after_the_last_change_lets_the_agent_stop(self):
        p = project(self, {"check_before_stop": True})
        p.write("src/app.py")
        p.bash("cd src && python3 -m pytest -q")
        self.assertIsNone(p.stop())

    def test_a_change_after_the_check_needs_another_check(self):
        p = project(self, {"check_before_stop": True})
        p.bash("python3 -m pytest -q")
        p.write("src/app.py")
        self.assertIn("after the last check you ran", p.stop()["reason"])

    def test_no_change_needs_no_check(self):
        p = project(self, {"check_before_stop": True})
        p.bash("cat docs.txt", outcome="none")
        self.assertIsNone(p.stop())

    def test_a_refused_change_is_not_a_change(self):
        p = project(self, {"check_before_stop": True, "scope": ["src/**"], "out_of_scope": "deny"})
        p.write("docs.txt")
        self.assertIsNone(p.stop())


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.p = project(self, {"report": "all"})

    def test_a_complete_honest_report_passes(self):
        self.p.write("src/app.py")
        self.p.bash("python3 -m pytest -q")
        self.assertIsNone(self.p.stop(GOOD_REPORT))

    def test_missing_fields_are_named_with_the_template(self):
        out = self.p.stop("All done, everything works.")
        self.assertEqual(out["decision"], "block")
        self.assertIn("status, files_changed, commands_run, evidence, risks, next_action", out["reason"])
        self.assertIn("status: completed | partial | blocked | failed", out["reason"])

    def test_a_changed_file_left_out_of_the_report(self):
        self.p.write("src/app.py")
        self.p.write("tests/test_app.py")
        self.p.bash("python3 -m pytest -q")
        out = self.p.stop(GOOD_REPORT)
        self.assertIn("leaves out tests/test_app.py", out["reason"])
        self.assertNotIn("src/app.py,", out["reason"])

    def test_a_check_listed_but_never_run(self):
        self.p.write("src/app.py")
        self.assertIn("`pytest`", self.p.stop(GOOD_REPORT)["reason"])

    def test_completed_after_a_failing_check(self):
        self.p.write("src/app.py")
        self.p.bash("python3 -m pytest -q", outcome="failed")
        self.assertIn("the last check you ran (`python3 -m pytest -q`) failed", self.p.stop(GOOD_REPORT)["reason"])

    def test_an_honest_partial_report_after_a_failing_check_passes(self):
        self.p.write("src/app.py")
        self.p.bash("python3 -m pytest -q", outcome="failed")
        self.assertIsNone(self.p.stop(GOOD_REPORT.replace("status: completed", "status: partial")))

    def test_an_unknown_status(self):
        self.p.write("src/app.py")
        self.p.bash("python3 -m pytest -q")
        self.assertIn("status must be one of", self.p.stop(GOOD_REPORT.replace("completed", "done"))["reason"])

    def test_a_json_report_is_read_too(self):
        self.p.write("src/app.py")
        self.p.bash("python3 -m pytest -q")
        report = json.dumps({"status": "completed", "files_changed": ["src/app.py"],
                             "commands_run": [{"command": "python3 -m pytest -q", "outcome": "pass"}],
                             "evidence": ["3 passed"], "risks": [], "next_action": "merge"})
        self.assertIsNone(self.p.stop(report))


class ScopeOfReportTests(unittest.TestCase):
    def test_subagents_only_leaves_the_main_agent_alone(self):
        p = project(self, {"report": "subagents"})
        p.hook({"hook_event_name": "SubagentStart", "agent_type": "general-purpose"}, agent="a1")
        self.assertIsNone(p.stop("Done."))
        self.assertEqual(p.stop("Done.", agent="a1")["decision"], "block")

    def test_each_subagent_is_judged_on_its_own_changes(self):
        p = project(self, {"report": "subagents"})
        for agent in ("a1", "a2"):
            p.hook({"hook_event_name": "SubagentStart", "agent_type": "general-purpose"}, agent=agent)
        p.write("tests/test_app.py", agent="a2")
        p.write("src/app.py", agent="a1")
        p.bash("python3 -m pytest -q", agent="a1")
        self.assertIsNone(p.stop(GOOD_REPORT, agent="a1"))
        self.assertIn("leaves out tests/test_app.py", p.stop(GOOD_REPORT, agent="a2")["reason"])


class OrderTests(unittest.TestCase):
    def test_settings_that_cannot_be_read_still_refuse_through_the_gate(self):
        p = project(self, {"reminders": True})
        (p.state / "config.json").write_text("{not json")
        self.assertEqual(p.write("tests/test_app.py")["permissionDecision"], "deny")

    def test_a_project_file_can_tighten_but_not_loosen(self):
        p = project(self, {"scope": ["src/**"], "out_of_scope": "remind", "max_calls": 10})
        project_file = p.root / ".claude" / "patton-gate.json"
        project_file.parent.mkdir()
        project_file.write_text(json.dumps({"discipline": {"out_of_scope": "allow", "max_calls": 50,
                                                           "scope": ["**"], "check_commands": ["true"],
                                                           "tests": ["only/**"]}}))
        config, _, ignored = gate.load_config(str(p.root), p.env)
        d = config["discipline"]
        self.assertEqual((d["out_of_scope"], d["max_calls"], d["scope"]), ("remind", 10, ["src/**"]))
        self.assertEqual(d["check_commands"], gate.DEFAULTS["discipline"]["check_commands"])
        self.assertEqual(d["tests"], gate.DEFAULTS["discipline"]["tests"])
        self.assertEqual({i["field"] for i in ignored}, {"discipline.out_of_scope", "discipline.max_calls",
                                                         "discipline.scope", "discipline.check_commands",
                                                         "discipline.tests"})
        project_file.write_text(json.dumps({"discipline": {"out_of_scope": "deny", "max_calls": 3,
                                                           "check_before_stop": True, "report": "all"}}))
        config, _, ignored = gate.load_config(str(p.root), p.env)
        d = config["discipline"]
        self.assertEqual((d["out_of_scope"], d["max_calls"], d["check_before_stop"], d["report"]),
                         ("deny", 3, True, "all"))
        self.assertEqual(ignored, [])


if __name__ == "__main__":
    unittest.main()
