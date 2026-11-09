"""P1X's pre-registration matches the code it will run, and the plugin it registers is patton-gate 0.7.0."""

from pathlib import Path
from unittest import mock
import hashlib
import importlib.util
import io
import re
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lab"))

from patton_lab.fleet import runner, suites  # noqa: E402
from patton_lab.p1d import detector  # noqa: E402
from patton_lab.p1v import prompts as p1v_prompts  # noqa: E402
from patton_lab.p1x import prereg, verdicts  # noqa: E402

COPY = REPO / prereg.REGISTERED_PLUGIN
LIVE = REPO / "plugins" / "patton-gate" / "scripts" / "patton_gate.py"
REGISTERED_SHA = "7a0de52a36e1e7284b42e2e2f7a22e23a6e1e106562a3fc2715713f11c5fae54"


def load(path: Path):
    spec = importlib.util.spec_from_file_location(f"patton_gate_{path.stem}_{abs(hash(str(path)))}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RegisteredPluginTests(unittest.TestCase):
    def test_the_copy_is_kept_byte_for_byte(self):
        self.assertEqual(hashlib.sha256(COPY.read_bytes()).hexdigest(), REGISTERED_SHA)

    def test_the_copy_is_patton_gate_0_7_0(self):
        self.assertEqual(load(COPY).VERSION, "0.7.0")

    def test_while_the_live_plugin_is_still_0_7_0_it_is_the_copy(self):
        if load(LIVE).VERSION == "0.7.0":
            self.assertEqual(LIVE.read_bytes(), COPY.read_bytes())

    def test_its_verifier_prompt_and_claim_reader_are_the_ones_p1l_ran(self):
        module = load(COPY)
        self.assertEqual(module.VERIFIER_SYSTEM, p1v_prompts.ARMS["fitted-v2"])
        self.assertEqual(module.CLAIMS_SYSTEM, p1v_prompts.gate.CLAIMS_SYSTEM)

    def test_the_detector_is_off_by_default_in_the_copy(self):
        self.assertIs(load(COPY).DEFAULTS["verify"]["detector"], False)

    def test_the_copys_detector_flags_what_the_labs_flags(self):
        before = {"tests/test_a.py": "assert f() == 153.0\n", "src/a.py": "def f():\n    return g()\n"}
        text = ("allowed_paths: [\"/work/src/**\"]\n### src/a.py (modified)\n```diff\n--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n"
                "-    return g()\n+    return 153.0\n```\n")
        lab, copy = detector.detect(before, text), load(COPY).fit_detect(before, text)
        self.assertTrue(lab.flag)
        self.assertEqual((copy.flag, copy.reasons), (lab.flag, lab.reasons))

    def test_the_generator_refuses_a_plugin_without_the_detector(self):
        with self.assertRaises(ValueError):
            prereg.registered_gate(REPO / "lab" / "p1h" / "registered" / "patton_gate.py")  # 0.6.0

    def test_the_generator_refuses_a_plugin_whose_prompt_was_not_measured(self):
        stale = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False)
        self.addCleanup(Path(stale.name).unlink)
        stale.write(Path(p1v_prompts.gate.__file__).read_text())
        stale.close()
        with self.assertRaises(ValueError):
            prereg.registered_gate(Path(stale.name))

    def test_the_generator_accepts_the_registered_copy(self):
        self.assertEqual(prereg.registered_gate().VERSION, "0.7.0")


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.text = prereg.PREREG_PATH.read_text(encoding="utf-8")

    def test_the_committed_preregistration_matches_the_code(self):
        self.assertEqual(prereg.check(), 0)

    def test_check_fails_when_the_file_is_missing_or_edited(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p1x.md"
            with mock.patch.object(prereg, "PREREG_PATH", path), mock.patch("sys.stdout", io.StringIO()):
                self.assertEqual(prereg.check(), 1)
                path.write_text(self.text + "\nedited\n")
                self.assertEqual(prereg.check(), 1)
                path.write_text(self.text)
                self.assertEqual(prereg.check(), 0)

    def test_check_names_this_study(self):
        lines = []
        self.assertEqual(prereg.check(log=lines.append), 0)
        self.assertEqual(lines, ["P1X pre-registration matches the code"])

    def test_every_frozen_input_is_hashed_in_the_document(self):
        for rel in prereg.FROZEN:
            self.assertIn(f"| `{rel}` | `{prereg.file_hash(rel)}` |", self.text, rel)

    def test_the_detector_and_the_rules_it_inherits_are_frozen(self):
        for rel in ("lab/patton_lab/p1d/detector.py", "lab/patton_lab/p1l/verdicts.py", "lab/patton_lab/fleetstudy/verdicts.py"):
            self.assertIn(rel, prereg.FROZEN)

    def test_the_document_names_the_seven_harnesses_and_the_stamp_of_each(self):
        for harness in runner.SETS["p1x"]["harnesses"]:
            self.assertRegex(self.text, rf"\| [^|\n]*\b{re.escape(harness)}\b[^|\n]* \| `[0-9a-f]{{64}}` \|")

    def test_the_predictions_quote_the_registered_thresholds(self):
        for needle in (f"p < {verdicts.ALPHA}", f"{verdicts.SHARE_HONESTY:.2f}", f"{verdicts.SHARE_GAMED:.2f}", f"{verdicts.MARGIN:.2f}",
                       str(verdicts.MIN_PAIRS), "U-arm", "O-arm", "G-arm", "H-arm", "Holm", "p02", "p03", "Eight verdicts"):
            self.assertIn(needle, self.text, needle)

    def test_it_says_the_honesty_share_limit_was_chosen_after_p1l(self):
        self.assertIn("chosen after P1L", self.text)

    def test_the_design_counts_match_the_set(self):
        cfg = runner.SETS["p1x"]
        runs = len(cfg["harnesses"]) * len(suites.SUITES["p1x"].arms) * len(cfg["fixtures"]) * cfg["reps"]
        self.assertEqual(runs, 504)
        self.assertIn(f"Runs: {runs}", self.text)
        self.assertIn(f"seed {cfg['seed']}", " ".join(self.text.split()))
        self.assertIn(f"${cfg['cap']:.2f}", self.text)

    def test_both_arms_configurations_are_printed(self):
        for arm in ("stop", "detect"):
            config = suites.SUITES["p1x"].prepare("p02", arm).config
            self.assertIn(prereg.json.dumps(config, sort_keys=True), self.text)
        self.assertIn('"detector": true', self.text)

    def test_the_counts_from_p1l_are_derived_from_its_records(self):
        rows = runner.load("p1l")
        over = {arm: sum(bool(r["metrics"]["overclaim"]) for r in rows if r["arm"] == arm) for arm in ("none", "stop")}
        self.assertIn(f"overclaims, none {over['none']} of 168, stop {over['stop']}", self.text)

    def test_the_document_is_dated_before_any_call_of_the_study(self):
        self.assertIn("before any call of this study", self.text)


class FleetCheckTests(unittest.TestCase):
    def test_the_fleet_runner_asks_this_module_for_its_check(self):
        self.assertEqual(runner.SETS["p1x"]["prereg"], "patton_lab.p1x.prereg")
        self.assertTrue(callable(prereg.check))


if __name__ == "__main__":
    unittest.main()
