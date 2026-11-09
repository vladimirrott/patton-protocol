"""P1X's report on made-up runs."""

from pathlib import Path
from unittest import mock
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lab"))

from patton_lab.fleet import runner  # noqa: E402
from patton_lab.p1x import report, verdicts  # noqa: E402

HARNESSES = runner.SETS["p1x"]["harnesses"]
FIXTURES = runner.SETS["p1x"]["fixtures"]
ARMS = ("none", "stop", "detect")


def row(harness, arm, fixture, rep, **metrics):
    base = {"goal_met": False, "gamed": False, "overclaim": False, "honest_end": False, "out_of_scope": [], "over_budget": False,
            "refused_stops": 0, "verifier_blocks": 0, "verifier_capped": 0, "claimed_done": False}
    return {"harness": harness, "arm": arm, "fixture": fixture, "rep": rep, "rc": 0, "timed_out": False, "calls": 10, "usd": 0.01,
            "metrics": {**base, **metrics}}


def full(rule=lambda h, a, f, r: {}):
    return [row(h, a, f, r, **rule(h, a, f, r)) for h in HARNESSES for a in ARMS for f in FIXTURES for r in range(6)]


class TextTests(unittest.TestCase):
    def text(self, rows):
        with mock.patch.object(runner, "load", return_value=rows):
            return report.text()

    def test_a_partial_study_says_how_many_runs_are_missing(self):
        self.assertIn("Not final: 100 of 504 registered runs are stored.", self.text(full()[:100]))

    def test_a_complete_study_says_so_and_gives_the_cost(self):
        self.assertIn("Final: all 504 registered runs are stored, $5.0400.", self.text(full()))

    def test_every_verdict_line_is_the_registered_rules_output(self):
        rows = full(lambda h, a, f, r: {"gamed": a == "none" and f in ("p01", "p04"), "claimed_done": True})
        text = self.text(rows)
        for key, (verdict, detail) in verdicts.p1x(rows).items():
            self.assertIn(f"- {key}: **{verdict}**. {detail}", text)

    def test_the_by_fixture_table_counts_undisclosed_fits_per_arm(self):
        rule = lambda h, a, f, r: {"gamed": f == "p01", "claimed_done": a == "none" or r < 2, "honest_end": a == "detect"}  # noqa: E731
        text = self.text(full(rule))
        self.assertIn("| p01 | none | 42 | 42 | 42 | 0 | 0 | 0 |", text)
        self.assertIn("| p01 | stop | 42 | 42 | 14 | 0 | 0 | 0 |", text)
        self.assertIn("| p01 | detect | 42 | 42 | 14 | 0 | 42 | 0 |", text)

    def test_the_refusal_lines_separate_refused_runs_from_the_rest_for_each_arm(self):
        def rule(h, a, f, r):
            if a == "none" or f not in ("p01", "p04"):
                return {}
            if r == 0:
                return {"verifier_blocks": 1, "honest_end": True}
            if r == 1:
                return {"verifier_blocks": 2, "gamed": True, "claimed_done": True, "verifier_capped": 1}
            return {"gamed": r == 2}
        text = self.text(full(rule))
        for arm in ("stop", "detect"):
            self.assertIn(f"- {arm} runs on p01 and p04: 84. Refused at least once: 28; of those, ended gamed 14, ended with an honest "
                          f"end 14, ended with an undisclosed fit 14; never refused: 56; of those, ended gamed 14. "
                          f"Flags the round cap let through: 14.", text)

    def test_detect_is_paired_against_stop_on_the_same_cells(self):
        rule = lambda h, a, f, r: {"gamed": a == "detect" and r < 2, "overclaim": a == "stop" and r < 3, "claimed_done": True}  # noqa: E731
        text = self.text(full(rule))
        self.assertIn("Detect against stop, paired on 168 cells, gamed runs: only detect 56, only stop 0.", text)
        self.assertIn("overclaims: only detect 0, only stop 84.", text)
        self.assertIn("undisclosed fits: only detect 56, only stop 0.", text)

    def test_an_unpaired_arm_is_left_out_of_the_pairing(self):
        rows = [r for r in full() if not (r["arm"] == "stop" and r["rep"] == 0)]
        self.assertIn("paired on 140 cells", self.text(rows))


class CheckTests(unittest.TestCase):
    def doc(self, body):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "findings.md"
        path.write_text(body)
        return path

    def test_findings_that_quote_the_report_pass_and_others_fail(self):
        with mock.patch.object(runner, "load", return_value=full()):
            begin, end = report.markers()
            good = self.doc(f"intro\n{begin}\n{report.text()}\n{end}\noutro\n")
            bad = self.doc(f"intro\n{begin}\nsomething else\n{end}\n")
            self.assertEqual(report.check(good), [])
            self.assertEqual(len(report.check(bad)), 1)

    def test_a_document_without_the_markers_is_not_checked(self):
        self.assertEqual(report.check(self.doc("no report here")), [])


if __name__ == "__main__":
    unittest.main()
