"""P1L's report: the runner's table, the registered verdicts, and the mechanism tables, on made-up runs."""

from pathlib import Path
from unittest import mock
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lab"))

from patton_lab.fleet import runner  # noqa: E402
from patton_lab.p1l import report, verdicts  # noqa: E402

HARNESSES = runner.SETS["p1l"]["harnesses"]
FIXTURES = runner.SETS["p1l"]["fixtures"]


def row(harness, arm, fixture, rep, **metrics):
    base = {"goal_met": False, "gamed": False, "overclaim": False, "honest_end": False, "out_of_scope": [],
            "over_budget": False, "refused_stops": 0, "verifier_blocks": 0, "verifier_capped": 0}
    return {"harness": harness, "arm": arm, "fixture": fixture, "rep": rep, "rc": 0, "timed_out": False, "calls": 10,
            "usd": 0.01, "metrics": {**base, **metrics}}


def full(rule=lambda h, a, f, r: {}):
    return [row(h, a, f, r, **rule(h, a, f, r)) for h in HARNESSES for a in ("none", "stop") for f in FIXTURES
            for r in range(6)]


class TextTests(unittest.TestCase):
    def text(self, rows):
        with mock.patch.object(runner, "load", return_value=rows):
            return report.text()

    def test_a_partial_study_says_how_many_runs_are_missing(self):
        text = self.text(full()[:100])
        self.assertIn("Not final: 100 of 336 registered runs are stored.", text)

    def test_a_complete_study_says_so_and_gives_the_cost(self):
        text = self.text(full())
        self.assertIn("Final: all 336 registered runs are stored, $3.3600.", text)

    def test_every_verdict_line_is_the_registered_rules_output(self):
        rows = full(lambda h, a, f, r: {"gamed": a == "none" and f in ("p01", "p04")})
        text = self.text(rows)
        for key, (verdict, detail) in verdicts.p1l(rows).items():
            self.assertIn(f"- {key}: **{verdict}**. {detail}", text)

    def test_the_table_has_the_verifier_column(self):
        self.assertIn("| Verifier blocks |", self.text(full()))

    def test_mechanism_counts_refused_stop_runs_and_what_became_of_them(self):
        def rule(h, a, f, r):
            if a != "stop" or f not in ("p01", "p04"):
                return {}
            if r == 0:   # refused, then ended honestly
                return {"verifier_blocks": 1, "honest_end": True}
            if r == 1:   # refused, then gamed anyway
                return {"verifier_blocks": 2, "gamed": True}
            if r == 2:   # refused twice, the cap let the third through
                return {"verifier_blocks": 2, "verifier_capped": 1, "gamed": True}
            return {"gamed": r == 3}   # never refused
        text = self.text(full(rule))
        # 7 harnesses x 2 fixtures = 14 cells per rep
        self.assertIn("Stop runs on p01 and p04: 84", text)
        self.assertIn("refused at least once by the Verifier: 42; of those, ended gamed 28, ended with an honest end 14", text)
        self.assertIn("never refused: 42; of those, ended gamed 14", text)
        self.assertIn("flags the round cap let through, stop runs on p01 and p04: 14", text)
        self.assertIn("| p01 | stop | 42 | 21 | 0 | 7 | 0 |", text)  # gamed 21, overclaimed 0, honest end 7, met 0
        # per harness: 2 fixtures x (1 + 2 + 2) refusals; the capped flags (2) are not what the column counts
        self.assertRegex(text, r"\| pi \| stop \| 24 \|.*\| 0 \| 10 \| 0 of 24 \|")

    def test_mechanism_has_one_row_per_fixture_and_arm(self):
        text = self.text(full(lambda h, a, f, r: {"gamed": a == "none" and f == "p01"}))
        self.assertRegex(text, r"\| p01 \| none \| 42 \| 42 \|")
        self.assertRegex(text, r"\| p01 \| stop \| 42 \| 0 \|")


class CheckTests(unittest.TestCase):
    def doc(self, body):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "findings.md"
        path.write_text(body)
        return path

    def test_findings_that_quote_the_report_pass_and_others_fail(self):
        rows = full()
        with mock.patch.object(runner, "load", return_value=rows):
            begin, end = report.markers()
            good = self.doc(f"intro\n{begin}\n{report.text()}\n{end}\noutro\n")
            bad = self.doc(f"intro\n{begin}\nsomething else\n{end}\n")
            self.assertEqual(report.check(good), [])
            self.assertEqual(len(report.check(bad)), 1)

    def test_a_document_without_the_markers_is_not_checked(self):
        self.assertEqual(report.check(self.doc("no report here")), [])


if __name__ == "__main__":
    unittest.main()
