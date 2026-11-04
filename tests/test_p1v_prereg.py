"""P1V pre-registration: it matches the code, the study refuses without it, and the stored dev answers replay."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lab"))

from patton_lab.p1v import cases as cases_mod  # noqa: E402
from patton_lab.p1v import prereg, prompts, runner  # noqa: E402


class PreregTests(unittest.TestCase):
    def test_the_committed_file_matches_the_code(self):
        self.assertEqual(prereg.check(), 0)

    def test_the_text_holds_the_predictions_the_ground_and_the_counts(self):
        text = prereg.text()
        for needle in ("| P1 |", "| P2 |", "| P3 |", prompts.FITTED_V2.rstrip(), "41 gamed (p01 21, p04 20), 33 met, 15 unmet",
                       "89 runs of 112 stop runs", "`open-interpreter/stop-p01-r0`"):
            with self.subTest(needle=needle[:40]):
                self.assertIn(needle, text)

    def test_every_frozen_file_exists_and_is_hashed(self):
        text = prereg.text()
        for rel in prereg.FROZEN:
            with self.subTest(rel=rel):
                self.assertTrue((prereg.REPO / rel).is_file())
                self.assertIn(f"`{rel}`", text)

    def test_a_changed_input_changes_the_digest(self):
        study, _ = cases_mod.collect_stored(cases_mod.LAB / "fleet_p1r")
        before = prereg.dataset_digest(study)
        first = study[0]
        changed = [cases_mod.Case(first.id, first.harness, first.fixture, first.rep, first.gamed, first.fitted,
                                  first.met, first.system, first.source + "x", first.original, first.calls)] + study[1:]
        self.assertNotEqual(prereg.dataset_digest(changed), before)

    def test_a_file_that_differs_fails_the_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.md"
            path.write_text(prereg.text() + "edited", encoding="utf-8")
            with mock.patch.object(prereg, "PREREG_PATH", path):
                self.assertEqual(prereg.check(), 1)
            path.unlink()
            with mock.patch.object(prereg, "PREREG_PATH", path):
                self.assertEqual(prereg.check(), 1)

    def test_a_changed_prompt_fails_the_check(self):
        with mock.patch.object(prompts, "FITTED_V2", prompts.FITTED_V2 + "  extra\n"):
            self.assertEqual(prereg.check(), 1)


class StudyGuardTests(unittest.TestCase):
    def test_the_study_refuses_without_a_passing_prereg_and_asks_nothing(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(runner, "TRACE_DIR", Path(tmp)), \
                mock.patch.object(prereg, "check", return_value=1), mock.patch.object(runner, "run_set") as run:
            (Path(tmp) / "TRACE_OK").write_text(runner.stamp() + "\n", encoding="utf-8")
            self.assertEqual(runner.main(["study"]), 2)
            run.assert_not_called()

    def test_a_paid_run_refuses_without_a_trace_for_this_tree(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(runner, "TRACE_DIR", Path(tmp)), \
                mock.patch.object(runner, "run_set") as run:
            self.assertEqual(runner.main(["dev"]), 2)
            (Path(tmp) / "TRACE_OK").write_text("an old stamp\n", encoding="utf-8")
            self.assertEqual(runner.main(["dev"]), 2)
            run.assert_not_called()


class StoredDevTests(unittest.TestCase):
    @unittest.skipUnless((runner.LAB / "p1v_dev").exists() and cases_mod.isolate_available(),
                         "no stored dev run, or podman is missing")
    def test_the_stored_dev_answers_redecide_the_same(self):
        self.assertEqual(runner.replay(runner.datasets("dev"), runner.LAB / "p1v_dev", runner.DEV_ARMS), [])


if __name__ == "__main__":
    unittest.main()
