"""The gate study as committed: pre-registration, records, cassettes, spend, report and the prose that quotes it.

Each check is a function over data, and a second test feeds it a bad input to prove it fails.
"""

from pathlib import Path
import copy
import json
import re
import subprocess
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
LAB_ROOT = REPO_ROOT / "lab"
if str(LAB_ROOT) not in sys.path:
    sys.path.insert(0, str(LAB_ROOT))

from patton_lab.cc import prereg, recorder, report, runner  # noqa: E402

FINDINGS = sorted(p for p in (REPO_ROOT / "docs" / "findings").glob("*gate-study*.md")
                  if "codex" not in p.name)  # tests/test_codex_study_integrity.py checks the Codex study's
DECIMAL = re.compile(r"(?<![\w.])-?\d+\.\d+(?![\w.])")


def records():
    return report.load(report.RUNS_DIR)


def hash_problems(rows, prereg_text):
    frozen = set(re.findall(r"`([0-9a-f]{64})`", prereg_text))
    problems = []
    for r in rows:
        if r.get("gate_hash") and r["gate_hash"] not in frozen:
            problems.append(f"{r['run_id']}: gate not the frozen one")
        if r.get("skill_hash") and r["skill_hash"] not in frozen:
            problems.append(f"{r['run_id']}: skill not the frozen one")
        if r["arm"] in ("O-R1", "O-R1S", "O-R1D") and not r.get("gate_hash"):
            problems.append(f"{r['run_id']}: a hook arm without a gate hash")
    return problems


def count_problems(rows):
    problems = []
    planned = prereg.planned_runs()
    seen = set()
    for r in rows:
        key = (r["arm"], r["fixture"], r["rep"])
        if r["arm"] not in planned:
            problems.append(f"{r['run_id']}: an arm the pre-registration does not list")
        if r["fixture"] not in prereg.FIXTURES:
            problems.append(f"{r['run_id']}: a fixture outside the study")
        if not 0 <= r["rep"] < prereg.REPS:
            problems.append(f"{r['run_id']}: rep {r['rep']} outside 0..{prereg.REPS - 1}")
        if key in seen:
            problems.append(f"{r['run_id']}: stored twice")
        seen.add(key)
    return problems


def seed_problems(rows):
    return [f"{r['run_id']}: seed {r.get('seed')} is not the registered {prereg.SEED}" for r in rows
            if r.get("seed") != prereg.SEED]


def unbacked_decimals(prose, report_text):
    """Decimals in a findings document that the report does not print."""
    return sorted({d for d in DECIMAL.findall(prose) if d not in report_text})


# Claude Code fills schema defaults into a tool call before it writes the transcript: the model sent an Edit
# without `replace_all`, and the transcript shows `"replace_all": false`. The frozen replay compares the two
# exactly and reports it (one run of 560 in the 2026-10-21 study). Only that exact difference is accepted here.
FILLED_DEFAULTS = {"Edit": {"replace_all": False}}


def default_filled(taped, recorded):
    """True when every difference between the cassette's tool calls and the transcript's is a filled default."""
    if len(taped) != len(recorded):
        return False
    for a, b in zip(taped, recorded):
        if a == b:
            continue
        filled = FILLED_DEFAULTS.get(a.get("name"))
        if not filled or (a["id"], a["name"]) != (b["id"], b["name"]) or not isinstance(a.get("input"), dict):
            return False
        if b["input"] != {**a["input"], **{k: v for k, v in filled.items() if k not in a["input"]}}:
            return False
    return True


def unexplained_replay_problems(problems, out_dir):
    left = []
    for problem in problems:
        run_id, _, what = problem.partition(": ")
        path = Path(out_dir) / f"{run_id}.json"
        if what == "the tool calls in the cassette differ from the transcript" and path.exists():
            record = json.loads(path.read_text(encoding="utf-8"))
            taped = recorder.tool_uses(recorder.load(Path(out_dir) / record["cassette"]))
            if default_filled(taped, record["tools"]):
                continue
        left.append(problem)
    return left


def first_commit_time(path):
    out = subprocess.run(["git", "-C", str(REPO_ROOT), "log", "--diff-filter=A", "--format=%ct", "--", path],
                         capture_output=True, text=True)
    stamps = [int(x) for x in out.stdout.split()]
    return min(stamps) if stamps else None


class StudyIntegrityTests(unittest.TestCase):
    def test_the_committed_preregistration_matches_its_inputs(self):
        self.assertTrue(prereg.PREREG_PATH.exists(), "the pre-registration is not written")
        self.assertEqual(prereg.PREREG_PATH.read_text(encoding="utf-8"), prereg.render(),
                         "a frozen input changed after the freeze, or the file was edited by hand")

    def test_the_preregistration_was_committed_before_the_first_study_record(self):
        rows = records()
        if not rows:
            self.skipTest("no study record yet")
        frozen = first_commit_time(str(prereg.PREREG_PATH.relative_to(REPO_ROOT)))
        first_run = first_commit_time("lab/cc_runs")
        if frozen is None or first_run is None:
            self.skipTest("git history unavailable")
        self.assertLessEqual(frozen, first_run)

    def test_every_record_carries_the_frozen_gate_and_skill(self):
        self.assertEqual(hash_problems(records(), prereg.PREREG_PATH.read_text(encoding="utf-8")), [])

    def test_recorded_counts_stay_inside_the_plan(self):
        self.assertEqual(count_problems(records()), [])

    def test_every_record_ran_with_the_registered_seed(self):
        self.assertEqual(seed_problems(records()), [])

    def test_every_record_and_charge_replays(self):
        problems = runner.replay(report.RUNS_DIR, report.LEDGER)
        self.assertEqual(unexplained_replay_problems(problems, report.RUNS_DIR), [])

    def test_the_committed_report_is_the_one_the_records_give(self):
        self.assertTrue(report.REPORT_PATH.exists(), "lab/CC_REPORT.md is not written")
        self.assertEqual(report.REPORT_PATH.read_text(encoding="utf-8"), report.build())

    def test_findings_quote_only_numbers_the_report_prints(self):
        text = report.REPORT_PATH.read_text(encoding="utf-8") + "\n" + prereg.PREREG_PATH.read_text(encoding="utf-8")
        self.assertTrue(FINDINGS, "the study has no findings document")
        for path in FINDINGS:
            self.assertEqual(unbacked_decimals(path.read_text(encoding="utf-8"), text), [], path.name)


RECORD = REPO_ROOT / "docs" / "records" / "2026-10-22-gate-study-record.md"
SPEND_ROW = re.compile(r"^\| `?(2026-10-12 lab ledger|2026-10-19 unguarded Claude Code pilot|[a-z0-9_]+)[^|]*\| ([^|]+) \| (\d+\.\d{4}) \|$",
                       re.M)


def spend_table(text):
    return {m.group(1): (m.group(2).strip(), m.group(3)) for m in SPEND_ROW.finditer(text)}


def spend_from_ledgers():
    from patton_lab.cc.meter import Meter
    lab = json.loads((LAB_ROOT / "results" / "spend.json").read_text())["spent"]
    meter = Meter(LAB_ROOT / "cc_spend.jsonl", LAB_ROOT / "results" / "spend.json",
                  [LAB_ROOT / "pilot" / "out", LAB_ROOT / "pilot" / "first_pass", LAB_ROOT / "pilot" / "out_failed"])
    table = {"2026-10-12 lab ledger": ("n/a", f"{sum(lab.values()):.4f}"),
             "2026-10-19 unguarded Claude Code pilot": ("n/a", f"{meter.prior() - sum(lab.values()):.4f}")}
    groups = {}
    for line in (LAB_ROOT / "cc_spend.jsonl").read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            key = row["id"].split("/")[0] if "/" in row["id"] else "cc_smoke"
            n, usd = groups.get(key, (0, 0.0))
            groups[key] = (n + 1, usd + row["usd"])
    table.update({k: (str(n), f"{usd:.4f}") for k, (n, usd) in groups.items()})
    return table, f"{meter.total():.4f}"


class RecordTests(unittest.TestCase):
    def test_the_record_spend_table_is_the_ledgers(self):
        text = RECORD.read_text(encoding="utf-8")
        expected, total = spend_from_ledgers()
        self.assertEqual(spend_table(text), expected)
        self.assertIn(f"| Total | | {total} |", text)


class GuardsBiteTests(unittest.TestCase):
    """Each guard above, fed a bad input."""

    def row(self, **kw):
        base = {"run_id": "O-R1-f01-r0", "arm": "O-R1", "fixture": "f01", "rep": 0, "gate_hash": "a" * 64,
                "skill_hash": None}
        return {**base, **kw}

    def test_a_record_with_another_gate_is_caught(self):
        text = f"| x | `{'a' * 64}` |"
        self.assertEqual(hash_problems([self.row()], text), [])
        self.assertTrue(hash_problems([self.row(gate_hash="b" * 64)], text))
        self.assertTrue(hash_problems([self.row(gate_hash=None)], text))
        self.assertTrue(hash_problems([self.row(arm="O-R0S", gate_hash=None, skill_hash="c" * 64)], text))

    def test_counts_outside_the_plan_are_caught(self):
        good = self.row()
        self.assertEqual(count_problems([good]), [])
        for bad in ([good, copy.deepcopy(good)], [self.row(arm="R0")], [self.row(fixture="h01")],
                    [self.row(rep=prereg.REPS)]):
            self.assertTrue(count_problems(bad), bad)

    def test_a_record_with_another_seed_is_caught(self):
        self.assertEqual(seed_problems([self.row(seed=prereg.SEED)]), [])
        self.assertTrue(seed_problems([self.row(seed=0)]))
        self.assertTrue(seed_problems([self.row()]))

    def test_an_unbacked_number_in_prose_is_caught(self):
        self.assertEqual(unbacked_decimals("harm fell to 0.125", "| x | 0.125 |"), [])
        self.assertEqual(unbacked_decimals("harm fell to 0.13", "| x | 0.125 |"), ["0.13"])

    def test_only_a_filled_default_is_accepted_as_a_replay_difference(self):
        sent = {"id": "t1", "name": "Edit", "input": {"file_path": "/work/a", "old_string": "x", "new_string": ""}}
        shown = {"id": "t1", "name": "Edit", "input": {**sent["input"], "replace_all": False}}
        self.assertTrue(default_filled([sent], [shown]))
        self.assertFalse(default_filled([sent], [{**shown, "input": {**shown["input"], "replace_all": True}}]))
        self.assertFalse(default_filled([sent], [{**shown, "input": {**shown["input"], "new_string": "y"}}]))
        self.assertFalse(default_filled([{**sent, "name": "Write"}], [{**shown, "name": "Write"}]))
        self.assertFalse(default_filled([sent], [shown, shown]))
        self.assertFalse(default_filled([sent], [{**shown, "id": "t2"}]))

    def test_a_record_spend_row_that_differs_from_the_ledgers_is_caught(self):
        expected, _ = spend_from_ledgers()
        text = RECORD.read_text(encoding="utf-8").replace(expected["cc_runs"][1], "15.0000")
        self.assertNotEqual(spend_table(text), expected)

    def test_a_stale_report_is_caught(self):
        self.assertNotEqual(report.build() + "x", report.build())


if __name__ == "__main__":
    unittest.main()
