"""P1M's record: the report equals what the stored answers give, the replay re-decides every stored answer, and the
findings quote only numbers the report, the pre-registration or the ledger print."""

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lab"))

from patton_lab.p1m import arms as arms_mod  # noqa: E402
from patton_lab.p1m import prereg, report, runner  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
FINDINGS = REPO / "docs" / "findings" / "2026-11-04-p1m-findings.md"
STUDY = runner.LAB / "p1m_study"


@unittest.skipUnless(FINDINGS.exists() and report.REPORT.exists(), "the P1M study has not been recorded yet")
class P1MRecordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.findings = FINDINGS.read_text(encoding="utf-8")
        cls.report = report.REPORT.read_text(encoding="utf-8")

    def test_the_committed_report_equals_the_stored_answers(self):
        self.assertEqual(report.main(["--check"]), 0)

    def test_the_preregistration_still_matches_the_code(self):
        self.assertEqual(prereg.check(), 0)

    def test_every_stored_study_answer_redecides_the_same(self):
        names = runner.draw_names(arms_mod.ARMS, runner.DRAWS["study"])
        self.assertEqual(runner.replay(runner.datasets("study"), STUDY, names), [])

    def test_the_study_holds_one_call_per_case_arm_and_draw(self):
        names = runner.draw_names(arms_mod.ARMS, runner.DRAWS["study"])
        total = sum(len(runner.load(runner.store_path(STUDY, n))) for n in names)
        self.assertEqual(total, len(names) * 89)

    def test_every_count_in_the_findings_is_in_the_report(self):
        for match in set(re.findall(r"\b\d+ of \d+\b", self.findings)):
            with self.subTest(count=match):
                self.assertIn(match, self.report)

    def test_every_decimal_in_the_findings_is_in_the_report_the_preregistration_or_the_ledger(self):
        rows = [json.loads(line) for line in (runner.LAB / "p1m_spend.jsonl").read_text().splitlines() if line.strip()]
        study = sum(r["usd"] for r in rows if r["id"].startswith("p1m_study/"))
        money = f"{sum(r['usd'] for r in rows):.2f} {study:.2f} {runner.SETS['study']:.2f}"
        evidence = "\n".join((self.report, prereg.PREREG_PATH.read_text(encoding="utf-8"), money))
        for match in set(re.findall(r"(?<![\w.])\d+\.\d+(?![\w.])", self.findings)):
            with self.subTest(decimal=match):
                self.assertIn(match, evidence)

    def test_the_costs_are_the_ledgers(self):
        rows = [json.loads(line) for line in (runner.LAB / "p1m_spend.jsonl").read_text().splitlines() if line.strip()]
        study = sum(r["usd"] for r in rows if r["id"].startswith("p1m_study/"))
        self.assertIn(f"${study:.2f}", self.findings)
        self.assertLessEqual(sum(r["usd"] for r in rows), runner.SETS["study"])

    def test_the_verdict_words_in_the_findings_are_the_reports(self):
        for verdict in re.findall(r"\b(SUPPORTED|NOT SUPPORTED|INCONCLUSIVE|VOID)\b", self.report):
            self.assertIn(verdict, self.findings)


if __name__ == "__main__":
    unittest.main()
