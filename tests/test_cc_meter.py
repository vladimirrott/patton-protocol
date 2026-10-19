"""The spend meter: prior spend, new charges, the abort line, and the subscription run cap."""

from pathlib import Path
import json
import sys
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
LAB_ROOT = REPO_ROOT / "lab"
if str(LAB_ROOT) not in sys.path:
    sys.path.insert(0, str(LAB_ROOT))

from patton_lab.cc import meter  # noqa: E402

PRICE = (0.15, 0.60)


class MeterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        (self.dir / "spend.json").write_text(json.dumps({"version": 1, "spent": {"groq": 4.0, "openai": 0.5}, "calls": 10}))
        self.pilot = self.dir / "pilot"
        self.pilot.mkdir()
        (self.pilot / "a.json").write_text(json.dumps({"usage": {"input_tokens": 1_000_000, "cache_read_input_tokens": 1_000_000,
                                                                  "output_tokens": 500_000}}))
        (self.pilot / "b.json").write_text(json.dumps({"usage": None}))

    def meter(self, abort=14.0, cap=15.0):
        return meter.Meter(self.dir / "cc_spend.jsonl", self.dir / "spend.json", [self.pilot], cap=cap, abort=abort)

    def test_prior_spend_is_the_lab_ledger_plus_the_pilot_estimate(self):
        # 4.0 + 0.5 from the ledger; the pilot used (1M + 1M) x 0.15 + 0.5M x 0.60 = 0.6
        self.assertAlmostEqual(self.meter().prior(), 5.1, places=6)

    def test_a_charge_adds_to_the_total_and_persists(self):
        m = self.meter()
        cost = m.charge("run-1", 1_000_000, 100_000, PRICE)
        self.assertAlmostEqual(cost, 0.21, places=6)
        self.assertAlmostEqual(self.meter().total(), 5.1 + 0.21, places=6)

    def test_charging_the_same_record_twice_is_refused(self):
        m = self.meter()
        m.charge("run-1", 10, 10, PRICE)
        with self.assertRaises(ValueError):
            m.charge("run-1", 10, 10, PRICE)

    def test_a_negative_charge_is_refused(self):
        with self.assertRaises(ValueError):
            self.meter().charge("run-1", -1, 0, PRICE)

    def test_check_raises_at_the_abort_line(self):
        m = self.meter(abort=5.2)
        m.check()
        m.charge("run-1", 1_000_000, 0, PRICE)  # +0.15 -> 5.25
        with self.assertRaises(meter.CapExceeded):
            m.check()

    def test_check_with_a_bound_refuses_a_run_that_could_cross_the_line(self):
        m = self.meter(abort=5.2)
        m.check(bound=0.05)
        with self.assertRaises(meter.CapExceeded):
            m.check(bound=0.2)

    def test_an_unreadable_ledger_is_refused_not_guessed(self):
        (self.dir / "spend.json").write_text("{broken")
        with self.assertRaises(ValueError):
            self.meter().prior()

    def test_the_abort_line_cannot_pass_the_cap(self):
        with self.assertRaises(ValueError):
            self.meter(abort=16.0, cap=15.0)

    def test_the_real_ledger_and_pilot_give_the_figure_the_design_quotes(self):
        real = meter.Meter(self.dir / "x.jsonl", LAB_ROOT / "results" / "spend.json", [LAB_ROOT / "pilot" / "out",
                           LAB_ROOT / "pilot" / "first_pass", LAB_ROOT / "pilot" / "out_failed"])
        self.assertTrue(6.0 < real.prior() < 7.0, real.prior())


class RunCapTests(unittest.TestCase):
    def test_live_runs_count_only_the_named_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            for i, backend in enumerate(["anthropic", "anthropic", "groq"]):
                (Path(tmp) / f"r{i}.json").write_text(json.dumps({"backend": backend}))
            self.assertEqual(meter.live_runs(Path(tmp), "anthropic"), 2)
            meter.check_run_cap(Path(tmp), "anthropic", limit=3)
            with self.assertRaises(meter.CapExceeded):
                meter.check_run_cap(Path(tmp), "anthropic", limit=2)


if __name__ == "__main__":
    unittest.main()
