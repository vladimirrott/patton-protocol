"""The registered studies' report: it says when it is not final, and its check catches a changed verdict."""

from pathlib import Path
from unittest import mock
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lab"))

from patton_lab.fleet import runner  # noqa: E402
from patton_lab.fleetstudy import report  # noqa: E402

TABLE = "# t\n\nA pilot.\n\n| Harness |\n| --- |\n"


def rows(n):
    return [{"harness": "pi", "arm": arm, "fixture": "f04", "rep": i, "rc": 0, "timed_out": False, "usd": 0.01,
             "ledger": [], "metrics": {"harm": 0, "approvals": 1 if arm == "rules" else 0, "utility": 0.0,
                                       "triaged": 0}}
            for i in range(n) for arm in ("rules", "triage")]


class ReportTests(unittest.TestCase):
    def setUp(self):
        for p in (mock.patch.object(runner, "report", lambda which: TABLE),):
            p.start()
            self.addCleanup(p.stop)

    def test_a_partial_study_is_marked_not_final(self):
        with mock.patch.object(runner, "load", lambda which: rows(3)):
            out = report.text("h1r")
        self.assertIn("Not final: 6 of 768", out)
        self.assertIn("- R1: **SUPPORTED**", out)

    def test_a_complete_study_is_final(self):
        with mock.patch.object(runner, "load", lambda which: rows(3)), \
                mock.patch.object(runner, "cells", lambda which, hs: [None] * 6):
            self.assertIn("Final: all 6", report.text("h1r"))

    def test_the_check_catches_a_changed_verdict(self):
        with mock.patch.object(runner, "load", lambda which: rows(3)):
            begin, end = report.markers("h1r")
            good = f"x\n{begin}\n{report.text('h1r')}\n{end}\n"
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "f.md"
                path.write_text(good)
                self.assertEqual(report.check(path), [])
                path.write_text(good.replace("R1: **SUPPORTED**", "R1: **NOT SUPPORTED**"))
                self.assertTrue(report.check(path))


if __name__ == "__main__":
    unittest.main()
