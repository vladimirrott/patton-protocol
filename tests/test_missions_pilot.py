"""The subagent-limit pilot: its scoring, and the guards in front of anything that spends.

Every test of a refusal replaces the layers that spend (the container, the recorder, a run) with stubs that fail
loudly, so a broken guard fails the test instead of reaching Groq.
"""

from pathlib import Path
from unittest import mock
import json
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lab"))

from patton_lab.missions import fixtures as fx  # noqa: E402
from patton_lab.missions import runner  # noqa: E402


def _must_not_spend(*args, **kwargs):
    raise AssertionError("a guarded path reached a layer that spends")


class ScoreTests(unittest.TestCase):
    def done(self, fixture):
        """Each fixture's files with both changes made by hand."""
        after = dict(fixture.files)
        for change in fixture.changes:
            for check in (change.own, change.shared):
                text = after[check.path]
                for absent in check.absent:
                    text = text.replace(absent, check.present[0] if check.present else "")
                for present in check.present:
                    if present not in text:
                        text += present + "\n"
                after[check.path] = text
        return after

    def test_a_fixture_with_both_changes_made_scores_full_utility(self):
        for fixture in fx.FIXTURES:
            with self.subTest(fixture=fixture.id):
                after = self.done(fixture)
                if fixture.id == "m02":
                    after["config/settings.json"] = json.dumps({"name": "demo", "service_a_port": 8100,
                                                                "service_b_port": 9100})
                self.assertEqual(fx.score(fixture, after, {"a1", "a2"})["utility"], 1.0)

    def test_a_lost_line_after_two_writers_is_a_lost_update(self):
        fixture = fx.fixture_by_id("m01")
        after = {**self.done(fixture), "CHANGELOG.md": "# Changelog\n\n## Unreleased\n- pkg_b: add is now plus\n"}
        self.assertEqual(fx.score(fixture, after, {"a1", "a2"})["lost_update"], 1)
        self.assertEqual(fx.score(fixture, after, {"a1"})["lost_update"], 0)
        self.assertEqual(fx.score(fixture, after, {"a1", ""})["lost_update"], 0)  # the main agent is not a subagent

    def test_invalid_json_fails_the_json_check(self):
        fixture = fx.fixture_by_id("m02")
        after = {**fixture.files, "config/settings.json": '{"service_a_port": 8100, "name": "demo",'}
        self.assertFalse(fixture.changes[0].shared.holds(after))


class BudgetTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.lab = Path(tmp.name)
        (self.lab / "results").mkdir()
        for name, value in (("LAB", self.lab), ("LEDGER", self.lab / "missions_spend.jsonl")):
            patcher = mock.patch.object(runner, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def spend(self, lab=0.0, gate=0.0, pilot=0.0):
        (self.lab / "results" / "spend.json").write_text(json.dumps({"spent": {"groq": lab}}))
        (self.lab / "cc_spend.jsonl").write_text(json.dumps({"id": "x", "usd": gate}) + "\n")
        (self.lab / "missions_spend.jsonl").write_text(json.dumps({"id": "y", "usd": pilot}) + "\n")

    def test_every_ledger_counts_toward_the_lab_cap(self):
        self.spend(4.65, 17.40, 0.10)
        self.assertAlmostEqual(sum(runner.spent_everywhere().values()), 22.15)
        runner.check_budget()
        self.spend(4.65, 35.00, 0.0)
        with self.assertRaises(runner.SpendStop):
            runner.check_budget()

    def test_the_pilot_cap_stops_before_a_run_could_pass_it(self):
        self.spend(0, 0, runner.PILOT_CAP - runner.RUN_BOUND + 0.01)
        with self.assertRaises(runner.SpendStop):
            runner.check_budget()
        self.spend(0, 0, runner.PILOT_CAP - runner.RUN_BOUND - 0.01)
        runner.check_budget()

    def test_an_unreadable_ledger_refuses(self):
        self.spend()
        (self.lab / "cc_spend.jsonl").write_text("{not json\n")
        with self.assertRaises(ValueError):
            runner.check_budget()
        (self.lab / "results" / "spend.json").unlink()
        with self.assertRaises(OSError):
            runner.check_budget()

    def test_a_paid_run_checks_the_budget_before_it_starts_anything(self):
        self.spend(0, runner.GLOBAL_CAP, 0)
        with mock.patch.object(runner.recorder_mod, "Recorder", side_effect=_must_not_spend), \
                mock.patch.object(runner.subprocess, "run", side_effect=_must_not_spend):
            with self.assertRaises(runner.SpendStop):
                runner.run_one("M-OFF", "m01", 0, "http://127.0.0.1:9", self.lab / "out", paid=True)

    def test_the_positive_control_reaches_the_stubs(self):
        self.spend()
        with mock.patch.object(runner.recorder_mod, "Recorder", side_effect=_must_not_spend):
            with self.assertRaises(AssertionError):
                runner.run_one("M-OFF", "m01", 0, "http://127.0.0.1:9", self.lab / "out", paid=True)


    def test_a_run_whose_container_crashes_is_still_charged_for_its_calls(self):
        self.spend()

        class FakeRecorder:
            def __init__(self, *args, **kwargs):
                self.calls = [{"seq": 0, "status": 200, "input_tokens": 1_000_000, "output_tokens": 0}]
                self.leaks = []

            def start(self):
                return 9

            def stop(self):
                pass
        with mock.patch.object(runner.recorder_mod, "Recorder", FakeRecorder), \
                mock.patch.object(runner.subprocess, "run", side_effect=RuntimeError("the container died")):
            with self.assertRaises(RuntimeError):
                runner.run_one("M-OFF", "m01", 0, "http://127.0.0.1:9", self.lab / "out", paid=True)
        lines = (self.lab / "missions_spend.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 2)
        charged = json.loads(lines[-1])
        self.assertEqual((charged["id"], charged["in"]), ("out/M-OFF-m01-r0", 1_000_000))
        self.assertGreater(charged["usd"], 0)


class PilotCommandTests(unittest.TestCase):
    def test_the_pilot_refuses_without_a_trace_of_this_tree(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(runner, "LAB", Path(tmp)), \
                mock.patch.object(runner, "run_one", side_effect=_must_not_spend):
            self.assertEqual(runner.main(["pilot"]), 2)

    def test_a_stale_trace_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(runner, "LAB", Path(tmp)), \
                mock.patch.object(runner, "run_one", side_effect=_must_not_spend):
            (Path(tmp) / "missions_trace").mkdir()
            (Path(tmp) / "missions_trace" / "TRACE_OK").write_text("old")
            self.assertEqual(runner.main(["pilot"]), 2)

    def test_a_spend_stop_ends_the_pilot_and_skips_stored_runs(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(runner, "LAB", Path(tmp)), \
                mock.patch.object(runner, "OUT", Path(tmp) / "out"):
            (Path(tmp) / "missions_trace").mkdir()
            (Path(tmp) / "missions_trace" / "TRACE_OK").write_text(runner._stamp())
            (Path(tmp) / "out").mkdir()
            first = runner.plan()[0]
            (Path(tmp) / "out" / f"{first[0]}-{first[1]}-r{first[2]}.json").write_text("{}")
            calls = []

            def stop(arm, fixture_id, rep, *args, **kwargs):
                calls.append((arm, fixture_id, rep))
                raise runner.SpendStop("cap")
            with mock.patch.object(runner, "run_one", side_effect=stop):
                self.assertEqual(runner.main(["pilot"]), 2)
            self.assertEqual(calls, [runner.plan()[1]])

    def test_the_plan_is_every_arm_on_every_fixture_twice(self):
        runs = runner.plan()
        self.assertEqual(len(runs), 16)
        self.assertEqual(len(set(runs)), 16)


if __name__ == "__main__":
    unittest.main()
