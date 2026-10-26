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

FINDINGS = {"h1r": REPO / "docs" / "findings" / "2026-10-26-fleet-h1r-findings.md"}


def faults(which):
    counts = Counter()
    for path in (runner.LAB / runner.SETS[which]["out"]).glob("*/*.cassette.jsonl.gz"):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                call = json.loads(line)
                if call["status"] != 200:
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

    def test_the_ledger_holds_what_the_records_charge(self):
        for which in FINDINGS:
            spent = ledgers.ledger_sum(runner.LAB / runner.SETS[which]["ledger"])
            self.assertAlmostEqual(spent, sum(r["usd"] for r in runner.load(which)), places=6)

    def test_no_stored_request_was_refused_by_the_provider(self):
        for which in FINDINGS:
            self.assertEqual({k: v for k, v in faults(which).items() if k[1] == "refused"}, {})

    def test_the_set_aside_h1r_runs_are_failed_starts_only(self):
        aside = [json.loads(p.read_text()) for p in (runner.LAB / "fleet_h1r_refused").glob("*/*.json")]
        self.assertEqual(len(aside), 370)
        self.assertTrue(all(r["calls"] == 0 and "create keyring" in r["stderr_tail"] for r in aside))


class FindingsTests(unittest.TestCase):
    def test_the_findings_quote_the_report_and_the_post_hoc_block(self):
        self.assertEqual(report.check(FINDINGS["h1r"]), [])
        self.assertEqual(posthoc.check(FINDINGS["h1r"].read_text(encoding="utf-8")), [])

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
