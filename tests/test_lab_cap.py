"""The lab-wide cap after the CTO's approval of $50 more on 2026-10-25.

The cap new runs check is `spend.GLOBAL_CAP`. `ledgers.GLOBAL_CAP` stays at $40 because the triage
pre-registration froze that file, and the closed studies' guards keep reading it.
"""

from pathlib import Path
import json
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lab"))

from patton_lab import ledgers, spend  # noqa: E402
from patton_lab.fleet import money  # noqa: E402


def lab_with(spent):
    tmp = tempfile.TemporaryDirectory()
    path = Path(tmp.name)
    (path / "results").mkdir()
    (path / "results" / "spend.json").write_text(json.dumps({"spent": {"groq": spent}}))
    return tmp, money.Meter(path, path / spend.LINE_LEDGERS["fleet"], 100.0)


class LabCapTests(unittest.TestCase):
    def test_new_runs_count_against_ninety_dollars_and_the_frozen_file_keeps_forty(self):
        self.assertEqual(spend.GLOBAL_CAP, 90.0)
        self.assertEqual(ledgers.GLOBAL_CAP, 40.0)

    def test_a_fleet_call_past_forty_dollars_is_allowed_and_one_past_ninety_is_refused(self):
        tmp, meter = lab_with(45.0)
        self.addCleanup(tmp.cleanup)
        meter.reserve(1.0)
        tmp, meter = lab_with(89.5)
        self.addCleanup(tmp.cleanup)
        with self.assertRaises(money.SpendStop):
            meter.reserve(1.0)


if __name__ == "__main__":
    unittest.main()


class StudyLedgerTests(unittest.TestCase):
    def test_each_registered_study_keeps_its_own_ledger_and_the_lab_total_counts_it(self):
        from patton_lab.fleet import runner
        for which in ("h1r", "p1r"):
            ledger = runner.SETS[which]["ledger"]
            self.assertIn(ledger, spend.LINE_LEDGERS.values())
            self.assertNotEqual(ledger, spend.LINE_LEDGERS["fleet"])
        self.assertNotEqual(runner.SETS["h1r"]["ledger"], runner.SETS["p1r"]["ledger"])
        self.assertNotIn("ledger", runner.SETS["h1"])

    def test_a_study_cap_ignores_what_the_pilots_spent(self):
        tmp, _ = lab_with(30.0)
        self.addCleanup(tmp.cleanup)
        lab = Path(tmp.name)
        (lab / spend.LINE_LEDGERS["fleet"]).write_text(json.dumps({"id": "x", "usd": 7.9}) + "\n")
        study = money.Meter(lab, lab / spend.LINE_LEDGERS["fleet h1r"], 8.0)
        study.reserve(1.0)
        with self.assertRaises(money.SpendStop):
            money.Meter(lab, lab / spend.LINE_LEDGERS["fleet"], 8.0).reserve(1.0)
