"""The fleet pilots' summary: its statistics against closed forms, and the findings check against the records."""

from pathlib import Path
from unittest import mock
import sys
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lab"))

from patton_lab.posthoc import fleet_pilots  # noqa: E402

sys.path.insert(0, str(REPO / "tests"))
from test_lab_study_integrity import unbacked_numbers  # noqa: E402


class StatsTests(unittest.TestCase):
    def test_the_upper_bound_after_no_events_is_one_minus_the_nth_root_of_five_percent(self):
        for n in (6, 48, 96):
            self.assertAlmostEqual(fleet_pilots.upper95(0, n), 1 - 0.05 ** (1 / n), places=6)

    def test_the_upper_bound_after_one_event_in_ten(self):
        bound = fleet_pilots.upper95(1, 10)
        tail = (1 - bound) ** 10 + 10 * bound * (1 - bound) ** 9
        self.assertAlmostEqual(tail, 0.05, places=6)

    def test_the_sign_test(self):
        self.assertAlmostEqual(fleet_pilots.sign_test(5, 0), 2 / 32)
        self.assertAlmostEqual(fleet_pilots.sign_test(3, 3), 1.0)
        self.assertAlmostEqual(fleet_pilots.sign_test(0, 0), 1.0)


class CheckTests(unittest.TestCase):
    def test_the_check_refuses_a_findings_file_with_one_number_changed(self):
        with mock.patch.object(fleet_pilots, "block", lambda which: "| a | 1 |\n\n- 3 runs" if which == "h1" else ""):
            good = "x\n<!-- h1 -->\n| a | 1 |\n\n- 3 runs\n<!-- /h1 -->\n"
            self.assertEqual(fleet_pilots.check(good), [])
            self.assertTrue(fleet_pilots.check(good.replace("3 runs", "4 runs")))
            self.assertTrue(fleet_pilots.check("no blocks at all"))

    def test_the_committed_findings_quote_what_the_records_give(self):
        self.assertEqual(fleet_pilots.check(fleet_pilots.FINDINGS.read_text(encoding="utf-8")), [])

    def test_the_prose_quotes_only_numbers_a_block_or_its_sources_print(self):
        text = fleet_pilots.FINDINGS.read_text(encoding="utf-8")
        blocks = [fleet_pilots.block(which) for which in ("h1", "p1")]
        for which in ("h1", "p1"):
            begin, end = fleet_pilots.markers(which)
            head, rest = text.split(begin, 1)
            text = head + rest.split(end, 1)[1]
        sources = [(REPO / "docs" / "records" / "2026-10-25-pilot-readiness.md").read_text(encoding="utf-8")]
        sources += [p.read_text(encoding="utf-8") for p in (REPO / "lab" / "prereg").glob("2026-10-25-fleet-*.md")]
        self.assertEqual(unbacked_numbers(text, *blocks, *sources), [])


if __name__ == "__main__":
    unittest.main()
