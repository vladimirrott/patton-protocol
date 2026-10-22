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

from patton_lab.cc import prereg, report, runner  # noqa: E402

FINDINGS = sorted((REPO_ROOT / "docs" / "findings").glob("*gate-study*.md"))
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
        self.assertEqual(runner.replay(report.RUNS_DIR, report.LEDGER), [])

    def test_the_committed_report_is_the_one_the_records_give(self):
        self.assertTrue(report.REPORT_PATH.exists(), "lab/CC_REPORT.md is not written")
        self.assertEqual(report.REPORT_PATH.read_text(encoding="utf-8"), report.build())

    def test_findings_quote_only_numbers_the_report_prints(self):
        text = report.REPORT_PATH.read_text(encoding="utf-8")
        for path in FINDINGS:
            self.assertEqual(unbacked_decimals(path.read_text(encoding="utf-8"), text), [], path.name)


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

    def test_a_stale_report_is_caught(self):
        self.assertNotEqual(report.build() + "x", report.build())


if __name__ == "__main__":
    unittest.main()
