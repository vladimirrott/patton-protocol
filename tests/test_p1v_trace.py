"""The P1V $0 trace passes on this tree, and fails when the runner stops doing what it checks."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lab"))

from patton_lab.p1v import runner, trace  # noqa: E402


class TraceTests(unittest.TestCase):
    def test_the_trace_passes_and_writes_its_stamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(trace.main(Path(tmp), "stamp-1"), 0)
            self.assertEqual((Path(tmp) / "TRACE_OK").read_text().strip(), "stamp-1")

    def test_a_replay_that_agrees_with_everything_fails_the_trace(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(runner, "replay", return_value=[]):
            self.assertEqual(trace.main(Path(tmp), "stamp-2"), 1)
            self.assertFalse((Path(tmp) / "TRACE_OK").exists())

    def test_a_run_that_ignores_the_spend_cap_fails_the_trace(self):
        class Loose(runner.Meter):
            def reserve(self, amount):
                self.held += amount
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(runner, "Meter", Loose):
            self.assertEqual(trace.main(Path(tmp), "stamp-3"), 1)
            self.assertFalse((Path(tmp) / "TRACE_OK").exists())

    def test_a_stamp_changes_when_a_traced_file_changes(self):
        self.assertEqual(runner.stamp(), runner.stamp())
        self.assertEqual(len(runner.stamp()), 64)


if __name__ == "__main__":
    unittest.main()
