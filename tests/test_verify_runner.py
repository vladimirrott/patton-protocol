"""The Verifier replay's guards in front of Groq, and its use of the plugin's own rules.

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

from patton_lab import spend  # noqa: E402
from patton_lab.verify import cases, runner  # noqa: E402


def _must_not_spend(*args, **kwargs):
    raise AssertionError("a guarded path reached the network")


class LabDir:
    def __init__(self, **usd):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        (self.path / "results").mkdir()
        (self.path / "results" / "spend.json").write_text(json.dumps({"spent": {"groq": usd.get("lab", 0.0)}}))
        for name, file in spend.LINE_LEDGERS.items():
            (self.path / file).write_text(json.dumps({"id": "x", "usd": usd.get(name.replace(" ", "_"), 0.0)}) + "\n")


class MoneyTests(unittest.TestCase):
    def test_the_verify_ledger_counts_toward_the_lab_cap(self):
        lab = LabDir(lab=10.0, verify=spend.GLOBAL_CAP - 10.01)
        self.addCleanup(lab.tmp.cleanup)
        self.assertAlmostEqual(sum(spend.spent_everywhere(lab.path).values()), spend.GLOBAL_CAP - 0.01)
        with self.assertRaises(runner.SpendStop):
            runner.Meter(lab.path, lab.path / "verify_spend.jsonl", 100.0).reserve(0.02)

    def test_the_set_cap_counts_this_ledger(self):
        lab = LabDir(verify=0.45)
        self.addCleanup(lab.tmp.cleanup)
        with self.assertRaises(runner.SpendStop):
            runner.Meter(lab.path, lab.path / "verify_spend.jsonl", 0.50).reserve(0.06)
        runner.Meter(lab.path, lab.path / "verify_spend.jsonl", 0.50).reserve(0.04)


class RuleTests(unittest.TestCase):
    def test_the_replay_decides_with_the_plugins_rules(self):
        case = next(c for c in cases.collect(cases.DEV_SOURCES) if c.kind == "harmful")
        source = cases.verifier_text(case)
        quote = next(line for line in source.splitlines() if "`" in line and len(line) > 30)
        fail = json.dumps({"verdict": "fail", "findings": [{"problem": "lost", "evidence": quote}]})
        claim = json.dumps({"claims_done": True})
        self.assertEqual(runner.decide(claim, fail, source)["decision"], "block")
        self.assertEqual(runner.decide(json.dumps({"claims_done": False}), fail, source)["decision"],
                         "allow (no completion claimed)")
        self.assertEqual(runner.decide(json.dumps({"claims_done": False}), fail, source)["verifier_decision"], "block")
        self.assertEqual(runner.decide(claim, "garbage", source)["decision"], "allow (not verified)")

    def test_the_verifier_input_never_holds_the_final_message(self):
        for case in cases.collect(cases.DEV_SOURCES):
            if len(case.final) > 40:
                self.assertNotIn(case.final, cases.verifier_text(case), case.id)
                self.assertIn(case.final.strip(), cases.claims_text(case), case.id)


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for patcher in (mock.patch.object(runner, "TRACE_DIR", Path(self.tmp.name)),
                        mock.patch.object(runner.triage, "post", side_effect=_must_not_spend)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_paid_command_refuses_without_a_trace(self):
        self.assertEqual(runner.main(["dev"]), 2)

    def test_a_stale_trace_is_refused(self):
        (Path(self.tmp.name) / "TRACE_OK").write_text("old\n")
        self.assertEqual(runner.main(["dev"]), 2)

    def test_the_study_refuses_without_a_matching_preregistration(self):
        (Path(self.tmp.name) / "TRACE_OK").write_text(runner.stamp() + "\n")
        from patton_lab.verify import prereg
        with mock.patch.object(prereg, "check", return_value=1):
            self.assertEqual(runner.main(["study"]), 2)

    def test_the_positive_control_reaches_the_network_stub(self):
        lab = LabDir()
        self.addCleanup(lab.tmp.cleanup)
        (Path(self.tmp.name) / "TRACE_OK").write_text(runner.stamp() + "\n")
        with mock.patch.object(runner, "LAB", lab.path), mock.patch.object(runner, "LEDGER", lab.path / "v.jsonl"):
            with self.assertRaises(AssertionError):
                runner.main(["dev"])


if __name__ == "__main__":
    unittest.main()
