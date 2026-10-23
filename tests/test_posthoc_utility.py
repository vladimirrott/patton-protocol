"""The post hoc utility note quotes exactly the breakdown the gate study's records give."""

from pathlib import Path
import sys
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lab"))

from patton_lab.posthoc import utility  # noqa: E402


class PosthocUtilityTests(unittest.TestCase):
    def test_the_note_quotes_the_breakdown_the_records_give(self):
        self.assertEqual(utility.main(["--check"]), 0)

    def test_the_check_fails_on_a_changed_number(self):
        real = utility.breakdown
        try:
            utility.breakdown = lambda runs: real(runs).replace("| 17 |", "| 18 |")
            self.assertEqual(utility.main(["--check"]), 1)
        finally:
            utility.breakdown = real

    def test_the_prose_quotes_only_numbers_the_breakdown_the_report_or_the_preregistration_print(self):
        from test_lab_study_integrity import unbacked_numbers
        doc = utility.FINDINGS.read_text(encoding="utf-8")
        prose = doc.split(utility.BEGIN)[0] + doc.split(utility.END)[1]
        sources = [utility.breakdown(REPO / "lab" / "cc_runs"), (REPO / "lab" / "CC_REPORT.md").read_text(),
                   (REPO / "lab" / "prereg" / "2026-10-21-gate-study-preregistration.md").read_text()]
        self.assertEqual(unbacked_numbers(prose, *sources), [])
        self.assertNotEqual(unbacked_numbers(prose + " utility 0.123 in 5 of 9", *sources), [])

    def test_every_gated_run_is_in_exactly_one_class(self):
        rows = [r for r in utility.load(REPO / "lab" / "cc_runs") if r["arm"] in ("O-R1", "O-R1S")]
        self.assertEqual(len(rows), 224)
        self.assertTrue(all(utility.gate_class(r) in utility.CLASSES for r in rows))


if __name__ == "__main__":
    unittest.main()
