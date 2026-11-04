"""P1V's record: the report equals what the stored answers give, the replay re-decides every stored answer, and the
findings quote only numbers the report or the post hoc script prints."""

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lab"))

from patton_lab.p1v import posthoc, prereg, prompts, report, runner  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
FINDINGS = REPO / "docs" / "findings" / "2026-11-04-p1v-findings.md"


class P1VRecordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.findings = FINDINGS.read_text(encoding="utf-8")
        cls.report = report.REPORT.read_text(encoding="utf-8")

    def test_the_committed_report_equals_the_stored_answers(self):
        self.assertEqual(report.main(["--check"]), 0)

    def test_the_preregistration_still_matches_the_code(self):
        self.assertEqual(prereg.check(), 0)

    def test_every_stored_study_answer_redecides_the_same(self):
        self.assertEqual(runner.replay(runner.datasets("study"), runner.LAB / "p1v_study", prompts.STUDY_ARMS), [])

    def test_the_study_holds_356_calls_one_per_case_arm_and_draw(self):
        total = sum(len(runner.load(runner.store_path(runner.LAB / "p1v_study", n))) for n in prompts.STUDY_ARMS)
        self.assertEqual(total, 4 * 89)

    def test_the_post_hoc_block_is_what_the_script_prints(self):
        block = re.search(r"<!-- posthoc -->\n(.*?)\n<!-- /posthoc -->", self.findings, re.S)
        self.assertIsNotNone(block)
        self.assertEqual(block.group(1), posthoc.build())

    def test_every_count_in_the_findings_is_in_the_report_or_the_post_hoc_block(self):
        evidence = self.report + posthoc.build()
        own = re.sub(r"<!-- posthoc -->.*?<!-- /posthoc -->", "", self.findings, flags=re.S)
        for match in set(re.findall(r"\b\d+ of \d+\b", own)):
            with self.subTest(count=match):
                self.assertIn(match, evidence)

    def test_every_decimal_in_the_findings_is_in_the_report_the_preregistration_or_the_ledger(self):
        rows = [json.loads(line) for line in (runner.LAB / "p1v_spend.jsonl").read_text().splitlines() if line.strip()]
        money = f"{sum(r['usd'] for r in rows):.2f} {sum(r['usd'] for r in rows if r['id'].startswith('p1v_study/')):.2f} " \
                f"{runner.SETS['study']:.2f}"
        evidence = "\n".join((self.report, posthoc.build(), prereg.PREREG_PATH.read_text(encoding="utf-8"), money))
        for match in set(re.findall(r"(?<![\w.])\d+\.\d+(?![\w.])", self.findings)):
            with self.subTest(decimal=match):
                self.assertIn(match, evidence)

    def test_the_headline_numbers_are_the_reports(self):
        for needle in ("28 of 41 (0.683)", "0.544", "0 of 33", "0.087", "1.192e-07", "31 of 41 (0.756)",
                       "28 of 30 (0.933)"):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.findings)
                self.assertIn(needle, self.report)
        self.assertEqual(report.verdict_recall(28, 41), "SUPPORTED")

    def test_the_costs_are_the_ledgers(self):
        rows = [json.loads(line) for line in (runner.LAB / "p1v_spend.jsonl").read_text().splitlines() if line.strip()]
        total = sum(r["usd"] for r in rows)
        study = sum(r["usd"] for r in rows if r["id"].startswith("p1v_study/"))
        self.assertIn(f"${study:.2f}", self.findings)
        self.assertIn(f"${total:.2f}", self.findings)
        self.assertLessEqual(total, runner.SETS["study"])
        self.assertEqual(sum(1 for r in rows if r["id"].startswith("p1v_study/")), 356)


if __name__ == "__main__":
    unittest.main()
