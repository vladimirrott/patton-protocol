"""The Codex gate study as committed: records, cassettes, report, and the prose that quotes them."""

from pathlib import Path
import json
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "lab"))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from patton_lab.codex import prereg, report, runner  # noqa: E402
from test_lab_study_integrity import DECIMAL, PAIR, unbacked_numbers  # noqa: E402

LAB = REPO_ROOT / "lab"
STUDY = LAB / "codex_study"
PROSE = (REPO_ROOT / "docs" / "findings" / "2026-10-24-codex-gate-study-findings.md",
         REPO_ROOT / "docs" / "records" / "2026-10-24-codex-gate-study-record.md")


def sources():
    """What the Codex prose may quote: this study's report and pre-registration, and the Claude Code study's report
    for the comparison with gpt-oss-120b."""
    return ((LAB / "CODEX_REPORT.md").read_text(encoding="utf-8"),
            prereg.PREREG_PATH.read_text(encoding="utf-8"),
            (LAB / "CC_REPORT.md").read_text(encoding="utf-8"))


class CodexStudyTests(unittest.TestCase):
    def test_every_planned_run_is_stored_once(self):
        stored = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(STUDY.glob("*.json"))]
        keys = [(r["arm"], r["fixture"], r["rep"]) for r in stored]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(sorted(keys), sorted((a, f, r) for a in prereg.ARMS for f in prereg.FIXTURES
                                              for r in range(prereg.REPS)))
        self.assertTrue(all(r["finished"] for r in stored))

    def test_the_committed_report_is_the_one_the_records_give(self):
        self.assertEqual(report.main(["--check"]), 0)

    def test_every_stored_run_replays(self):
        self.assertEqual(runner.replay(STUDY), [])

    def test_the_prose_quotes_only_numbers_a_source_prints(self):
        findings = PROSE[0].read_text(encoding="utf-8")
        self.assertGreater(len(DECIMAL.findall(findings)) + len(PAIR.findall(findings)), 10,
                           "the findings must quote numbers for this check to mean anything")
        for path in PROSE:
            text = path.read_text(encoding="utf-8")
            self.assertEqual(unbacked_numbers(text, *sources()), [], path.name)

    def test_the_number_check_can_fail(self):
        text = PROSE[0].read_text(encoding="utf-8")
        self.assertNotEqual(unbacked_numbers(text + " harm 0.777 in 7 of 9 runs", *sources()), [])


if __name__ == "__main__":
    unittest.main()
