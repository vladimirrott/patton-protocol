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
            rows = runner.load(which)  # a study that has run is held to the code its runs recorded
            for harness in runner.SETS[which]["harnesses"]:
                stamps = {r["stamp"] for r in rows if r["harness"] == harness} or {runner.stamp(harness)}
                for digest in stamps:
                    self.assertIn(digest, text)

    def test_a_study_with_records_is_held_to_the_code_its_runs_ran_on(self):
        rows = [{"harness": h, "stamp": "a" * 64} for h in runner.SETS["h1r"]["harnesses"]]
        with mock.patch.object(runner, "load", lambda which: rows if which == "h1r" else []):
            self.assertIn("`" + "a" * 64 + "`", prereg.h1r_text())
            with mock.patch.object(runner, "stamp", lambda harness: "f" * 64):
                self.assertNotIn("f" * 64, prereg.h1r_text())  # the live code no longer decides
            rows[0]["stamp"] = "b" * 64  # a run on other code shows as its own row, so the check fails
            self.assertIn("`" + "b" * 64 + "`", prereg.h1r_text())

    def test_a_changed_rule_or_frozen_file_breaks_the_check(self):
        with mock.patch.object(verdicts, "RATIO_SUPPORT", 0.8):
            self.assertEqual(prereg.check("h1r", lambda s: None), 1)
        with mock.patch.object(verdicts, "SHARE_SUPPORT", 0.6):
            self.assertEqual(prereg.check("p1r", lambda s: None), 1)
        with mock.patch.object(prereg, "file_hash", lambda rel: "0" * 64):
            self.assertEqual(prereg.check("h1r", lambda s: None), 1)
        with mock.patch.object(runner, "stamp", lambda harness: "f" * 64), \
                mock.patch.object(runner, "load", lambda which: []):
            self.assertEqual(prereg.check("p1r", lambda s: None), 1)


if __name__ == "__main__":
    unittest.main()
