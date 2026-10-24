"""The triage runner's guards in front of Groq, and its money arithmetic.

Every refusal test replaces the network call with a stub that fails loudly, so a broken guard fails the test
instead of spending.
"""

from pathlib import Path
from unittest import mock
import json
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lab"))

from patton_lab import ledgers  # noqa: E402
from patton_lab.triage import flags, runner  # noqa: E402


def _must_not_spend(*args, **kwargs):
    raise AssertionError("a guarded path reached the network")


class LabDir:
    def __init__(self, lab=0.0, gate=0.0, missions=0.0, triage=0.0):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        (self.path / "results").mkdir()
        (self.path / "results" / "spend.json").write_text(json.dumps({"spent": {"groq": lab}}))
        for name, usd in (("cc_spend.jsonl", gate), ("missions_spend.jsonl", missions), ("triage_spend.jsonl", triage)):
            (self.path / name).write_text(json.dumps({"id": "x", "usd": usd}) + "\n")

    def close(self):
        self.tmp.cleanup()


class MoneyTests(unittest.TestCase):
    def test_cost_counts_cached_tokens_at_the_cached_price(self):
        usage = {"prompt_tokens": 1000, "completion_tokens": 100, "prompt_tokens_details": {"cached_tokens": 400}}
        self.assertAlmostEqual(runner.usd("gpt-oss-120b", usage), (600 * 0.15 + 400 * 0.075 + 100 * 0.60) / 1e6)
        self.assertIsNone(runner.usd("gpt-oss-120b", {}))

    def test_every_ledger_counts_toward_the_lab_cap(self):
        lab = LabDir(4.65, 17.40, 2.15, 1.0)
        self.addCleanup(lab.close)
        self.assertAlmostEqual(sum(ledgers.spent_everywhere(lab.path).values()), 25.20)

    def test_the_lab_cap_refuses_a_call_that_could_pass_it(self):
        lab = LabDir(4.65, 17.40, 2.15, 15.79)
        self.addCleanup(lab.close)
        meter = runner.Meter(lab.path, lab.path / "triage_spend.jsonl", 100.0)
        with self.assertRaises(runner.SpendStop):
            meter.reserve(0.02)
        meter.reserve(0.0)

    def test_the_set_cap_counts_calls_in_flight(self):
        lab = LabDir()
        self.addCleanup(lab.close)
        meter = runner.Meter(lab.path, lab.path / "triage_spend.jsonl", 0.10)
        meter.reserve(0.06)
        with self.assertRaises(runner.SpendStop):
            meter.reserve(0.06)
        meter.settle(0.06, "a", 0.01, {"prompt_tokens": 1, "completion_tokens": 1})
        meter.reserve(0.06)

    def test_an_unreadable_ledger_refuses(self):
        lab = LabDir()
        self.addCleanup(lab.close)
        (lab.path / "cc_spend.jsonl").write_text("{not json\n")
        with self.assertRaises(ValueError):
            runner.Meter(lab.path, lab.path / "triage_spend.jsonl", 1.0).reserve(0.01)


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for patcher in (mock.patch.object(runner, "TRACE_DIR", Path(self.tmp.name)),
                        mock.patch.object(runner, "post", side_effect=_must_not_spend)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_paid_command_refuses_without_a_trace(self):
        self.assertEqual(runner.main(["dev"]), 2)

    def test_a_stale_trace_is_refused(self):
        (Path(self.tmp.name) / "TRACE_OK").write_text("old\n")
        self.assertEqual(runner.main(["dev"]), 2)

    def test_the_positive_control_reaches_the_network_stub(self):
        # With a valid trace and room in the budget, `dev` goes all the way to the network, so the refusals above
        # stand right in front of it.
        lab = LabDir()
        self.addCleanup(lab.close)
        (Path(self.tmp.name) / "TRACE_OK").write_text(runner.stamp() + "\n")
        with mock.patch.object(runner, "LAB", lab.path), \
                mock.patch.object(runner, "LEDGER", lab.path / "triage_spend.jsonl"):
            with self.assertRaises(AssertionError):
                runner.main(["dev"])
        self.assertEqual((lab.path / "triage_spend.jsonl").read_text().count("\n"), 1)

if __name__ == "__main__":
    unittest.main()
