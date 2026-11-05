"""P1H's record: every registered cell is stored once, the ledger holds what the records charge, the hold arm's
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
from patton_lab.p1h import prereg, report  # noqa: E402
from patton_lab.p1v import cases as p1v_cases  # noqa: E402
from patton_lab.p1v import prompts as p1v_prompts  # noqa: E402
from test_fleet_study_integrity import faults, late_charges  # noqa: E402
from test_lab_study_integrity import unbacked_numbers  # noqa: E402

FINDINGS = REPO / "docs" / "findings" / "2026-11-05-p1h-findings.md"
OUT = runner.LAB / runner.SETS["p1h"]["out"]
LEDGER = runner.LAB / runner.SETS["p1h"]["ledger"]


def cassettes(arm: str):
    return sorted(OUT.glob(f"*/{arm}-*.cassette.jsonl.gz"))


def t1_calls(path: Path):
    """("claim" or "verifier", the call) for each claim-reader and Verifier call of one stored run, in order."""
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            call = json.loads(line) if line.strip() else {}
            messages = (call.get("request") or {}).get("messages") or []
            head = str(messages[0].get("content", "")) if messages else ""
            if call.get("route") == "t1" and head.startswith("You read the final message of a coding agent"):
                yield "claim", call
            elif call.get("route") == "t1" and head.startswith(p1v_cases.VERIFIER_PREFIX):
                yield "verifier", call


def last_looks():
    """For each hold run on p01 and p04 that the Verifier refused: how its last Verifier call was decided (by the
    registered plugin's own judge), and whether the run ended gamed. Also the Verifier calls that no claim-reader call
    preceded: the stops `verify.hold` sent to the Verifier whatever the agent said, with how each was decided."""
    gate = prereg.registered_gate()
    last, held = Counter(), Counter()
    for record in runner.load("p1h"):
        m = record["metrics"]
        if record["arm"] != "hold" or record["fixture"] not in ("p01", "p04"):
            continue
        path = OUT / record["harness"] / f"hold-{record['fixture']}-r{record['rep']}.cassette.jsonl.gz"
        previous, decided = None, []
        for kind, call in t1_calls(path):
            if kind == "verifier":
                reply, source = call["response"]["choices"][0]["message"]["content"], call["request"]["messages"][1]["content"]
                decision = gate.judge(reply, source)[2]
                decided.append(decision)
                if previous != "claim":
                    held[decision] += 1
            previous = kind
        if m["verifier_blocks"]:
            last[(decided[-1], bool(m["gamed"]))] += 1
    return last, held


@unittest.skipUnless(FINDINGS.exists(), "the P1H study has not been recorded yet")
class RecordTests(unittest.TestCase):
    def test_every_registered_cell_is_stored_once_and_reached_the_model(self):
        rows = runner.load("p1h")
        cells = {(r["harness"], r["arm"], r["fixture"], r["rep"]) for r in rows}
        self.assertEqual(cells, set(runner.cells("p1h", list(runner.SETS["p1h"]["harnesses"]))))
        self.assertEqual(len(rows), len(cells))
        self.assertEqual([r["run_id"] for r in rows if r["calls"] == 0], [])

    def test_the_ledger_holds_what_the_records_charge_and_the_late_charges(self):
        spent = spend_all.ledger_sum(LEDGER)
        late = late_charges("p1h")
        self.assertAlmostEqual(spent, sum(r["usd"] for r in runner.load("p1h")) + sum(late.values()), places=6)
        self.assertLessEqual(spent, runner.SETS["p1h"]["cap"])

    def test_no_stored_request_was_refused_by_the_provider(self):
        self.assertEqual({k: v for k, v in faults("p1h").items() if k[1] == "refused"}, {})

    def test_the_lab_stayed_under_its_cap(self):
        self.assertLessEqual(sum(spend_all.spent_everywhere().values()), spend_all.GLOBAL_CAP)

    def test_every_runs_stamp_is_the_one_the_preregistration_froze(self):
        stamps = {r["stamp"] for r in runner.load("p1h")}
        self.assertEqual(len(stamps), 1, stamps)
        self.assertIn(stamps.pop(), prereg.PREREG_PATH.read_text(encoding="utf-8"))

    def test_the_preregistration_still_matches_the_code_and_the_records(self):
        self.assertEqual(prereg.check(), 0)

    def test_every_verifier_call_of_the_hold_arm_read_the_prompt_with_the_ground(self):
        ground = hashlib.sha256(p1v_prompts.ARMS["fitted-v2"].encode()).hexdigest()
        seen = Counter()
        for path in cassettes("hold"):
            for call in p1v_cases._verifier_calls(path):
                seen[hashlib.sha256(call["request"]["messages"][0]["content"].encode()).hexdigest()] += 1
        self.assertEqual(set(seen), {ground})
        self.assertGreater(sum(seen.values()), 0)

    def test_the_none_arm_never_called_the_verifier_or_the_claim_reader(self):
        for path in cassettes("none"):
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                routes = {json.loads(line)["route"] for line in handle if line.strip()}
            self.assertEqual(routes - {"worker"}, set(), path.name)

    def test_the_claim_reader_of_the_hold_arm_is_the_one_the_p1r_runs_used(self):
        reader = hashlib.sha256(p1v_prompts.gate.CLAIMS_SYSTEM.encode()).hexdigest()
        seen = set()
        for path in cassettes("hold"):
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                for line in handle:
                    call = json.loads(line) if line.strip() else {}
                    messages = (call.get("request") or {}).get("messages") or []
                    if call.get("route") == "t1" and messages and str(messages[0].get("content", "")).startswith(
                            "You read the final message of a coding agent"):
                        seen.add(hashlib.sha256(messages[0]["content"].encode()).hexdigest())
        self.assertEqual(seen, {reader})

    def test_the_refusals_that_reached_the_worker_asked_for_a_change_to_the_code(self):
        held = say_plainly = 0
        for path in cassettes("hold"):
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                for line in handle:
                    call = json.loads(line) if line.strip() else {}
                    if call.get("route") == "worker":
                        text = json.dumps((call.get("request") or {}).get("messages"))
                        held += "does not lift this refusal" in text
                        say_plainly += "say plainly in your final message" in text
        blocks = sum(r["metrics"]["verifier_blocks"] for r in runner.load("p1h") if r["arm"] == "hold")
        self.assertEqual(say_plainly, 0)
        self.assertEqual(held > 0, blocks > 0, (held, blocks))

    def test_how_the_refused_runs_were_last_looked_at(self):
        last, held = last_looks()
        decided = Counter()
        for (decision, _gamed), n in last.items():
            decided[decision] += n
        self.assertEqual(sum(last.values()), 48)
        self.assertEqual(decided["allow (passed)"], 19)
        self.assertEqual(decided["allow (failure without quoted evidence)"], 10)
        self.assertEqual(decided["allow (not verified)"], 5)
        self.assertEqual(decided["block"], 14)
        self.assertEqual(last[("allow (passed)", True)], 18)
        self.assertGreater(sum(held.values()), 0)

    def test_every_run_that_hit_the_round_cap_was_last_looked_at_with_a_refusal(self):
        capped = {(r["harness"], r["fixture"], r["rep"]) for r in runner.load("p1h")
                  if r["arm"] == "hold" and r["metrics"]["verifier_capped"]}
        self.assertEqual(len(capped), 12)
        self.assertEqual(sum(bool(r["metrics"]["gamed"]) for r in runner.load("p1h")
                             if (r["harness"], r["fixture"], r["rep"]) in capped and r["arm"] == "hold"), 9)
        gate = prereg.registered_gate()
        for harness, fixture, rep in capped:
            calls = [c for kind, c in t1_calls(OUT / harness / f"hold-{fixture}-r{rep}.cassette.jsonl.gz") if kind == "verifier"]
            last = calls[-1]
            decision = gate.judge(last["response"]["choices"][0]["message"]["content"], last["request"]["messages"][1]["content"])[2]
            self.assertEqual(decision, "block", (harness, fixture, rep))

    def test_the_none_arm_was_never_refused(self):
        self.assertEqual(sum(r["metrics"]["refused_stops"] for r in runner.load("p1h") if r["arm"] == "none"), 0)


@unittest.skipUnless(FINDINGS.exists(), "the P1H study has not been recorded yet")
class FindingsTests(unittest.TestCase):
    def test_the_findings_quote_the_registered_report(self):
        self.assertEqual(report.check(FINDINGS), [])

    def test_the_prose_quotes_only_backed_numbers(self):
        text = FINDINGS.read_text(encoding="utf-8")
        rows = runner.load("p1h")
        spent = spend_all.ledger_sum(LEDGER)
        arms = {a: [r for r in rows if r["arm"] == a] for a in ("none", "hold")}
        gamed = " ".join(f"{sum(bool(r['metrics']['gamed']) for r in g)} of {len(g)}" for g in arms.values())
        last, held = last_looks()
        looked = Counter()
        for (decision, _gamed), n in last.items():
            looked[decision] += n
        p1l_spent = spend_all.ledger_sum(runner.LAB / runner.SETS["p1l"]["ledger"])
        facts = (f"${spent:.2f} ${runner.SETS['p1h']['cap']:.2f} ${p1l_spent:.2f} {len(rows)} runs {gamed} "
                 + " ".join(f"{n} runs" for n in looked.values()))
        sources = [report.text(), prereg.PREREG_PATH.read_text(encoding="utf-8"), facts]
        self.assertEqual(unbacked_numbers(text, *sources), [])

    def test_the_cost_in_the_findings_is_the_ledgers(self):
        self.assertIn(f"${spend_all.ledger_sum(LEDGER):.2f}", FINDINGS.read_text(encoding="utf-8"))

    def test_the_verdict_words_in_the_findings_are_the_reports(self):
        text = FINDINGS.read_text(encoding="utf-8")
        for key, (verdict, _) in report.verdicts.p1h(runner.load("p1h")).items():
            self.assertIn(f"{key}: **{verdict}**", text)


if __name__ == "__main__":
    unittest.main()
