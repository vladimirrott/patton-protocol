"""P1M pre-registration: it matches the code, the study refuses without it, and the stored dev answers replay."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lab"))

from patton_lab.p1m import arms as arms_mod  # noqa: E402
from patton_lab.p1m import prereg, runner  # noqa: E402
from patton_lab.p1v import cases as cases_mod  # noqa: E402


class PreregTests(unittest.TestCase):
    def test_the_committed_file_matches_the_code(self):
        self.assertEqual(prereg.check(), 0)

    def test_the_text_names_the_predictions_the_arms_the_dropped_arms_and_the_counts(self):
        text = prereg.text()
        for needle in ("| M1 |", "| M2 |", "| q38-med |", "| q38-high |", "g120-med, g120-high, sg20-high",
                       "41 gamed, 33 met, 15 unmet", "qwen/qwen3.6-27b", "7 gamed runs", "drop_params"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_every_frozen_file_exists_and_is_hashed(self):
        text = prereg.text()
        for rel in prereg.FROZEN:
            with self.subTest(rel=rel):
                self.assertTrue((prereg.REPO / rel).is_file())
                self.assertIn(f"`{rel}`", text)

    def test_the_reference_draws_are_pinned_by_hash(self):
        self.assertIn("lab/p1v_study/fitted-v2.jsonl.gz", prereg.FROZEN)
        self.assertIn("lab/p1v_study/fitted-v2#2.jsonl.gz", prereg.FROZEN)

    def test_a_changed_arm_fails_the_check(self):
        changed = {**arms_mod.ARMS, "q38-high": arms_mod.Arm("q38-high", "qwen3.8-27b", "medium", hide=True)}
        with mock.patch.object(arms_mod, "ARMS", changed):
            self.assertEqual(prereg.check(), 1)

    def test_a_file_that_differs_fails_the_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.md"
            path.write_text(prereg.text() + "edited", encoding="utf-8")
            with mock.patch.object(prereg, "PREREG_PATH", path):
                self.assertEqual(prereg.check(), 1)


class GuardTests(unittest.TestCase):
    def test_the_study_refuses_without_a_passing_prereg_and_asks_nothing(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(runner, "TRACE_DIR", Path(tmp)), \
                mock.patch.object(prereg, "check", return_value=1), mock.patch.object(runner, "run_set") as run:
            (Path(tmp) / "TRACE_OK").write_text(runner.stamp() + "\n", encoding="utf-8")
            self.assertEqual(runner.main(["study"]), 2)
            run.assert_not_called()

    def test_the_study_refuses_a_restricted_arm_list(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(runner, "TRACE_DIR", Path(tmp)), \
                mock.patch.object(prereg, "check", return_value=0), mock.patch.object(runner, "run_set") as run:
            (Path(tmp) / "TRACE_OK").write_text(runner.stamp() + "\n", encoding="utf-8")
            self.assertEqual(runner.main(["study", "--arms", "q38-high"]), 2)
            run.assert_not_called()

    def test_a_paid_run_refuses_without_a_trace_for_this_tree(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(runner, "TRACE_DIR", Path(tmp)), \
                mock.patch.object(runner, "run_set") as run:
            self.assertEqual(runner.main(["dev"]), 2)
            (Path(tmp) / "TRACE_OK").write_text("an old stamp\n", encoding="utf-8")
            self.assertEqual(runner.main(["dev"]), 2)
            run.assert_not_called()

    def test_the_study_set_is_the_89_p1v_used(self):
        self.assertEqual(len(runner.datasets("study")), 89)


class StoredDevTests(unittest.TestCase):
    @unittest.skipUnless((runner.LAB / "p1m_dev").exists() and cases_mod.isolate_available(),
                         "no stored dev run, or podman is missing")
    def test_the_stored_dev_answers_redecide_the_same(self):
        names = runner.draw_names(arms_mod.ALL, runner.DRAWS["dev"])
        self.assertEqual(runner.replay(runner.datasets("dev"), runner.LAB / "p1m_dev", names), [])


if __name__ == "__main__":
    unittest.main()
