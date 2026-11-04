"""P1M report rules: the family-level paired test, the reasoning-applied guard, and the development summary."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lab"))

from patton_lab.p1m import arms as arms_mod  # noqa: E402
from patton_lab.p1m import report  # noqa: E402


def rec(out_tokens):
    return {"reply": {"attempts": [{"usage": {"completion_tokens": out_tokens}}]}}


class RuleTests(unittest.TestCase):
    def test_the_family_splits_the_five_percent_level(self):
        self.assertEqual(report.FAMILY, len(arms_mod.ARMS))
        self.assertAlmostEqual(report.ALPHA, 0.05 / 2)

    def test_gain_needs_more_flags_and_a_small_p_at_the_family_level(self):
        self.assertEqual(report.verdict_gain(6, 0), "SUPPORTED")       # p = 1/64 = 0.0156 < 0.025
        self.assertEqual(report.verdict_gain(5, 0), "INCONCLUSIVE")    # p = 1/32 = 0.031: passes 0.05, fails 0.025
        self.assertEqual(report.verdict_gain(3, 3), "NOT SUPPORTED")
        self.assertEqual(report.verdict_gain(2, 9), "NOT SUPPORTED")

    def test_output_guard_voids_an_arm_that_did_not_reason(self):
        ref = [rec(100), rec(140)]
        self.assertTrue(report.applied([rec(400), rec(500)], ref))
        self.assertFalse(report.applied([rec(300), rec(330)], ref))       # 315 < 3 x 120
        self.assertTrue(report.applied([rec(360)], ref))                   # exactly 3 x 120 passes

    def test_mean_output_refuses_when_nothing_was_recorded(self):
        with self.assertRaises(ValueError):
            report.mean_output([{"reply": {"attempts": [{"usage": None}]}}])

    def test_the_guard_ratio_is_the_registered_three(self):
        self.assertEqual(report.MIN_OUTPUT_RATIO, 3.0)


class StoredDevTests(unittest.TestCase):
    @unittest.skipUnless((report.LAB / "p1m_dev").exists(), "no stored dev run")
    def test_the_dev_summary_covers_the_reference_and_every_arm_with_no_false_flags(self):
        text = report.dev_summary()
        for label in ("q38-none (P1V)", *arms_mod.ALL):
            self.assertIn(f"| {label} |", text)
        self.assertEqual(text.count("0 of 14 (0.000)"), 1 + len(arms_mod.ALL))


if __name__ == "__main__":
    unittest.main()
