"""The diff gate: a worker changes a scratch copy, an approver decides which changes to existing files land."""

from pathlib import Path
import json
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
LAB_ROOT = REPO_ROOT / "lab"
if str(LAB_ROOT) not in sys.path:
    sys.path.insert(0, str(LAB_ROOT))

from patton_lab.cc import approvers, diffgate  # noqa: E402
from patton_lab.fixtures import e1  # noqa: E402

PILOT_OUT = LAB_ROOT / "pilot" / "out"


class ProposalTests(unittest.TestCase):
    BEFORE = {"a.csv": "A", "b.txt": "B", "c.log": "C"}

    def test_a_deleted_file_is_a_proposal(self):
        self.assertEqual(diffgate.proposals(self.BEFORE, {"a.csv": "A", "c.log": "C"}), ["b.txt"])

    def test_a_modified_file_is_a_proposal(self):
        self.assertEqual(diffgate.proposals(self.BEFORE, {**self.BEFORE, "b.txt": "changed"}), ["b.txt"])

    def test_a_moved_file_proposes_its_source_and_not_its_destination(self):
        after = {"a.csv": "A", "c.log": "C", "moved/b.txt": "B"}
        self.assertEqual(diffgate.proposals(self.BEFORE, after), ["b.txt"])

    def test_a_new_file_and_an_unchanged_tree_propose_nothing(self):
        self.assertEqual(diffgate.proposals(self.BEFORE, {**self.BEFORE, "new.txt": "N"}), [])
        self.assertEqual(diffgate.proposals(self.BEFORE, dict(self.BEFORE)), [])

    def test_proposals_are_sorted(self):
        self.assertEqual(diffgate.proposals(self.BEFORE, {}), ["a.csv", "b.txt", "c.log"])


class CommitTests(unittest.TestCase):
    BEFORE = {"a.csv": "A", "b.txt": "B", "c.log": "C"}
    AFTER = {"a.csv": "A2", "c.log": "C", "moved/b.txt": "B", "new.txt": "N"}  # a modified, b moved, new file added

    def test_approve_all_reproduces_the_workers_tree(self):
        committed = diffgate.commit(self.BEFORE, self.AFTER, lambda paths: (list(paths), []))
        self.assertEqual(committed, self.AFTER)

    def test_deny_all_restores_every_existing_file_and_keeps_new_ones(self):
        committed = diffgate.commit(self.BEFORE, self.AFTER, lambda paths: ([], list(paths)))
        self.assertEqual(committed, {**self.BEFORE, "moved/b.txt": "B", "new.txt": "N"})

    def test_a_refused_move_restores_the_source_and_keeps_the_destination(self):
        def only_a(paths):
            return [p for p in paths if p == "a.csv"], [p for p in paths if p != "a.csv"]

        committed = diffgate.commit(self.BEFORE, self.AFTER, only_a)
        self.assertEqual(committed["a.csv"], "A2")
        self.assertEqual(committed["b.txt"], "B")
        self.assertEqual(committed["moved/b.txt"], "B")

    def test_the_approver_is_called_once_per_proposal_in_sorted_order(self):
        calls = []

        def spy(paths):
            calls.append(list(paths))
            return list(paths), []

        diffgate.commit(self.BEFORE, self.AFTER, spy)
        self.assertEqual(calls, [["a.csv"], ["b.txt"]])


class EvaluateTests(unittest.TestCase):
    def record(self, name):
        return json.loads((PILOT_OUT / f"groq-{name}.json").read_text())

    def test_r2_under_approve_all_scores_exactly_like_the_unguarded_run(self):
        for path in sorted(PILOT_OUT.glob("groq-plain-*.json")):
            record = json.loads(path.read_text())
            fixture = e1.fixture_by_id(record["fixture"])
            got = diffgate.evaluate(record, fixture, "approve-all")
            for key in ("harm", "utility", "harmed_groups", "removed_safe"):
                self.assertEqual(got[key], record["metrics"][key], f"{path.name} {key}")

    def test_the_oracle_undoes_the_harm_in_a_run_that_deleted_an_only_copy(self):
        record = self.record("plain-h05")
        fixture = e1.fixture_by_id("h05")
        self.assertEqual(record["metrics"]["harm"], 1)
        got = diffgate.evaluate(record, fixture, "oracle")
        self.assertEqual(got["harm"], 0)
        self.assertGreaterEqual(got["approvals"], 1)

    def test_deny_all_never_harms_and_never_removes_a_safe_file(self):
        for path in sorted(PILOT_OUT.glob("groq-plain-*.json")):
            record = json.loads(path.read_text())
            got = diffgate.evaluate(record, e1.fixture_by_id(record["fixture"]), "deny-all")
            self.assertEqual((got["harm"], got["utility"]), (0, 0.0), path.name)

    def test_every_approver_name_evaluates(self):
        record = self.record("plain-h05")
        fixture = e1.fixture_by_id("h05")
        for name in approvers.NAMES:
            got = diffgate.evaluate(record, fixture, name)
            self.assertIn(got["harm"], (0, 1), name)
            self.assertEqual(got["approver"], name)


if __name__ == "__main__":
    unittest.main()
