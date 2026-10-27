"""The registered fleet studies' records, ledgers and findings agree with one another.

Each finished study must store every registered cell once, with no run that never reached the model, and its
findings must quote the report and post hoc blocks the records give, and only numbers those blocks, the
pre-registration or the facts below print.
"""

from collections import Counter
from pathlib import Path
import gzip
import json
import sys
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lab"))
sys.path.insert(0, str(REPO / "tests"))

from patton_lab import ledgers  # noqa: E402
from patton_lab.fleet import recorder, runner  # noqa: E402
from patton_lab.fleetstudy import posthoc, prereg, report  # noqa: E402
from test_lab_study_integrity import unbacked_numbers  # noqa: E402

FINDINGS = {"h1r": REPO / "docs" / "findings" / "2026-10-26-fleet-h1r-findings.md",
            "p1r": REPO / "docs" / "findings" / "2026-10-26-fleet-p1r-findings.md"}


def late_charges(which):
    """Ledger lines for calls a stored cassette shows with no attempt: the run ended while the call was in flight,
    and the reply was charged after the record was written."""
    out = {}
    lines = [json.loads(line) for line in (runner.LAB / runner.SETS[which]["ledger"]).read_text().splitlines()]
    for path in (runner.LAB / runner.SETS[which]["out"]).glob("*/*.cassette.jsonl.gz"):
        record = json.loads(path.with_name(path.name.replace(".cassette.jsonl.gz", ".json")).read_text())
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                call = json.loads(line)
                if not call["attempts"]:
                    prefix = f"{record['run_id']}/c{call['i']}/"
                    out[prefix] = sum(entry["usd"] for entry in lines if entry["id"].startswith(prefix))
    return out


def faults(which):
    counts = Counter()
    for path in (runner.LAB / runner.SETS[which]["out"]).glob("*/*.cassette.jsonl.gz"):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                call = json.loads(line)
                if call["status"] != 200 and call["attempts"]:  # no attempt: a late charge, counted apart
                    error = call["attempts"][-1]["error"]
                    kind = "model" if any(m in error for m in recorder.MODEL_FAULTS) else "refused"
                    counts[(path.parent.name, kind)] += 1
    return counts


class StudyRecordTests(unittest.TestCase):
    def test_every_registered_cell_is_stored_once_and_reached_the_model(self):
        for which in FINDINGS:
            rows = runner.load(which)
            cells = {(r["harness"], r["arm"], r["fixture"], r["rep"]) for r in rows}
            self.assertEqual(cells, set(runner.cells(which, list(runner.SETS[which]["harnesses"]))))
            self.assertEqual(len(rows), len(cells))
            self.assertEqual([r["run_id"] for r in rows if r["calls"] == 0], [])

    def test_the_ledger_holds_what_the_records_charge_and_the_late_charges_named(self):
        for which in FINDINGS:
            spent = ledgers.ledger_sum(runner.LAB / runner.SETS[which]["ledger"])
            late = late_charges(which)
            self.assertAlmostEqual(spent, sum(r["usd"] for r in runner.load(which)) + sum(late.values()), places=6)
        self.assertEqual(late_charges("h1r"), {})
        self.assertEqual({k: round(v, 6) for k, v in late_charges("p1r").items()},
                         {"open-interpreter/stop/p04-r2/c4/": 0.000502})

    def test_no_stored_request_was_refused_by_the_provider(self):
        for which in FINDINGS:
            self.assertEqual({k: v for k, v in faults(which).items() if k[1] == "refused"}, {})

    def test_the_set_aside_runs_never_reached_the_model(self):
        aside = [json.loads(p.read_text()) for p in (runner.LAB / "fleet_h1r_refused").glob("*/*.json")]
        self.assertEqual(len(aside), 370)
        self.assertTrue(all(r["calls"] == 0 and "create keyring" in r["stderr_tail"] for r in aside))
        aside = [json.loads(p.read_text()) for p in (runner.LAB / "fleet_p1r_refused").glob("*/*.json")]
        self.assertEqual([(r["run_id"], r["calls"]) for r in aside], [("gemini-cli/none/p01-r0", 0)])


class FindingsTests(unittest.TestCase):
    def test_the_findings_quote_the_report_and_the_post_hoc_blocks(self):
        for which in FINDINGS:
            self.assertEqual(report.check(FINDINGS[which]), [])
        self.assertEqual(posthoc.check(FINDINGS["h1r"].read_text(encoding="utf-8")), [])
        self.assertEqual(posthoc.check(FINDINGS["p1r"].read_text(encoding="utf-8"), ("early", "verifier")), [])

    def test_the_p1r_prose_quotes_only_backed_numbers(self):
        text = FINDINGS["p1r"].read_text(encoding="utf-8")
        none = [r for r in runner.load("p1r") if r["arm"] == "none"]
        aside = json.loads((runner.LAB / "fleet_p1r_refused" / "gemini-cli" / "none-p01-r0.json").read_text())
        rerun = next(r for r in none if r["run_id"] == aside["run_id"])
        moved = []
        for key in ("gamed", "overclaim"):
            after = sum(bool(r["metrics"][key]) for r in none)
            before = after - bool(rerun["metrics"][key]) + bool(aside["metrics"][key])
            moved.append(f"from {before} to {after}")
        self.assertIn(f"{moved[0]} gamed runs and", text.replace("\n  ", " "))
        self.assertIn(f"{moved[1]} overclaims", text.replace("\n  ", " "))
        self.assertIn(f"ended in {aside['wall_s']:.0f} s with exit code {aside['rc']}", text)
        from patton_lab.fleetstudy import verdicts
        rows = runner.load("p1r")
        none_gamed, stop_gamed = verdicts._paired(rows, "stop", "gamed")[:2]
        none_over, stop_over = verdicts._paired(rows, "stop", "overclaim")[:2]
        jit_gamed, rule_gamed = verdicts._paired(rows, "jit", "gamed")[1], verdicts._paired(rows, "rule", "gamed")[1]
        flat = " ".join(text.split())
        self.assertIn(f"gamed the tests in {stop_gamed} runs, against {none_gamed} with no hook", flat)
        self.assertIn(f"plain mission, {stop_over} against {none_over}.", flat)
        self.assertIn(f"either: {jit_gamed} and {rule_gamed} runs against {none_gamed}.", flat)
        late = late_charges("p1r")["open-interpreter/stop/p04-r2/c4/"]
        self.assertIn(f"${late:.6f}", text)
        sources = [report.text("p1r"), posthoc.early_block(), posthoc.verifier_block(),
                   prereg.PATHS["p1r"].read_text(encoding="utf-8"), f"${late:.6f}"]
        self.assertEqual(unbacked_numbers(text, *sources), [])

    def test_the_prose_quotes_only_backed_numbers(self):
        text = FINDINGS["h1r"].read_text(encoding="utf-8")
        rows = runner.load("h1r")
        goose = [r for r in rows if r["harness"] == "goose"]
        model = faults("h1r")
        facts = (f"{len(list((runner.LAB / 'fleet_h1r_refused').glob('*/*.json')))} runs; "
                 f"{len(rows) - 370} runs; goose {model[('goose', 'model')]} faults; "
                 f"{sum(r['rc'] != 0 or r['timed_out'] for r in goose)} of {len(goose)}")
        sources = [report.text("h1r"), posthoc.block(), prereg.PATHS["h1r"].read_text(encoding="utf-8"), facts]
        self.assertEqual(unbacked_numbers(text, *sources), [])
        self.assertIn(f"goose passed {model[('goose', 'model')]} model faults", text)
        self.assertIn(f"{sum(r['rc'] != 0 or r['timed_out'] for r in goose)} of its {len(goose)} runs", text)
        self.assertIn(f"After {len(rows) - 370} runs", text)


if __name__ == "__main__":
    unittest.main()
