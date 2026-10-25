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

    def test_the_registered_plugin_is_kept_byte_for_byte(self):
        # the study ran on patton-gate 0.2.x; the plugin has moved on, so the registered copy stands in for it
        copy = prereg.REPO / prereg.REGISTERED["plugins/patton-gate/scripts/patton_gate.py"]
        self.assertEqual(prereg.file_hash("plugins/patton-gate/scripts/patton_gate.py"),
                         "6d766a838108093c1950739b9caa83b4468482a0f70327eab9658088dede91fd")
        self.assertEqual(prereg.hashlib.sha256(copy.read_bytes()).hexdigest(),
                         prereg.file_hash("plugins/patton-gate/scripts/patton_gate.py"))

    def test_the_live_plugin_still_reads_the_same_flags(self):
        live = prereg.dataset_digest(prereg.flags_mod.collect(prereg.flags_mod.TEST_SOURCES).flags)
        with prereg.registered_gate():
            registered = prereg.dataset_digest(prereg.flags_mod.collect(prereg.flags_mod.TEST_SOURCES).flags)
        self.assertEqual(live, registered)

    def test_every_frozen_input_is_hashed(self):
        text = prereg.PREREG_PATH.read_text(encoding="utf-8")
        for rel in prereg.FROZEN:
            self.assertIn(f"| `{rel}` | `{prereg.file_hash(rel)}` |", text)


if __name__ == "__main__":
    unittest.main()
