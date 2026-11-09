"""P1X's record: every registered cell is stored once, the ledger holds what the records charge, each arm's hook did what its
arm says (the stop arm's Verifier carried the ground, the detect arm called no Verifier model, the none arm called nothing), and the
findings quote only numbers the report, the pre-registration or the facts below print."""

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
from patton_lab.p1v import cases as p1v_cases  # noqa: E402
from patton_lab.p1v import prompts as p1v_prompts  # noqa: E402
from patton_lab.p1x import prereg, report, verdicts  # noqa: E402
from test_fleet_study_integrity import faults, late_charges  # noqa: E402
from test_lab_study_integrity import unbacked_numbers  # noqa: E402

FINDINGS = REPO / "docs" / "findings" / "2026-11-09-p1x-findings.md"
OUT = runner.LAB / runner.SETS["p1x"]["out"]
LEDGER = runner.LAB / runner.SETS["p1x"]["ledger"]
CLAIM_HEAD = "You read the final message of a coding agent"


def cassettes(arm: str):
    return sorted(OUT.glob(f"*/{arm}-*.cassette.jsonl.gz"))


def calls(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def head(call) -> str:
    messages = (call.get("request") or {}).get("messages") or []
    return str(messages[0].get("content", "")) if messages else ""


@unittest.skipUnless(FINDINGS.exists(), "the P1X study has not been recorded yet")
class RecordTests(unittest.TestCase):
    def test_every_registered_cell_is_stored_once_and_reached_the_model(self):
        rows = runner.load("p1x")
        cells = {(r["harness"], r["arm"], r["fixture"], r["rep"]) for r in rows}
        self.assertEqual(cells, set(runner.cells("p1x", list(runner.SETS["p1x"]["harnesses"]))))
        self.assertEqual(len(rows), len(cells))
        self.assertEqual([r["run_id"] for r in rows if r["calls"] == 0], [])
        self.assertEqual([r["run_id"] for r in rows if r["usd"] == 0], [])

    def test_the_ledger_holds_what_the_records_charge_and_the_late_charges(self):
        spent = spend_all.ledger_sum(LEDGER)
        late = late_charges("p1x")
        self.assertAlmostEqual(spent, sum(r["usd"] for r in runner.load("p1x")) + sum(late.values()), places=6)
        self.assertLessEqual(spent, runner.SETS["p1x"]["cap"])

    def test_no_stored_request_was_refused_by_the_provider(self):
        self.assertEqual({k: v for k, v in faults("p1x").items() if k[1] == "refused"}, {})

    def test_the_lab_stayed_under_its_cap(self):
        self.assertLessEqual(sum(spend_all.spent_everywhere().values()), spend_all.GLOBAL_CAP)

    def test_every_runs_stamp_is_the_one_the_preregistration_froze(self):
        stamps = {r["stamp"] for r in runner.load("p1x")}
        self.assertEqual(len(stamps), 1, stamps)
        self.assertIn(stamps.pop(), prereg.PREREG_PATH.read_text(encoding="utf-8"))

    def test_the_preregistration_still_matches_the_code_and_the_records(self):
        self.assertEqual(prereg.check(log=lambda *_: None), 0)

    def test_every_verifier_call_of_the_stop_arm_read_the_prompt_with_the_ground(self):
        ground = hashlib.sha256(p1v_prompts.ARMS["fitted-v2"].encode()).hexdigest()
        seen = Counter()
        for path in cassettes("stop"):
            for call in p1v_cases._verifier_calls(path):
                seen[hashlib.sha256(head(call).encode()).hexdigest()] += 1
        self.assertEqual(set(seen), {ground})
        self.assertGreater(sum(seen.values()), 0)

    def test_the_detect_arm_called_the_claim_reader_and_never_the_verifier_model(self):
        readers = 0
        for path in cassettes("detect"):
            self.assertEqual(p1v_cases._verifier_calls(path), [], path.name)
            readers += sum(call.get("route") == "t1" and head(call).startswith(CLAIM_HEAD) for call in calls(path))
        self.assertGreater(readers, 0)

    def test_the_none_arm_never_called_anything_but_the_worker(self):
        for path in cassettes("none"):
            self.assertEqual({c["route"] for c in calls(path)} - {"worker"}, set(), path.name)

    def test_the_claim_reader_of_both_hooked_arms_is_the_one_the_p1r_runs_used(self):
        reader = hashlib.sha256(p1v_prompts.gate.CLAIMS_SYSTEM.encode()).hexdigest()
        seen = set()
        for arm in ("stop", "detect"):
            for path in cassettes(arm):
                seen |= {hashlib.sha256(head(c).encode()).hexdigest() for c in calls(path)
                         if c.get("route") == "t1" and head(c).startswith(CLAIM_HEAD)}
        self.assertEqual(seen, {reader})

    def test_the_detectors_refusals_reached_the_worker_with_the_way_out_in_words(self):
        said = plainly = 0
        for path in cassettes("detect"):
            for call in calls(path):
                if call.get("route") == "worker":
                    text = json.dumps((call.get("request") or {}).get("messages"))
                    said += "a deterministic check of this change" in text
                    plainly += "say plainly in your final message" in text
        blocks = sum(r["metrics"]["verifier_blocks"] for r in runner.load("p1x") if r["arm"] == "detect")
        self.assertEqual(said > 0, blocks > 0, (said, blocks))
        self.assertGreaterEqual(plainly, said)

    def test_the_none_arm_was_never_refused(self):
        self.assertEqual(sum(r["metrics"]["refused_stops"] for r in runner.load("p1x") if r["arm"] == "none"), 0)


@unittest.skipUnless(FINDINGS.exists(), "the P1X study has not been recorded yet")
class FindingsTests(unittest.TestCase):
    def test_the_findings_quote_the_registered_report(self):
        self.assertEqual(report.check(FINDINGS), [])

    def test_the_prose_quotes_only_backed_numbers(self):
        rows = runner.load("p1x")
        facts = f"${spend_all.ledger_sum(LEDGER):.2f} ${runner.SETS['p1x']['cap']:.2f} {len(rows)} runs"
        self.assertEqual(unbacked_numbers(FINDINGS.read_text(encoding="utf-8"), report.text(),
                                          prereg.PREREG_PATH.read_text(encoding="utf-8"), facts), [])

    def test_the_cost_in_the_findings_is_the_ledgers(self):
        self.assertIn(f"${spend_all.ledger_sum(LEDGER):.2f}", FINDINGS.read_text(encoding="utf-8"))

    def test_the_verdict_words_in_the_findings_are_the_reports(self):
        text = FINDINGS.read_text(encoding="utf-8")
        for key, (verdict, _) in verdicts.p1x(runner.load("p1x")).items():
            self.assertIn(f"{key}: **{verdict}**", text)


if __name__ == "__main__":
    unittest.main()
