"""P1H's pre-registration matches the code it will run, and the plugin it registers is patton-gate 0.6.0."""

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
from patton_lab.p1h import prereg, verdicts  # noqa: E402
from patton_lab.p1v import prompts as p1v_prompts  # noqa: E402

COPY = REPO / prereg.REGISTERED_PLUGIN
LIVE = REPO / "plugins" / "patton-gate" / "scripts" / "patton_gate.py"
REGISTERED_SHA = "8bcebdc94c06555badd4c8a5ab3990a17e34e80eba37e9194026861ea46054f0"


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

    def test_the_copy_is_patton_gate_0_6_0(self):
        self.assertEqual(load(COPY).VERSION, "0.6.0")

    def test_while_the_live_plugin_is_still_0_6_0_it_is_the_copy(self):
        # a later edit of the plugin must bump its version, so the study's registered code is never silently changed
        if load(LIVE).VERSION == "0.6.0":
            self.assertEqual(LIVE.read_bytes(), COPY.read_bytes())

    def test_its_verifier_prompt_is_the_one_p1l_ran(self):
        self.assertEqual(load(COPY).VERIFIER_SYSTEM, p1v_prompts.ARMS["fitted-v2"])

    def test_its_claim_reader_is_the_one_the_stored_runs_used(self):
        self.assertEqual(load(COPY).CLAIMS_SYSTEM, p1v_prompts.gate.CLAIMS_SYSTEM)

    def test_hold_is_off_by_default_in_the_copy(self):
        self.assertIs(load(COPY).DEFAULTS["verify"]["hold"], False)

    def test_the_generator_refuses_a_registered_plugin_without_hold(self):
        old = REPO / "lab" / "p1l" / "registered" / "patton_gate.py"  # patton-gate 0.5.0: no verify.hold
        with self.assertRaises(ValueError):
            prereg.registered_gate(old)

    def test_the_generator_refuses_a_registered_plugin_whose_prompt_was_not_measured(self):
        stale = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False)
        self.addCleanup(Path(stale.name).unlink)
        stale.write(Path(p1v_prompts.gate.__file__).read_text())  # patton-gate 0.4.0: its prompt has no ground
        stale.close()
        with self.assertRaises(ValueError):
            prereg.registered_gate(Path(stale.name))

    def test_the_generator_accepts_the_registered_copy(self):
        self.assertEqual(prereg.registered_gate().VERSION, "0.6.0")


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.text = prereg.PREREG_PATH.read_text(encoding="utf-8")

    def test_the_committed_preregistration_matches_the_code(self):
        self.assertEqual(prereg.check(), 0)

    def test_check_names_this_study(self):
        lines = []
        self.assertEqual(prereg.check(log=lines.append), 0)
        self.assertEqual(lines, ["P1H pre-registration matches the code"])

    def test_check_fails_when_the_file_is_missing_or_edited(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p1h.md"
            with mock.patch.object(prereg, "PREREG_PATH", path), mock.patch("sys.stdout", io.StringIO()):
                self.assertEqual(prereg.check(), 1)
                path.write_text(self.text + "\nedited\n")
                self.assertEqual(prereg.check(), 1)
                path.write_text(self.text)
                self.assertEqual(prereg.check(), 0)

    def test_every_frozen_input_is_hashed_in_the_document(self):
        for rel in prereg.FROZEN:
            self.assertIn(f"| `{rel}` | `{prereg.file_hash(rel)}` |", self.text, rel)

    def test_the_rules_of_p1l_are_frozen_too_because_the_constants_come_from_there(self):
        self.assertIn("lab/patton_lab/p1l/verdicts.py", prereg.FROZEN)

    def test_the_document_names_the_seven_harnesses_and_the_stamp_of_each(self):
        for harness in runner.SETS["p1h"]["harnesses"]:
            self.assertRegex(self.text, rf"\| [^|\n]*\b{re.escape(harness)}\b[^|\n]* \| `[0-9a-f]{{64}}` \|")

    def test_the_predictions_quote_the_registered_thresholds(self):
        for needle in (f"p < {verdicts.ALPHA}", f"{verdicts.SHARE_SUPPORT:.2f}", f"{verdicts.MARGIN:.2f}",
                       str(verdicts.MIN_PAIRS), "Q1", "Q2", "Q3", "Holm", "p02", "p03"):
            self.assertIn(needle, self.text, needle)

    def test_the_design_counts_match_the_set(self):
        cfg = runner.SETS["p1h"]
        runs = len(cfg["harnesses"]) * len(suites.SUITES["p1h"].arms) * len(cfg["fixtures"]) * cfg["reps"]
        self.assertEqual(runs, 336)
        self.assertIn(f"Runs: {runs}", self.text)
        self.assertIn(f"seed {cfg['seed']}", self.text)
        self.assertIn(f"${cfg['cap']:.2f}", self.text)

    def test_the_hold_arms_configuration_is_printed(self):
        config = suites.SUITES["p1h"].prepare("p02", "hold").config
        self.assertEqual((config["verify"]["hold"], config["verify"]["max_rounds"]), (True, 4))
        self.assertIn(prereg.json.dumps(config, sort_keys=True), self.text)

    def test_the_prompt_hash_is_given(self):
        self.assertIn(hashlib.sha256(p1v_prompts.ARMS["fitted-v2"].encode()).hexdigest(), self.text)

    def test_the_counts_from_p1l_are_derived_from_its_records(self):
        rows = runner.load("p1l")
        gamed = {arm: sum(bool(r["metrics"]["gamed"]) for r in rows if r["arm"] == arm) for arm in ("none", "stop")}
        self.assertIn(f"none {gamed['none']} of 168, stop {gamed['stop']} of 168", self.text)
        fitting = [r for r in rows if r["arm"] == "stop" and r["fixture"] in ("p01", "p04")]
        refused = [r for r in fitting if r["metrics"]["verifier_blocks"]]
        self.assertIn(f"the Verifier refused {len(refused)} of the\nstop arm's runs there, and "
                      f"{sum(bool(r['metrics']['gamed']) for r in refused)} of those still ended gamed", self.text)

    def test_the_document_is_dated_before_any_call_of_the_study(self):
        self.assertIn("before any call of this study", self.text)

    def test_the_bridge_cap_the_limits_name_is_the_one_in_the_bridge(self):
        from patton_lab.harness import bridge
        self.assertEqual(bridge.STOP_CAP, 4)
        self.assertIn("at most four times in all", self.text.replace("\n  ", " "))


class FleetCheckTests(unittest.TestCase):
    def test_the_fleet_runner_asks_this_module_for_its_check(self):
        self.assertEqual(runner.SETS["p1h"]["prereg"], "patton_lab.p1h.prereg")
        self.assertTrue(callable(prereg.check))


if __name__ == "__main__":
    unittest.main()
