"""The live P1H study's wiring in the fleet runner: its set and its two-arm suite.

P1H runs the none arm and a hold arm against patton-gate 0.6.0. The hold arm is P1L's stop arm with two settings
added (`verify.hold` on, `verify.max_rounds` 4), so these tests pin everything else to P1L's and P1R's.
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
    def test_the_set_runs_the_harnesses_fixtures_and_timeout_of_p1l(self):
        p1l, p1h = runner.SETS["p1l"], runner.SETS["p1h"]
        for key in ("harnesses", "fixtures", "timeout_s", "reps"):
            self.assertEqual(p1h[key], p1l[key], key)
        self.assertEqual(p1h["suite"], "p1h")
        self.assertEqual(len(p1h["harnesses"]), 7)

    def test_the_set_has_its_own_seed_ledger_and_output_and_asks_for_its_own_preregistration(self):
        p1l, p1h = runner.SETS["p1l"], runner.SETS["p1h"]
        self.assertNotEqual(p1h["seed"], p1l["seed"])
        self.assertEqual((p1h["out"], p1h["ledger"]), ("fleet_p1h", "fleet_p1h_spend.jsonl"))
        self.assertEqual(p1h["prereg"], "patton_lab.p1h.prereg")
        self.assertNotEqual(p1h["ledger"], p1l["ledger"])
        self.assertNotEqual(p1h["out"], p1l["out"])

    def test_every_harness_arm_fixture_and_rep_is_run_once(self):
        cells = runner.cells("p1h", list(runner.SETS["p1h"]["harnesses"]))
        self.assertEqual(len(cells), 7 * 2 * 4 * 6)
        self.assertEqual(len(set(cells)), len(cells))
        self.assertEqual({c[1] for c in cells}, {"none", "hold"})

    def test_the_cap_covers_the_expected_spend_with_room_and_stays_far_under_the_lab_cap(self):
        # P1L's runs cost $0.0072 (none) and $0.0125 (stop) each; a hold arm adds Verifier rounds, so allow twice that
        cap = runner.SETS["p1h"]["cap"]
        self.assertGreater(cap, 168 * (0.0072 + 0.0125 * 2) * 1.2)
        self.assertLess(cap, 10.0)

    def test_the_report_adds_the_verifier_blocks_column(self):
        self.assertEqual(runner.COLUMNS["p1h"], runner.COLUMNS["p1l"])


class SuiteTests(unittest.TestCase):
    def test_the_suite_has_the_none_and_hold_arms_and_nothing_else(self):
        self.assertEqual(suites.SUITES["p1h"].arms, ("none", "hold"))
        self.assertEqual(suites.SUITES["p1h"].analyst_model, suites.SUITES["p1"].analyst_model)

    def test_the_none_arm_is_exactly_p1rs(self):
        for fixture in p1.FIXTURES:
            self.assertEqual(suites.SUITES["p1h"].prepare(fixture, "none"), suites.SUITES["p1"].prepare(fixture, "none"))

    def test_the_hold_arm_is_p1rs_stop_arm_with_hold_on_and_four_rounds(self):
        for fixture in p1.FIXTURES:
            hold = suites.SUITES["p1h"].prepare(fixture, "hold")
            stop = suites.SUITES["p1"].prepare(fixture, "stop")
            self.assertEqual((hold.files, hold.mission, hold.git), (stop.files, stop.mission, stop.git))
            self.assertEqual(hold.config["verify"], {**stop.config["verify"], "hold": True, "max_rounds": 4})
            self.assertEqual({k: v for k, v in hold.config.items() if k != "verify"},
                             {k: v for k, v in stop.config.items() if k != "verify"})

    def test_preparing_the_hold_arm_leaves_p1rs_stop_arm_alone(self):
        suites.SUITES["p1h"].prepare("p01", "hold")
        self.assertNotIn("hold", suites.SUITES["p1"].prepare("p01", "stop").config["verify"])

    def test_the_hold_arm_is_asked_for_a_verifier_that_carries_the_ground(self):
        def check(arm, requests):
            analyst = types.SimpleNamespace(requests=requests)
            with mock.patch.object(suites.P1, "trace_check", return_value=[]):
                return suites.SUITES["p1h"].trace_check(types.SimpleNamespace(STOP=True), arm, {}, None, analyst)
        verifier = "You are an independent verifier. x " + suites.GROUND
        ask = lambda s: {"messages": [{"role": "system", "content": s}, {"role": "user", "content": "x"}]}  # noqa: E731
        self.assertEqual(check("hold", [ask(verifier)]), [])
        self.assertTrue(any("never ran" in p for p in check("hold", [])))
        self.assertTrue(any("lacks" in p for p in check("hold", [ask("You are an independent verifier. x")])))
        self.assertEqual(check("none", []), [])

    def test_the_base_checks_see_the_hold_arm_as_a_stop_arm(self):
        seen = []
        with mock.patch.object(suites.P1, "trace_check", side_effect=lambda m, arm, *a: seen.append(arm) or []):
            suites.SUITES["p1h"].trace_check(types.SimpleNamespace(STOP=False), "hold", {}, None, types.SimpleNamespace(requests=[]))
            suites.SUITES["p1h"].trace_check(types.SimpleNamespace(STOP=False), "none", {}, None, types.SimpleNamespace(requests=[]))
        self.assertEqual(seen, ["stop", "none"])

    def test_it_counts_blocks_and_capped_flags_like_p1l(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(suites.P1, "score", return_value={}):
            (Path(tmp) / "verify.jsonl").write_text("".join(json.dumps(e) + "\n" for e in (
                {"verdict": "fail", "decision": "block"}, {"verdict": "fail", "decision": "allow (round cap)"})))
            got = suites.SUITES["p1h"].score("p02", Path("."), Path(tmp), [], "")
        self.assertEqual((got["verifier_blocks"], got["verifier_capped"]), (1, 1))


if __name__ == "__main__":
    unittest.main()
