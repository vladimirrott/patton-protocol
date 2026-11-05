"""P1L's pre-registration matches the code it will run, and the plugin it registers is the one P1V and P1M measured."""

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
from patton_lab.p1l import prereg, verdicts  # noqa: E402
from patton_lab.p1v import prompts as p1v_prompts  # noqa: E402

COPY = REPO / prereg.REGISTERED_PLUGIN
LIVE = REPO / "plugins" / "patton-gate" / "scripts" / "patton_gate.py"
REGISTERED_SHA = "136c5a98c30bcff26ca3582aaa8fe908db04c1f75884fbf64f0ae8ac27466652"


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

    def test_the_copy_is_patton_gate_0_5_0(self):
        self.assertEqual(load(COPY).VERSION, "0.5.0")

    def test_while_the_live_plugin_is_still_0_5_0_it_is_the_copy(self):
        # a later edit of the plugin must bump its version, so the study's registered code is never silently changed
        if load(LIVE).VERSION == "0.5.0":
            self.assertEqual(LIVE.read_bytes(), COPY.read_bytes())

    def test_its_verifier_prompt_is_the_one_p1v_and_p1m_replayed(self):
        self.assertEqual(load(COPY).VERIFIER_SYSTEM, p1v_prompts.ARMS["fitted-v2"])

    def test_its_claim_reader_is_the_one_the_stored_runs_used(self):
        self.assertEqual(load(COPY).CLAIMS_SYSTEM, p1v_prompts.gate.CLAIMS_SYSTEM)

    def test_the_generator_refuses_a_registered_plugin_whose_prompt_was_not_measured(self):
        stale = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False)
        self.addCleanup(Path(stale.name).unlink)
        stale.write(Path(p1v_prompts.gate.__file__).read_text())  # patton-gate 0.4.0: its prompt has no ground
        stale.close()
        with self.assertRaises(ValueError):
            prereg.registered_gate(Path(stale.name))

    def test_the_generator_accepts_the_registered_copy(self):
        self.assertEqual(prereg.registered_gate().VERSION, "0.5.0")


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.text = prereg.PREREG_PATH.read_text(encoding="utf-8")

    def test_the_committed_preregistration_matches_the_code(self):
        self.assertEqual(prereg.check(), 0)

    def test_check_fails_when_the_file_is_missing_or_edited(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p1l.md"
            with mock.patch.object(prereg, "PREREG_PATH", path), mock.patch("sys.stdout", io.StringIO()):
                self.assertEqual(prereg.check(), 1)
                path.write_text(self.text + "\nedited\n")
                self.assertEqual(prereg.check(), 1)
                path.write_text(self.text)
                self.assertEqual(prereg.check(), 0)

    def test_every_frozen_input_is_hashed_in_the_document(self):
        for rel in prereg.FROZEN:
            self.assertIn(f"| `{rel}` | `{prereg.file_hash(rel)}` |", self.text, rel)

    def test_the_document_names_the_seven_harnesses_and_the_stamp_of_each(self):
        for harness in runner.SETS["p1l"]["harnesses"]:
            self.assertRegex(self.text, rf"\| [^|\n]*\b{re.escape(harness)}\b[^|\n]* \| `[0-9a-f]{{64}}` \|")

    def test_the_predictions_quote_the_registered_thresholds(self):
        for needle in (f"p < {verdicts.ALPHA}", f"{verdicts.SHARE_SUPPORT:.2f}", f"{verdicts.MARGIN:.2f}",
                       str(verdicts.MIN_PAIRS), "Q1", "Q2", "Q3", "Holm", "p02", "p03"):
            self.assertIn(needle, self.text, needle)

    def test_the_design_counts_match_the_set(self):
        cfg = runner.SETS["p1l"]
        runs = len(cfg["harnesses"]) * len(suites.SUITES["p1l"].arms) * len(cfg["fixtures"]) * cfg["reps"]
        self.assertEqual(runs, 336)
        self.assertIn(f"Runs: {runs}", self.text)
        self.assertIn(f"seed {cfg['seed']}", self.text)
        self.assertIn(f"${cfg['cap']:.2f}", self.text)

    def test_the_stop_arms_configuration_is_printed_so_it_can_be_compared_with_p1rs(self):
        config = suites.SUITES["p1l"].prepare("p02", "stop").config
        self.assertIn("check_before_stop", str(config))
        self.assertIn(prereg.json.dumps(config, sort_keys=True), self.text)

    def test_the_ground_is_quoted_word_for_word_and_the_prompt_hash_given(self):
        self.assertIn(p1v_prompts.FITTED_V2.rstrip(), self.text)
        self.assertIn(hashlib.sha256(p1v_prompts.ARMS["fitted-v2"].encode()).hexdigest(), self.text)

    def test_the_counts_from_p1r_are_derived_from_its_records(self):
        rows = runner.load("p1r")
        gamed = {arm: sum(bool(r["metrics"]["gamed"]) for r in rows if r["arm"] == arm) for arm in ("none", "stop")}
        self.assertIn(f"none {gamed['none']} of 112, stop {gamed['stop']} of 112", self.text)

    def test_the_document_is_dated_before_any_call_of_the_study(self):
        self.assertIn("before any call of this study", self.text)


class FleetCheckTests(unittest.TestCase):
    def test_the_fleet_runner_asks_this_module_for_its_check(self):
        self.assertEqual(runner.SETS["p1l"]["prereg"], "patton_lab.p1l.prereg")
        self.assertTrue(callable(prereg.check))


if __name__ == "__main__":
    unittest.main()
