"""The registered fleet studies' pre-registrations match the code, and a change to what they freeze is caught."""

from pathlib import Path
from unittest import mock
import sys
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lab"))

from patton_lab.fleet import runner  # noqa: E402
from patton_lab.fleetstudy import prereg, verdicts  # noqa: E402


class FleetPreregTests(unittest.TestCase):
    def test_the_committed_preregistrations_match_the_code(self):
        for which in ("h1r", "p1r"):
            self.assertEqual(prereg.check(which, lambda s: None), 0, which)

    def test_every_frozen_input_and_stamp_is_in_the_text(self):
        for which in ("h1r", "p1r"):
            text = prereg.PATHS[which].read_text(encoding="utf-8")
            for rel in prereg.FROZEN:
                self.assertIn(f"| `{rel}` | `{prereg.file_hash(rel)}` |", text)
            for harness in runner.SETS[which]["harnesses"]:
                self.assertIn(runner.stamp(harness), text)

    def test_a_changed_rule_or_frozen_file_breaks_the_check(self):
        with mock.patch.object(verdicts, "RATIO_SUPPORT", 0.8):
            self.assertEqual(prereg.check("h1r", lambda s: None), 1)
        with mock.patch.object(verdicts, "SHARE_SUPPORT", 0.6):
            self.assertEqual(prereg.check("p1r", lambda s: None), 1)
        with mock.patch.object(prereg, "file_hash", lambda rel: "0" * 64):
            self.assertEqual(prereg.check("h1r", lambda s: None), 1)
        with mock.patch.object(runner, "stamp", lambda harness: "f" * 64):
            self.assertEqual(prereg.check("p1r", lambda s: None), 1)


if __name__ == "__main__":
    unittest.main()
