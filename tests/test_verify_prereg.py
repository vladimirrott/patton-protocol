"""The Verifier replay's pre-registration matches the code it ran on, after the plugin has moved on."""

from pathlib import Path
import hashlib
import sys
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lab"))

from patton_lab.verify import cases, prereg, runner, trace  # noqa: E402

PLUGIN = "plugins/patton-gate/scripts/patton_gate.py"
# patton-gate 0.3.1 as it stood in commit e91854a, the version the 800 stored answers came from
REGISTERED_SHA = "f32ca729797763ae14db9d2a10da65b7fcc4d6ed87600ac3459778a79bfc9abf"


class VerifyPreregTests(unittest.TestCase):
    def test_the_committed_preregistration_matches_the_code(self):
        self.assertEqual(prereg.check(), 0)

    def test_the_registered_plugin_is_kept_byte_for_byte(self):
        copy = prereg.REPO / prereg.REGISTERED[PLUGIN]
        self.assertEqual(hashlib.sha256(copy.read_bytes()).hexdigest(), REGISTERED_SHA)
        self.assertEqual(prereg.file_hash(PLUGIN), REGISTERED_SHA)

    def test_every_frozen_input_is_hashed(self):
        text = prereg.PREREG_PATH.read_text(encoding="utf-8")
        for rel in prereg.FROZEN:
            self.assertIn(f"| `{rel}` | `{prereg.file_hash(rel)}` |", text)

    def test_the_replay_reads_the_registered_plugin_and_puts_the_live_one_back(self):
        live = cases.gate
        with prereg.registered_gate() as module:
            self.assertEqual(Path(module.__file__).resolve(), (prereg.REPO / prereg.REGISTERED[PLUGIN]).resolve())
            for holder in (cases, runner, trace):
                self.assertIs(holder.gate, module, holder.__name__)
            self.assertEqual(cases.CFG, module.DEFAULTS["verify"])
        for holder in (cases, runner, trace):
            self.assertIs(holder.gate, live, holder.__name__)


if __name__ == "__main__":
    unittest.main()
