"""The Verifier replay as committed: every planned answer stored once, replay, the report, and the prose."""

from pathlib import Path
import json
import sys
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lab"))
sys.path.insert(0, str(REPO / "tests"))

from patton_lab.posthoc import verify_cases  # noqa: E402
from patton_lab.verify import cases, prereg, report, runner  # noqa: E402
from test_lab_study_integrity import DECIMAL, PAIR, unbacked_numbers  # noqa: E402

FINDINGS = verify_cases.FINDINGS
STUDY = runner.LAB / "verify_study"


def sources():
    return (report.REPORT.read_text(encoding="utf-8"), prereg.PREREG_PATH.read_text(encoding="utf-8"),
            verify_cases.table())


class VerifyStudyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = cases.collect(cases.TEST_SOURCES)

    def test_every_planned_answer_is_stored_once_and_replays(self):
        self.assertEqual(runner.replay(self.dataset, STUDY, runner.MODELS), [])
        for model in runner.MODELS:
            ids = [r["case"] for r in runner.load(runner.store_path(STUDY, model))]
            self.assertEqual(sorted(ids), sorted(c.id for c in self.dataset), model)

    def test_the_dataset_is_the_registered_one(self):
        self.assertIn(f"`{prereg.dataset_digest(self.dataset)}`", prereg.PREREG_PATH.read_text(encoding="utf-8"))

    def test_the_committed_report_is_the_one_the_answers_give(self):
        self.assertEqual(report.main(["--check"]), 0)

    def test_the_ledger_holds_every_stored_charge(self):
        stored = sum(r["usd"] for m in runner.MODELS for r in runner.load(runner.store_path(STUDY, m)))
        study_lines = sum(float(json.loads(line)["usd"])
                          for line in runner.LEDGER.read_text(encoding="utf-8").splitlines()
                          if line.startswith('{"id": "verify_study/'))
        self.assertGreater(stored, 0)
        self.assertAlmostEqual(stored, study_lines, places=6)

    def test_the_findings_quote_the_post_hoc_table_the_answers_give(self):
        self.assertEqual(verify_cases.main(["--check"]), 0)

    def test_the_findings_quote_only_numbers_a_source_prints(self):
        text = FINDINGS.read_text(encoding="utf-8")
        self.assertGreater(len(DECIMAL.findall(text)) + len(PAIR.findall(text)), 10)
        self.assertEqual(unbacked_numbers(text, *sources()), [])
        self.assertNotEqual(unbacked_numbers(text + " a flag rate of 0.777 in 7 of 9", *sources()), [])


if __name__ == "__main__":
    unittest.main()
