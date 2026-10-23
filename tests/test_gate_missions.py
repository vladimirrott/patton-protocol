"""The patton-gate plugin's mission limits: worker count, command and token budgets, timeout, retries, exclusive paths.

A mission is one subagent. The event shapes follow what Claude Code 2.1.307 sends (tests/tools/probe_subagent_hooks.py
recorded them): PreToolUse on `Agent` for each dispatch, then SubagentStart, then PostToolUse on `Agent`, the
subagent's own calls carrying its `agent_id`, and SubagentStop when it ends.
"""

from pathlib import Path
import json
import unittest
from unittest import mock

from test_gate_config import ConfigCase
from test_gate_plugin import gate


class MissionCase(ConfigCase):
    def setUp(self):
        super().setUp()
        self.clock = [1_000_000.0]
        patcher = mock.patch.object(gate, "_now", side_effect=lambda: self.clock[0])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.transcript = self.home / "projects" / f"{self.session}.jsonl"

    def limits(self, **missions):
        self.user_config({"missions": missions})
        self.start()

    def event(self, event, agent=None):
        payload = {**event, "transcript_path": str(self.transcript)}
        if agent:
            payload.update(agent_id=agent, agent_type="general-purpose")
        return self.hook(payload)

    def dispatch(self, tool_use_id, description="mission", subagent_type="general-purpose"):
        return self.event({"hook_event_name": "PreToolUse", "tool_name": "Agent", "tool_use_id": tool_use_id,
                           "tool_input": {"description": description, "prompt": "do it",
                                          "subagent_type": subagent_type}})

    def launched(self, tool_use_id, agent):
        self.event({"hook_event_name": "SubagentStart"}, agent)
        self.event({"hook_event_name": "PostToolUse", "tool_name": "Agent", "tool_use_id": tool_use_id,
                    "tool_input": {"description": "mission"}, "tool_response": {}})

    def stopped(self, agent):
        self.event({"hook_event_name": "SubagentStop", "stop_hook_active": False}, agent)

    def run_bash(self, command, agent=None, tool_use_id=None):
        return self.event({"hook_event_name": "PreToolUse", "tool_name": "Bash", "permission_mode": "default",
                           "tool_input": {"command": command}, "tool_use_id": tool_use_id or command}, agent)

    def run_write(self, rel, agent=None, tool_use_id=None):
        return self.event({"hook_event_name": "PreToolUse", "tool_name": "Write", "permission_mode": "default",
                           "tool_input": {"file_path": str(self.root / rel), "content": "x"},
                           "tool_use_id": tool_use_id or rel}, agent)

    def tokens(self, agent, *messages):
        path = self.transcript.with_suffix("") / "subagents" / f"agent-{agent}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as handle:
            for message_id, output in messages:
                handle.write(json.dumps({"type": "assistant", "message": {"id": message_id,
                                                                          "usage": {"output_tokens": output}}}) + "\n")


class OffTests(MissionCase):
    def test_with_the_limits_off_a_dispatch_gets_no_decision_and_no_state(self):
        self.start()
        self.assertIsNone(self.decision(self.dispatch("d1")))
        self.assertFalse((self.state_dir() / "missions.json").exists())

    def test_with_the_limits_off_a_subagents_command_is_judged_on_files_alone(self):
        self.start()
        for i in range(30):
            self.assertIsNone(self.decision(self.run_bash("ls", agent="a1", tool_use_id=f"c{i}")))
        self.assertEqual(self.decision(self.run_bash("rm notes.txt", agent="a1")), "ask")


class WorkerTests(MissionCase):
    def test_a_dispatch_past_the_worker_limit_is_refused_in_the_same_message(self):
        self.limits(max_workers=1)
        self.assertIsNone(self.decision(self.dispatch("d1")))
        result = self.dispatch("d2")
        self.assertEqual(self.decision(result), "deny")
        self.assertIn("missions.max_workers", self.reason(result))

    def test_a_running_subagent_counts_and_a_stopped_one_frees_its_place(self):
        self.limits(max_workers=1)
        self.dispatch("d1")
        self.launched("d1", "a1")
        self.assertEqual(self.decision(self.dispatch("d2")), "deny")
        self.stopped("a1")
        self.assertIsNone(self.decision(self.dispatch("d3")))

    def test_a_started_subagent_takes_its_dispatchs_place_and_not_a_second_one(self):
        # SubagentStart can arrive before the dispatch's PostToolUse; the subagent must not count twice.
        self.limits(max_workers=2)
        self.dispatch("d1")
        self.event({"hook_event_name": "SubagentStart"}, "a1")
        self.assertIsNone(self.decision(self.dispatch("d2")))

    def test_two_places_allow_two_parallel_dispatches(self):
        self.limits(max_workers=2)
        self.assertIsNone(self.decision(self.dispatch("d1")))
        self.assertIsNone(self.decision(self.dispatch("d2")))
        self.assertEqual(self.decision(self.dispatch("d3")), "deny")

    def test_a_dispatch_that_never_started_frees_its_place_after_a_minute(self):
        self.limits(max_workers=1)
        self.dispatch("d1")
        self.clock[0] += 61
        self.assertIsNone(self.decision(self.dispatch("d2")))

    def test_a_failed_dispatch_frees_its_place(self):
        self.limits(max_workers=1)
        self.dispatch("d1")
        self.event({"hook_event_name": "PostToolUseFailure", "tool_name": "Agent", "tool_use_id": "d1",
                    "tool_input": {}, "error": "boom"})
        self.assertIsNone(self.decision(self.dispatch("d2")))

    def test_the_old_tool_name_task_counts_too(self):
        self.limits(max_workers=1)
        self.event({"hook_event_name": "PreToolUse", "tool_name": "Task", "tool_use_id": "d1",
                    "tool_input": {"description": "m", "prompt": "p"}})
        self.assertEqual(self.decision(self.dispatch("d2")), "deny")


class BudgetTests(MissionCase):
    def test_a_subagent_past_its_command_budget_is_told_to_report(self):
        self.limits(max_commands=2)
        self.event({"hook_event_name": "SubagentStart"}, "a1")
        self.assertIsNone(self.decision(self.run_bash("ls", "a1", "c1")))
        self.assertIsNone(self.decision(self.run_write("new.txt", "a1", "c2")))
        result = self.run_bash("ls", "a1", "c3")
        self.assertEqual(self.decision(result), "deny")
        self.assertIn("missions.max_commands", self.reason(result))
        self.assertIn("report", self.reason(result))

    def test_the_main_agent_has_no_command_budget(self):
        self.limits(max_commands=1)
        for i in range(5):
            self.assertIsNone(self.decision(self.run_bash("ls", None, f"m{i}")))

    def test_each_subagent_has_its_own_budget(self):
        self.limits(max_commands=1)
        self.assertIsNone(self.decision(self.run_bash("ls", "a1", "c1")))
        self.assertIsNone(self.decision(self.run_bash("ls", "a2", "c2")))
        self.assertEqual(self.decision(self.run_bash("ls", "a1", "c3")), "deny")

    def test_a_subagent_past_its_token_budget_is_told_to_report(self):
        self.limits(max_output_tokens=100)
        self.tokens("a1", ("m1", 30), ("m1", 60), ("m2", 30))  # m1 is written twice while it streams: 60 + 30
        self.assertIsNone(self.decision(self.run_bash("ls", "a1", "c1")))
        self.tokens("a1", ("m3", 10))
        result = self.run_bash("ls", "a1", "c2")
        self.assertEqual(self.decision(result), "deny")
        self.assertIn("missions.max_output_tokens", self.reason(result))

    def test_a_missing_transcript_counts_as_no_tokens_yet(self):
        self.limits(max_output_tokens=10)
        self.assertIsNone(self.decision(self.run_bash("ls", "a1", "c1")))


class TimeoutTests(MissionCase):
    def test_a_subagent_past_its_time_is_told_to_report(self):
        self.limits(timeout_minutes=15)
        self.event({"hook_event_name": "SubagentStart"}, "a1")
        self.clock[0] += 14 * 60
        self.assertIsNone(self.decision(self.run_bash("ls", "a1", "c1")))
        self.clock[0] += 2 * 60
        result = self.run_bash("ls", "a1", "c2")
        self.assertEqual(self.decision(result), "deny")
        self.assertIn("missions.timeout_minutes", self.reason(result))

    def test_a_subagent_whose_start_was_not_seen_is_timed_from_its_first_command(self):
        self.limits(timeout_minutes=1)
        self.assertIsNone(self.decision(self.run_bash("ls", "a1", "c1")))
        self.clock[0] += 30
        self.assertIsNone(self.decision(self.run_bash("ls", "a1", "c2")))
        self.clock[0] += 31
        self.assertEqual(self.decision(self.run_bash("ls", "a1", "c3")), "deny")


class RetryTests(MissionCase):
    def test_the_same_mission_is_dispatched_at_most_one_plus_retries_times(self):
        self.limits(max_retries=1)
        self.assertIsNone(self.decision(self.dispatch("d1", "fix the parser")))
        self.assertIsNone(self.decision(self.dispatch("d2", "fix the parser")))
        result = self.dispatch("d3", "fix the parser")
        self.assertEqual(self.decision(result), "deny")
        self.assertIn("missions.max_retries", self.reason(result))
        self.assertIsNone(self.decision(self.dispatch("d4", "update the docs")))
        self.assertIsNone(self.decision(self.dispatch("d5", "fix the parser", "Explore")))

    def test_a_refused_dispatch_does_not_use_up_a_retry(self):
        self.limits(max_retries=0, max_workers=1)
        self.dispatch("d1", "a")
        self.assertEqual(self.decision(self.dispatch("d2", "b")), "deny")  # refused for the worker limit
        self.event({"hook_event_name": "PostToolUseFailure", "tool_name": "Agent", "tool_use_id": "d1",
                    "tool_input": {}, "error": "x"})
        self.assertIsNone(self.decision(self.dispatch("d3", "b")))


class ExclusivePathTests(MissionCase):
    def test_a_path_one_running_subagent_changed_is_closed_to_the_others(self):
        self.limits(exclusive_paths=True)
        self.event({"hook_event_name": "SubagentStart"}, "a1")
        self.event({"hook_event_name": "SubagentStart"}, "a2")
        self.assertIsNone(self.decision(self.run_write("shared.txt", "a1", "w1")))
        result = self.run_write("shared.txt", "a2", "w2")
        self.assertEqual(self.decision(result), "deny")
        self.assertIn("shared.txt", self.reason(result))
        self.assertIn("missions.exclusive_paths", self.reason(result))
        self.assertEqual(self.decision(self.run_bash("rm shared.txt", None, "m1")), "deny")  # the main agent too
        self.assertIsNone(self.decision(self.run_write("shared.txt", "a1", "w3")))  # the owner may go on

    def test_the_path_opens_when_its_owner_stops(self):
        self.limits(exclusive_paths=True)
        self.run_write("shared.txt", "a1", "w1")
        self.stopped("a1")
        self.assertIsNone(self.decision(self.run_write("shared.txt", "a2", "w2")))
        self.assertEqual(json.loads((self.state_dir() / "missions.json").read_text())["owners"], {"shared.txt": "a2"})

    def test_the_main_agent_never_claims_a_path(self):
        self.limits(exclusive_paths=True)
        self.assertIsNone(self.decision(self.run_write("mine.txt", None, "m1")))
        self.assertIsNone(self.decision(self.run_write("mine.txt", "a1", "w1")))

    def test_a_call_that_lost_the_race_for_a_file_keeps_no_copy(self):
        # Another subagent took the file between the check and the claim: the call is refused and keeps nothing.
        self.limits(exclusive_paths=True)
        real = gate.exclusive_conflict
        lost = gate._refuse("exclusive_paths", "another running subagent is changing notes.txt", "Leave it.")
        with mock.patch.object(gate, "exclusive_conflict",
                               side_effect=lambda e, s, c, claim: lost if claim else real(e, s, c, claim)):
            result = self.run_write("notes.txt", "a1", "w1")
        self.assertEqual(self.decision(result), "deny")
        self.assertFalse((self.state_dir() / "originals" / "notes.txt").exists())

    def test_a_change_the_gate_refused_claims_nothing(self):
        self.limits(exclusive_paths=True)
        refused = self.event({"hook_event_name": "PreToolUse", "tool_name": "Write", "tool_use_id": "w1",
                              "permission_mode": "bypassPermissions",
                              "tool_input": {"file_path": str(self.root / "notes.txt"), "content": "x"}}, "a1")
        self.assertEqual(self.decision(refused), "deny")
        self.assertEqual(self.decision(self.run_write("notes.txt", "a2", "w2")), "ask")  # asked, not refused as taken

    def test_a_shell_command_claims_the_files_it_names(self):
        self.limits(exclusive_paths=True)
        self.assertIsNone(self.decision(self.run_bash("echo x > out.txt", "a1", "b1")))
        self.assertEqual(self.decision(self.run_bash("cat out.txt > copy.txt; rm out.txt", "a2", "b2")), "deny")

    def test_a_refused_change_claims_nothing(self):
        self.limits(exclusive_paths=True, max_commands=1)
        self.run_bash("ls", "a1", "b0")
        self.assertEqual(self.decision(self.run_write("late.txt", "a1", "b1")), "deny")  # over budget
        self.assertIsNone(self.decision(self.run_write("late.txt", "a2", "b2")))


class PresetTests(MissionCase):
    def test_the_patton_preset_enforces_the_protocols_numbers(self):
        self.limits(preset="patton")
        for i in range(4):
            self.assertIsNone(self.decision(self.dispatch(f"d{i}", f"m{i}")))
        self.assertEqual(self.decision(self.dispatch("d4", "m4")), "deny")
        for i in range(20):
            self.assertIsNone(self.decision(self.run_bash("ls", "a1", f"c{i}")))
        self.assertEqual(self.decision(self.run_bash("ls", "a1", "c20")), "deny")

    def test_every_mission_decision_is_in_the_ledger(self):
        self.limits(max_workers=1)
        self.dispatch("d1")
        self.dispatch("d2")
        verdicts = [(e.get("tool"), e.get("verdict")) for e in self.ledger()]
        self.assertIn(("Agent", "allow"), verdicts)
        self.assertIn(("Agent", "deny"), verdicts)


if __name__ == "__main__":
    unittest.main()
