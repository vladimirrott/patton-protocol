"""The T1 triage pre-registration matches the code, and the study refuses to start when it does not."""

from pathlib import Path
from unittest import mock
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lab"))

from patton_lab.triage import prereg, runner  # noqa: E402


def _must_not_spend(*args, **kwargs):
    raise AssertionError("a guarded path reached the network")


class PreregTests(unittest.TestCase):
    def test_the_committed_preregistration_matches_the_code(self):
        self.assertEqual(prereg.check(), 0)

    def test_the_study_refuses_when_the_preregistration_is_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "TRACE_OK").write_text(runner.stamp() + "\n")
            with mock.patch.object(runner, "TRACE_DIR", Path(tmp)), \
                    mock.patch.object(runner, "post", side_effect=_must_not_spend), \
                    mock.patch.object(prereg, "check", return_value=1):
                self.assertEqual(runner.main(["study"]), 2)

    def test_every_frozen_input_is_hashed(self):
        text = prereg.PREREG_PATH.read_text(encoding="utf-8")
        for rel in prereg.FROZEN:
            self.assertIn(f"| `{rel}` | `{prereg.file_hash(rel)}` |", text)


if __name__ == "__main__":
    unittest.main()
