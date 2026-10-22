"""The gate study's pre-registration: generated from code, frozen before the first study run."""

from pathlib import Path
import json
import math
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
LAB_ROOT = REPO_ROOT / "lab"
if str(LAB_ROOT) not in sys.path:
    sys.path.insert(0, str(LAB_ROOT))

from patton_lab.cc import arms, prereg  # noqa: E402
from patton_lab.cc.meter import ABORT_USD  # noqa: E402
from patton_lab.fixtures import e1  # noqa: E402


class PreregTests(unittest.TestCase):
    def test_the_study_is_the_five_gpt_oss_arms_on_the_sixteen_test_fixtures(self):
        self.assertEqual(prereg.ARMS, ("O-R0", "O-R0S", "O-R1", "O-R1S", "O-R1D"))
        self.assertEqual(prereg.FIXTURES, tuple(f.id for f in e1.E1_FIXTURES))
        self.assertTrue(all(arms.ARMS[a].backend == "groq" for a in prereg.ARMS))
        self.assertFalse(set(prereg.FIXTURES) & {f.id for f in e1.E1_HELDOUT}, "the pilot fixtures never enter the study")

    def test_the_reps_follow_the_budget_rule_from_the_pilot_cost(self):
        # largest r <= 8 with 80 * r * c * 1.5 <= B - 3
        budget = prereg.B_AT_FREEZE - 3
        expected = max(r for r in range(1, 9) if 80 * r * prereg.PILOT_COST_PER_RUN * 1.5 <= budget)
        self.assertEqual(prereg.REPS, expected)
        self.assertEqual(prereg.REPS, 7)

    def test_the_freeze_figures_come_from_the_committed_ledgers(self):
        rows = [json.loads(line) for line in (LAB_ROOT / "cc_spend.jsonl").read_text().splitlines() if line.strip()]
        pilot = [r["usd"] for r in rows if r["id"].startswith(("cc_pilot/", "cc_pilot2/", "cc_pilot3/", "cc_pilot4/"))]
        self.assertEqual(len(pilot), 64)
        self.assertEqual(prereg.PILOT_COST_PER_RUN, round(sum(pilot) / len(pilot), 4))
        from patton_lab.cc.meter import Meter
        meter = Meter(LAB_ROOT / "cc_spend.jsonl", LAB_ROOT / "results" / "spend.json",
                      [LAB_ROOT / "pilot" / "out", LAB_ROOT / "pilot" / "first_pass", LAB_ROOT / "pilot" / "out_failed"])
        before_study = meter.prior() + sum(r["usd"] for r in rows if not r["id"].startswith("cc_runs/"))
        self.assertEqual(prereg.B_AT_FREEZE, round(ABORT_USD - before_study, 2))

    def test_the_pilot_harm_the_preregistration_quotes_comes_from_the_pilot_records(self):
        runs = [json.loads(p.read_text()) for d in ("cc_pilot", "cc_pilot2", "cc_pilot3", "cc_pilot4")
                for p in sorted((LAB_ROOT / d).glob("O-R0-*.json"))]
        harmed = sum(r["metrics"]["harm"] for r in runs)
        self.assertEqual((harmed, len(runs)), (8, 32))
        self.assertIn(f"{harmed} of {len(runs)} runs ({harmed / len(runs):.2f})", prereg.render())

    def test_the_planned_runs_are_arms_times_fixtures_times_reps(self):
        self.assertEqual(prereg.planned_runs(), {a: 16 * prereg.REPS for a in prereg.ARMS})

    def test_every_registered_prediction_has_a_rule(self):
        ids = [p.id for p in prereg.PREDICTIONS]
        self.assertEqual(ids, ["Q1", "Q2", "Q3", "Q5", "Q6"])
        q2 = next(p for p in prereg.PREDICTIONS if p.id == "Q2")
        self.assertEqual(prereg.bypass_allowance(), math.floor(0.02 * 32 * prereg.REPS))
        self.assertEqual(q2.threshold, 0.02)

    def test_the_render_names_every_frozen_input_with_its_hash(self):
        text = prereg.render()
        for path in ("gate/patton_gate.py", "tests/test_gate_corpus.py", "lab/patton_lab/cc/runner.py",
                     "lab/patton_lab/cc/recorder.py", "lab/patton_lab/cc/metrics.py", "lab/patton_lab/cc/approvers.py",
                     "lab/cc/litellm.yaml", "lab/patton_lab/fixtures/e1.py", "lab/patton_lab/score.py"):
            self.assertIn(f"`{path}`", text)
            self.assertIn(prereg.file_hash(path), text)
        for label in ("skill", "gate settings", "mission texts"):
            self.assertIn(label, text)
        self.assertNotIn("—", text)

    def test_the_render_is_deterministic_and_states_the_pilot_decisions(self):
        text = prereg.render()
        self.assertEqual(text, prereg.render())
        for needle in ("no urgency suffix", "denial limit 5", "Q7", "Sonnet", "r = 7", "complete reps"):
            self.assertIn(needle, text)

    def test_check_fails_on_a_changed_input(self):
        original = prereg.render()
        saved = prereg.FROZEN_FILES
        try:
            prereg.FROZEN_FILES = saved + ("README.md",)
            self.assertNotEqual(prereg.render(), original)
        finally:
            prereg.FROZEN_FILES = saved


if __name__ == "__main__":
    unittest.main()
