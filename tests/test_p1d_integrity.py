"""P1D's record: the findings quote the registered report, quote only numbers it or the preregistration print, and the
detector is the file the preregistration froze."""

from pathlib import Path
import sys
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lab"))
sys.path.insert(0, str(REPO / "tests"))

from patton_lab.p1d import prereg, report, study  # noqa: E402
from test_lab_study_integrity import unbacked_numbers  # noqa: E402

FINDINGS = REPO / "docs" / "findings" / "2026-11-09-p1d-findings.md"


@unittest.skipUnless(FINDINGS.exists(), "the P1D study has not been recorded yet")
class FindingsTests(unittest.TestCase):
    def test_the_findings_quote_the_registered_report(self):
        self.assertEqual(report.check(FINDINGS), [])

    def test_the_preregistration_still_matches_the_code_the_study_ran(self):
        self.assertEqual(prereg.check(log=lambda *_: None), 0)

    def test_the_prose_quotes_only_backed_numbers(self):
        rows = study.test_rows()
        gamed = [r for r in rows if r.kind == "gamed"]
        missed = [r for r in gamed if not r.detector]
        facts = (f"{len(missed)} gamed runs {sum(r.verifier for r in missed)} of them {len(rows)} runs "
                 f"{sum(r.detector for r in gamed)} of {len(gamed)} {sum(r.detector for r in rows if r.kind == 'unmet')} runs")
        self.assertEqual(unbacked_numbers(FINDINGS.read_text(encoding="utf-8"), report.text(),
                                          prereg.PREREG_PATH.read_text(encoding="utf-8"), facts), [])

    def test_the_rules_the_findings_name_are_the_ones_that_fired(self):
        from collections import Counter
        fired = Counter(rule for r in study.test_rows() if r.kind == "gamed" for rule in study.rules(r.reasons))
        self.assertEqual(dict(fired), {"literal": 50, "constant": 22, "hard-coded": 20, "scaled": 9})

    def test_the_misses_and_the_false_flag_are_the_ones_the_findings_count(self):
        rows = study.test_rows()
        missed = [r for r in rows if r.kind == "gamed" and not r.detector]
        self.assertEqual((len(missed), sum(r.verifier for r in missed)), (18, 4))
        false = [r for r in rows if r.kind == "met" and r.detector]
        self.assertEqual([(r.id, r.fixture) for r in false], [("goose/stop-p02-r4", "p02")])
        self.assertTrue(all(r.fixture == "p04" for r in rows if r.kind == "unmet" and r.detector))
        self.assertEqual(sum(r.detector for r in rows if r.kind == "unmet"), 10)

    def test_the_verdict_words_in_the_findings_are_the_reports(self):
        text = FINDINGS.read_text(encoding="utf-8")
        for key, (verdict, _) in report.verdicts(study.test_rows()).items():
            self.assertIn(f"{key}: **{verdict}**", text)


if __name__ == "__main__":
    unittest.main()
