"""The Codex study report applies the frozen Claude Code study's verdict rules, unchanged.

The parity test runs the Codex report's rules over the 560 stored Claude Code runs, with the Claude Code design,
and requires every prediction to match what the frozen `patton_lab.cc.report` computes from the same records.
"""

from pathlib import Path
from dataclasses import replace
import copy
import json
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lab"))

from patton_lab.cc import prereg as cc_prereg  # noqa: E402
from patton_lab.cc import report as cc_report  # noqa: E402
from patton_lab.codex import report  # noqa: E402
from patton_lab.fixtures import e1  # noqa: E402

CC_DESIGN = report.Design(
    arms=cc_prereg.ARMS, fixtures=cc_prereg.FIXTURES, reps=cc_prereg.REPS, predictions=cc_prereg.PREDICTIONS,
    floor=cc_prereg.FLOOR, ceiling_floor=cc_prereg.CEILING_FLOOR, sesoi=cc_prereg.SESOI,
    unguarded="O-R0", gated="O-R1", bound_arms=("O-R1", "O-R1S"), skill_pairs=(("O-R0S", "O-R0"), ("O-R1S", "O-R1")),
    deny_all="O-R1D")
CC_ROWS = cc_report.load(REPO / "lab" / "cc_runs")


class ParityTests(unittest.TestCase):
    def test_the_rules_give_the_frozen_reports_verdicts_and_numbers_on_the_claude_code_study(self):
        self.assertEqual(len(CC_ROWS), 560)
        self.assertEqual(report.predictions(CC_ROWS, CC_DESIGN), cc_report.predictions(CC_ROWS))

    def test_parity_holds_on_an_incomplete_study_too(self):
        partial = [r for r in CC_ROWS if not (r["rep"] == 3 and r["fixture"] == "f05")]
        self.assertEqual(report.predictions(partial, CC_DESIGN), cc_report.predictions(partial))

    def test_parity_holds_where_the_floor_rule_decides(self):
        # The real study never reached the floor branch; lower the unguarded harm until it does.
        rows = copy.deepcopy(CC_ROWS)
        for r in rows:
            if r["arm"] == "O-R0" and r["fixture"] not in ("f01",):
                r["metrics"]["harm"] = 0
        ours, theirs = report.predictions(rows, CC_DESIGN), cc_report.predictions(rows)
        self.assertTrue(ours[0]["verdict"].startswith("INCONCLUSIVE (floor"), ours[0]["verdict"])
        self.assertEqual(ours, theirs)

    def test_parity_holds_where_the_hook_is_inferior(self):
        rows = copy.deepcopy(CC_ROWS)
        for r in rows:
            if r["arm"] == "O-R1":
                r["metrics"]["utility"] = 0.0
        ours, theirs = report.predictions(rows, CC_DESIGN), cc_report.predictions(rows)
        self.assertEqual(next(q for q in ours if q["id"] == "Q5")["verdict"], "NOT SUPPORTED (inferior)")
        self.assertEqual(ours, theirs)

    def test_the_parity_test_can_fail(self):
        # A changed threshold must change a verdict, or the comparison above proves nothing.
        loose = replace(CC_DESIGN, predictions=tuple(replace(p, threshold=0.5) if p.id == "Q1" else p
                                                      for p in CC_DESIGN.predictions))
        self.assertNotEqual(report.predictions(CC_ROWS, loose), cc_report.predictions(CC_ROWS))


def record(arm, fixture, rep, harm=0, utility=1.0, approvals=0, bypass=0, finished=True, model_calls=4):
    metrics = {"harm": harm, "utility": utility, "approvals": approvals, "bypass_harm": bypass,
               "granted_harm": harm - bypass, "opaque_refusals": 0, "denials": 0, "gate_tampered": False,
               "skill_invoked": False, "report_consistent": True, "tool_counts": {"Bash": 2}, "harmed_groups": []}
    fx = e1.fixture_by_id(fixture)
    return {"run_id": f"{arm}-{fixture}-r{rep}", "arm": arm, "fixture": fixture, "rep": rep, "metrics": metrics,
            "finished": finished, "attempts": [{}], "skill_offered": arm.endswith("S"), "calls": [{}] * model_calls,
            "after": dict(fx.files), "tool_names": ["Bash"], "final_text": "done",
            "model_calls": [{"name": "exec", "input": "tools.exec_command({cmd: 'ls', workdir: '/work'})"}],
            "rate_limits": {"primary": [1.0, 2.0], "secondary": [3.0, 3.0]}}


class CodexReportTests(unittest.TestCase):
    def test_the_report_builds_from_codex_records_and_names_its_rules(self):
        design = report.codex_design()
        rows = [record(a, f, 0, harm=int(a == "X-R0" and f in ("f01", "f02", "f03", "f04")))
                for a in design.arms for f in design.fixtures]
        with tempfile.TemporaryDirectory() as tmp:
            for r in rows:
                Path(tmp, f"{r['run_id']}.json").write_text(json.dumps(r))
            text = report.build(Path(tmp))
        self.assertIn("complete reps: 1 of 3", text)
        self.assertIn("| X-R0 | 16 |", text)
        self.assertIn("2026-10-22-codex-gate-study-preregistration.md", text)
        self.assertIn("Q1", text)
        self.assertIn("workdir", text)
        self.assertIn("Weekly limit readings over the stored runs: lowest 3 %, highest 3 %", text)
        self.assertIn("5-hour window readings over the stored runs: lowest 1 %, highest 2 %", text)

    def test_only_complete_reps_are_analysed(self):
        design = report.codex_design()
        rows = [record(a, f, 0) for a in design.arms for f in design.fixtures]
        rows += [record("X-R0", "f01", 1)]
        self.assertEqual(report.complete_reps(rows, design), [0])
        self.assertEqual(len(report.analysed(rows, design)), len(design.arms) * len(design.fixtures))


if __name__ == "__main__":
    unittest.main()
