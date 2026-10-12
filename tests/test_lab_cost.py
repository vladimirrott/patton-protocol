"""Tests for lab/patton_lab/cost.py: prices, bounds, and the persistent spend meter."""

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

LAB_ROOT = Path(__file__).resolve().parents[1] / "lab"
if str(LAB_ROOT) not in sys.path:
    sys.path.insert(0, str(LAB_ROOT))

from patton_lab import cost  # noqa: E402


class PriceTests(unittest.TestCase):
    def test_known_prices(self):
        self.assertEqual(cost.price("groq", "openai/gpt-oss-120b"), (0.15, 0.60))
        self.assertEqual(cost.price("openai", "gpt-4.1-mini"), (0.40, 1.60))

    def test_unknown_model_raises(self):
        with self.assertRaises(KeyError):
            cost.price("groq", "openai/gpt-oss-20b")

    def test_usd(self):
        # 1M in at $0.15 + 0.5M out at $0.60 = 0.15 + 0.30
        self.assertAlmostEqual(cost.usd(1_000_000, 500_000, (0.15, 0.60)), 0.45)

    def test_estimate_tokens_rounds_up(self):
        self.assertEqual(cost.estimate_tokens("abcd"), 2)  # '"abcd"' is 6 bytes -> ceil(6/4)
        self.assertEqual(cost.estimate_tokens(""), 1)  # '""' is 2 bytes -> 1


class RunBoundTests(unittest.TestCase):
    def test_matches_the_spec_closed_form(self):
        s = 3590
        expected_in = sum(s + 800 + 400 * i for i in range(6))
        tokens_in, tokens_out = cost.run_tokens(first_call_tokens=s + 800, turns=6, growth=400, out_per_turn=600)
        self.assertEqual(tokens_in, expected_in)
        self.assertEqual(tokens_out, 3600)


class MeterTests(unittest.TestCase):
    def test_authorize_refuses_past_the_abort_line(self):
        with TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "spend.json"
            ledger.write_text('{"version": 1, "spent": {"groq": 8.99}, "calls": 1}', encoding="utf-8")
            meter = cost.Meter(ledger, abort_at_usd=9.0)
            meter.authorize(0.01)
            with self.assertRaises(cost.BudgetExceeded):
                meter.authorize(0.02)

    def test_abort_line_cannot_exceed_the_cap(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                cost.Meter(Path(tmp) / "spend.json", abort_at_usd=10.5, cap_usd=10.0)

    def test_charge_persists_across_instances(self):
        with TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "spend.json"
            first = cost.Meter(ledger)
            charged = first.charge("groq", "openai/gpt-oss-120b", {"prompt_tokens": 1_000_000, "completion_tokens": 0})
            self.assertAlmostEqual(charged, 0.15)
            second = cost.Meter(ledger)
            self.assertAlmostEqual(second.spent(), 0.15)
            self.assertAlmostEqual(second.spent("groq"), 0.15)
            self.assertAlmostEqual(second.spent("openai"), 0.0)

    def test_output_tokens_are_charged(self):
        with TemporaryDirectory() as tmp:
            meter = cost.Meter(Path(tmp) / "spend.json")
            charged = meter.charge("groq", "openai/gpt-oss-120b", {"prompt_tokens": 0, "completion_tokens": 1_000_000})
            self.assertAlmostEqual(charged, 0.60)
            self.assertAlmostEqual(meter.spent(), 0.60)

    def test_cached_tokens_are_charged_at_full_price(self):
        with TemporaryDirectory() as tmp:
            meter = cost.Meter(Path(tmp) / "spend.json")
            usage = {
                "prompt_tokens": 1_000_000,
                "completion_tokens": 0,
                "prompt_tokens_details": {"cached_tokens": 900_000},
            }
            self.assertAlmostEqual(meter.charge("openai", "gpt-4.1-mini", usage), 0.40)

    def test_charge_rejects_usage_without_token_counts(self):
        with TemporaryDirectory() as tmp:
            meter = cost.Meter(Path(tmp) / "spend.json")
            with self.assertRaises(ValueError):
                meter.charge("groq", "openai/gpt-oss-120b", {})

    def test_corrupt_ledger_refuses(self):
        with TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "spend.json"
            ledger.write_text("{not json", encoding="utf-8")
            with self.assertRaises(ValueError):
                cost.Meter(ledger)


if __name__ == "__main__":
    unittest.main()
