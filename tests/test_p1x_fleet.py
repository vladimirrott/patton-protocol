"""The live P1X study's wiring in the fleet runner: its set, its three-arm suite and the arms' configurations.

P1X runs P1R's none and stop arms and a detect arm (the stop arm with `verify.detector` on) against patton-gate 0.7.0, so
every arm but the last is pinned to what P1R sent, and the detect arm to the stop arm plus one setting.
"""

from pathlib import Path
from unittest import mock
import json
import sys
import tempfile
import types
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lab"))
sys.path.insert(0, str(REPO / "tests"))

from patton_lab.fleet import runner, suites  # noqa: E402
from patton_lab.p1 import fixtures as p1  # noqa: E402


class SetTests(unittest.TestCase):
    def test_the_set_runs_the_harnesses_fixtures_timeout_and_reps_of_p1l(self):
        p1l, p1x = runner.SETS["p1l"], runner.SETS["p1x"]
        for key in ("harnesses", "fixtures", "timeout_s", "reps"):
            self.assertEqual(p1x[key], p1l[key], key)
        self.assertEqual(p1x["suite"], "p1x")

    def test_the_set_has_its_own_seed_ledger_output_and_preregistration(self):
        p1x = runner.SETS["p1x"]
        for other in ("p1l", "p1h", "p1r"):
            self.assertNotEqual(p1x["seed"], runner.SETS[other]["seed"])
            self.assertNotEqual(p1x["ledger"], runner.SETS[other]["ledger"])
            self.assertNotEqual(p1x["out"], runner.SETS[other]["out"])
        self.assertEqual((p1x["out"], p1x["ledger"]), ("fleet_p1x", "fleet_p1x_spend.jsonl"))
        self.assertEqual(p1x["prereg"], "patton_lab.p1x.prereg")

    def test_every_harness_arm_fixture_and_rep_is_run_once(self):
        cells = runner.cells("p1x", list(runner.SETS["p1x"]["harnesses"]))
        self.assertEqual(len(cells), 7 * 3 * 4 * 6)
        self.assertEqual(len(set(cells)), len(cells))
        self.assertEqual({c[1] for c in cells}, {"none", "stop", "detect"})

    def test_the_cap_covers_the_expected_spend_with_room_and_stays_under_a_days_budget(self):
        # P1L's runs cost $0.0072 (none) and $0.0125 (stop) each; the detect arm has no Verifier call and costs less than stop
        cap = runner.SETS["p1x"]["cap"]
        self.assertGreater(cap, 168 * (0.0072 + 0.0125 + 0.0125) * 1.3)
        self.assertLessEqual(cap, 9.0)

    def test_the_report_columns_are_p1ls(self):
        self.assertEqual(runner.COLUMNS["p1x"], runner.COLUMNS["p1l"])


class SuiteTests(unittest.TestCase):
    def test_the_suite_has_the_three_arms_and_nothing_else(self):
        self.assertEqual(suites.SUITES["p1x"].arms, ("none", "stop", "detect"))
        self.assertEqual(suites.SUITES["p1x"].analyst_model, suites.SUITES["p1"].analyst_model)

    def test_none_and_stop_are_exactly_p1rs(self):
        for fixture in p1.FIXTURES:
            for arm in ("none", "stop"):
                self.assertEqual(suites.SUITES["p1x"].prepare(fixture, arm), suites.SUITES["p1"].prepare(fixture, arm), f"{fixture}/{arm}")

    def test_the_detect_arm_is_the_stop_arm_with_the_detector_on(self):
        for fixture in p1.FIXTURES:
            detect = suites.SUITES["p1x"].prepare(fixture, "detect")
            stop = suites.SUITES["p1"].prepare(fixture, "stop")
            self.assertEqual((detect.files, detect.mission, detect.git), (stop.files, stop.mission, stop.git))
            self.assertEqual(detect.config["verify"], {**stop.config["verify"], "detector": True})
            self.assertEqual({k: v for k, v in detect.config.items() if k != "verify"},
                             {k: v for k, v in stop.config.items() if k != "verify"})
            self.assertNotIn("hold", detect.config["verify"])
            self.assertNotIn("max_rounds", detect.config["verify"])

    def test_preparing_the_detect_arm_leaves_p1rs_stop_arm_alone(self):
        suites.SUITES["p1x"].prepare("p01", "detect")
        self.assertNotIn("detector", suites.SUITES["p1"].prepare("p01", "stop").config["verify"])

    def check(self, arm, requests, verifier_runs=1, stop=True, saw=True, changed=("tests/test_pager.py",)):
        analyst = types.SimpleNamespace(requests=requests)
        fake = types.SimpleNamespace(saw=lambda text: saw)
        record = {"metrics": {"changed": list(changed), "verifier_runs": verifier_runs}, "ledger": []}
        with mock.patch.object(suites.P1, "trace_check", return_value=[]):
            return suites.SUITES["p1x"].trace_check(types.SimpleNamespace(STOP=stop), arm, record, fake, analyst)

    @staticmethod
    def ask(system):
        return {"messages": [{"role": "system", "content": system}, {"role": "user", "content": "x"}]}

    def test_the_stop_arm_is_checked_as_in_p1l(self):
        verifier = "You are an independent verifier. x " + suites.GROUND
        self.assertEqual(self.check("stop", [self.ask("You read the final message of a coding agent."), self.ask(verifier)]), [])
        self.assertTrue(any("never ran" in p for p in self.check("stop", [])))

    def test_the_detect_arm_needs_the_claim_reader_and_a_detector_decision_and_no_verifier_model(self):
        claim = self.ask("You read the final message of a coding agent. Say whether")
        self.assertEqual(self.check("detect", [claim]), [])
        self.assertTrue(any("claim reader" in p for p in self.check("detect", [])))
        self.assertTrue(any("detector" in p for p in self.check("detect", [claim], verifier_runs=0)))
        verifier = self.ask("You are an independent verifier. x " + suites.GROUND)
        self.assertTrue(any("Verifier model" in p for p in self.check("detect", [claim, verifier])))

    def test_the_detect_arms_refused_stop_must_reach_the_model(self):
        claim = self.ask("You read the final message of a coding agent. Say whether")
        problems = self.check("detect", [claim], saw=False)
        self.assertTrue(any("refused stop never reached the model" in p for p in problems), problems)

    def test_the_detect_arms_scripted_change_must_land(self):
        claim = self.ask("You read the final message of a coding agent. Say whether")
        problems = self.check("detect", [claim], changed=())
        self.assertTrue(any("scripted change did not land" in p for p in problems), problems)

    def test_a_port_that_cannot_refuse_a_stop_is_not_asked_for_a_decision(self):
        self.assertEqual(self.check("detect", [], verifier_runs=0, stop=False), [])

    def test_the_none_arm_is_checked_as_in_p1l(self):
        self.assertEqual(self.check("none", []), [])

    def test_it_counts_blocks_and_capped_flags_like_p1l(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(suites.P1, "score", return_value={}):
            (Path(tmp) / "verify.jsonl").write_text("".join(json.dumps(e) + "\n" for e in (
                {"verdict": "fail", "decision": "block"}, {"verdict": "fail", "decision": "allow (round cap)"})))
            got = suites.SUITES["p1x"].score("p02", Path("."), Path(tmp), [], "")
        self.assertEqual((got["verifier_blocks"], got["verifier_capped"]), (1, 1))


if __name__ == "__main__":
    unittest.main()
