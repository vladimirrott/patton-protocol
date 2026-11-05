"""P1L's record: every registered cell is stored once, the ledger holds what the records charge, the stop arm's
Verifier really carried the ground, and the findings quote only numbers the report, the pre-registration or the
facts below print."""

from collections import Counter
from pathlib import Path
import gzip
import hashlib
import json
import sys
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lab"))
sys.path.insert(0, str(REPO / "tests"))

from patton_lab import spend_all  # noqa: E402
from patton_lab.fleet import runner  # noqa: E402
from patton_lab.p1l import prereg, report  # noqa: E402
from patton_lab.p1v import cases as p1v_cases  # noqa: E402
from patton_lab.p1v import prompts as p1v_prompts  # noqa: E402
from test_fleet_study_integrity import faults, late_charges  # noqa: E402
from test_lab_study_integrity import unbacked_numbers  # noqa: E402

FINDINGS = REPO / "docs" / "findings" / "2026-11-04-p1l-findings.md"
OUT = runner.LAB / runner.SETS["p1l"]["out"]
LEDGER = runner.LAB / runner.SETS["p1l"]["ledger"]


def cassettes(arm: str):
    return sorted(OUT.glob(f"*/{arm}-*.cassette.jsonl.gz"))


@unittest.skipUnless(FINDINGS.exists(), "the P1L study has not been recorded yet")
class RecordTests(unittest.TestCase):
    def test_every_registered_cell_is_stored_once_and_reached_the_model(self):
        rows = runner.load("p1l")
        cells = {(r["harness"], r["arm"], r["fixture"], r["rep"]) for r in rows}
        self.assertEqual(cells, set(runner.cells("p1l", list(runner.SETS["p1l"]["harnesses"]))))
        self.assertEqual(len(rows), len(cells))
        self.assertEqual([r["run_id"] for r in rows if r["calls"] == 0], [])

    def test_the_ledger_holds_what_the_records_charge_and_the_late_charges(self):
        spent = spend_all.ledger_sum(LEDGER)
        late = late_charges("p1l")
        self.assertAlmostEqual(spent, sum(r["usd"] for r in runner.load("p1l")) + sum(late.values()), places=6)
        self.assertLessEqual(spent, runner.SETS["p1l"]["cap"])

    def test_no_stored_request_was_refused_by_the_provider(self):
        self.assertEqual({k: v for k, v in faults("p1l").items() if k[1] == "refused"}, {})

    def test_the_lab_stayed_under_its_cap(self):
        self.assertLessEqual(sum(spend_all.spent_everywhere().values()), spend_all.GLOBAL_CAP)

    def test_every_runs_stamp_is_the_one_the_preregistration_froze(self):
        stamps = {r["stamp"] for r in runner.load("p1l")}
        self.assertEqual(len(stamps), 1, stamps)
        self.assertIn(stamps.pop(), prereg.PREREG_PATH.read_text(encoding="utf-8"))

    def test_the_preregistration_still_matches_the_code_and_the_records(self):
        self.assertEqual(prereg.check(), 0)

    def test_every_verifier_call_of_the_stop_arm_read_the_prompt_with_the_ground(self):
        ground = hashlib.sha256(p1v_prompts.ARMS["fitted-v2"].encode()).hexdigest()
        seen = Counter()
        for path in cassettes("stop"):
            for call in p1v_cases._verifier_calls(path):
                seen[hashlib.sha256(call["request"]["messages"][0]["content"].encode()).hexdigest()] += 1
        self.assertEqual(set(seen), {ground})
        self.assertGreater(sum(seen.values()), 0)

    def test_the_none_arm_never_called_the_verifier_or_the_claim_reader(self):
        for path in cassettes("none"):
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                routes = {json.loads(line)["route"] for line in handle if line.strip()}
            self.assertEqual(routes - {"worker"}, set(), path.name)

    def test_the_claim_reader_of_the_stop_arm_is_the_one_the_p1r_runs_used(self):
        reader = hashlib.sha256(p1v_prompts.gate.CLAIMS_SYSTEM.encode()).hexdigest()
        seen = set()
        for path in cassettes("stop"):
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                for line in handle:
                    call = json.loads(line) if line.strip() else {}
                    messages = (call.get("request") or {}).get("messages") or []
                    if call.get("route") == "t1" and messages and str(messages[0].get("content", "")).startswith(
                            "You read the final message of a coding agent"):
                        seen.add(hashlib.sha256(messages[0]["content"].encode()).hexdigest())
        self.assertEqual(seen, {reader})


@unittest.skipUnless(FINDINGS.exists(), "the P1L study has not been recorded yet")
class FindingsTests(unittest.TestCase):
    def test_the_findings_quote_the_registered_report(self):
        self.assertEqual(report.check(FINDINGS), [])

    def test_the_prose_quotes_only_backed_numbers(self):
        text = FINDINGS.read_text(encoding="utf-8")
        rows = runner.load("p1l")
        spent = spend_all.ledger_sum(LEDGER)
        arms = {a: [r for r in rows if r["arm"] == a] for a in ("none", "stop")}
        gamed = " ".join(f"{sum(bool(r['metrics']['gamed']) for r in g)} of {len(g)}" for g in arms.values())
        facts = f"${spent:.2f} ${runner.SETS['p1l']['cap']:.2f} {len(rows)} runs {gamed}"
        sources = [report.text(), prereg.PREREG_PATH.read_text(encoding="utf-8"), facts]
        self.assertEqual(unbacked_numbers(text, *sources), [])

    def test_the_cost_in_the_findings_is_the_ledgers(self):
        self.assertIn(f"${spend_all.ledger_sum(LEDGER):.2f}", FINDINGS.read_text(encoding="utf-8"))

    def test_the_verdict_words_in_the_findings_are_the_reports(self):
        text = FINDINGS.read_text(encoding="utf-8")
        for key, (verdict, _) in report.verdicts.p1l(runner.load("p1l")).items():
            self.assertIn(f"{key}: **{verdict}**", text)


if __name__ == "__main__":
    unittest.main()
