"""P1V report: exact binomial bounds, the paired sign test, and the three verdict rules, each checked from the
definition rather than from the implementation."""

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lab"))

from patton_lab.p1v import report  # noqa: E402


def cdf(k, n, p):
    return sum(math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k + 1))


class BoundTests(unittest.TestCase):
    def test_the_upper_bound_puts_five_percent_in_the_lower_tail(self):
        for k, n in ((0, 33), (1, 33), (3, 33), (10, 41), (40, 41)):
            with self.subTest(k=k, n=n):
                self.assertAlmostEqual(cdf(k, n, report.exact_upper(k, n)), 0.05, places=9)

    def test_the_lower_bound_puts_five_percent_in_the_upper_tail(self):
        for k, n in ((1, 33), (26, 41), (30, 41), (41, 41), (5, 10)):
            with self.subTest(k=k, n=n):
                self.assertAlmostEqual(1 - cdf(k - 1, n, report.exact_lower(k, n)), 0.05, places=9)

    def test_the_edges(self):
        self.assertEqual(report.exact_lower(0, 41), 0.0)
        self.assertEqual(report.exact_upper(41, 41), 1.0)
        self.assertAlmostEqual(report.exact_upper(0, 33), 1 - 0.05 ** (1 / 33), places=9)
        self.assertAlmostEqual(report.exact_lower(41, 41), 0.05 ** (1 / 41), places=9)

    def test_an_empty_denominator_refuses(self):
        for fn in (report.exact_lower, report.exact_upper):
            with self.assertRaises(ValueError):
                fn(0, 0)

    def test_a_count_above_the_denominator_refuses(self):
        with self.assertRaises(ValueError):
            report.exact_lower(5, 4)


class SignTests(unittest.TestCase):
    def test_one_sided_exact_values(self):
        self.assertAlmostEqual(report.sign_p(10, 0), 0.5**10)
        self.assertAlmostEqual(report.sign_p(3, 1), (math.comb(4, 3) + 1) / 16)
        self.assertEqual(report.sign_p(0, 0), 1.0)
        self.assertEqual(report.sign_p(0, 5), 1.0)


class VerdictTests(unittest.TestCase):
    def test_recall_rule(self):
        self.assertEqual(report.verdict_recall(41, 41), "SUPPORTED")
        self.assertEqual(report.verdict_recall(20, 41), "NOT SUPPORTED")  # point 0.488 < 0.50
        self.assertEqual(report.verdict_recall(22, 41), "INCONCLUSIVE")  # point 0.537, lower bound under 0.50
        lo = [k for k in range(42) if report.exact_lower(k, 41) > 0.5]
        self.assertEqual(report.verdict_recall(min(lo), 41), "SUPPORTED")
        self.assertNotEqual(report.verdict_recall(min(lo) - 1, 41), "SUPPORTED")

    def test_false_flag_rule(self):
        self.assertEqual(report.verdict_false(0, 33), "SUPPORTED")
        self.assertEqual(report.verdict_false(10, 33), "NOT SUPPORTED")
        ok = [k for k in range(34) if report.exact_upper(k, 33) <= 0.15]
        self.assertEqual(report.verdict_false(max(ok), 33), "SUPPORTED")
        self.assertNotEqual(report.verdict_false(max(ok) + 1, 33), "SUPPORTED")

    def test_paired_rule(self):
        self.assertEqual(report.verdict_paired(10, 0), "SUPPORTED")
        self.assertEqual(report.verdict_paired(2, 2), "NOT SUPPORTED")
        self.assertEqual(report.verdict_paired(1, 3), "NOT SUPPORTED")
        self.assertEqual(report.verdict_paired(3, 1), "INCONCLUSIVE")

    def test_the_thresholds_are_the_registered_ones(self):
        self.assertEqual((report.RECALL_LIMIT, report.FALSE_FLAG_LIMIT, report.ALPHA), (0.50, 0.15, 0.05))


if __name__ == "__main__":
    unittest.main()
