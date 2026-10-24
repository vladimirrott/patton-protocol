"""The T1 triage study as committed: every planned answer stored once, replay, the report, and the prose."""

from pathlib import Path
import json
import sys
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lab"))
sys.path.insert(0, str(REPO / "tests"))

from patton_lab.posthoc import triage_cases  # noqa: E402
from patton_lab.triage import flags, prereg, report, runner  # noqa: E402
from test_lab_study_integrity import DECIMAL, PAIR, unbacked_numbers  # noqa: E402

FINDINGS = REPO / "docs" / "findings" / "2026-10-24-triage-findings.md"
STUDY = runner.LAB / "triage_study"


def sources():
    return (report.REPORT.read_text(encoding="utf-8"), prereg.PREREG_PATH.read_text(encoding="utf-8"),
            (runner.LAB / "CC_REPORT.md").read_text(encoding="utf-8"), triage_cases.table())


class TriageStudyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = flags.collect(flags.TEST_SOURCES).flags

    def test_every_planned_answer_is_stored_once_and_replays(self):
        self.assertEqual(runner.replay(self.dataset, STUDY), [])
        total = sum(len(runner.load(runner.store_path(STUDY, m, c))) for m, c in runner.CELLS)
        self.assertEqual(total, sum(len(runner.cell_flags(self.dataset, c)) for _, c in runner.CELLS))

    def test_the_dataset_is_the_registered_one(self):
        self.assertIn(f"`{prereg.dataset_digest(self.dataset)}`", prereg.PREREG_PATH.read_text(encoding="utf-8"))

    def test_the_committed_report_is_the_one_the_answers_give(self):
        self.assertEqual(report.main(["--check"]), 0)

    def test_the_ledger_holds_every_stored_charge(self):
        stored = sum(r["usd"] for m, c in runner.CELLS for r in runner.load(runner.store_path(STUDY, m, c)))
        study_lines = sum(float(json.loads(line)["usd"])
                          for line in runner.LEDGER.read_text(encoding="utf-8").splitlines()
                          if line.startswith('{"id": "triage_study/'))
        self.assertAlmostEqual(stored, study_lines, places=6)

    def test_the_findings_quote_the_post_hoc_table_the_flags_give(self):
        self.assertEqual(triage_cases.main(["--check"]), 0)

    def test_the_findings_quote_only_numbers_a_source_prints(self):
        text = FINDINGS.read_text(encoding="utf-8")
        self.assertGreater(len(DECIMAL.findall(text)) + len(PAIR.findall(text)), 10)
        self.assertEqual(unbacked_numbers(text, *sources()), [])
        self.assertNotEqual(unbacked_numbers(text + " a miss rate of 0.777 in 7 of 9", *sources()), [])


if __name__ == "__main__":
    unittest.main()
