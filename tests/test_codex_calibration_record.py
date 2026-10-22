"""The Codex calibration record quotes `runner summary` verbatim, so no number in it is typed by hand."""

from pathlib import Path
import re
import sys
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lab"))

from patton_lab.codex import runner  # noqa: E402

RECORD = REPO / "docs" / "records" / "2026-10-22-codex-calibration.md"
RUNS = REPO / "lab" / "codex_runs"


class CalibrationRecordTests(unittest.TestCase):
    def test_the_quoted_summary_is_what_the_stored_runs_give(self):
        quoted = re.search(r"<!-- summary -->\n```\n(.*?)```", RECORD.read_text(encoding="utf-8"), re.S)
        self.assertIsNotNone(quoted, "the record has no summary block")
        dirs = sorted(p for p in RUNS.iterdir() if p.is_dir() and p.name != "cassettes")
        self.assertEqual(quoted.group(1), runner.summary(dirs, RUNS / "ORDER"))

    def test_every_stored_calibration_run_replays(self):
        for model_dir in sorted(p for p in RUNS.iterdir() if p.is_dir()):
            self.assertEqual(runner.replay(model_dir), [], model_dir.name)

    def test_the_order_file_lists_every_stored_run_once(self):
        listed = (RUNS / "ORDER").read_text(encoding="utf-8").split()
        stored = sorted(f"{p.parent.name}/{p.stem}" for p in RUNS.glob("*/*.json"))
        self.assertEqual(sorted(listed), stored)


if __name__ == "__main__":
    unittest.main()
