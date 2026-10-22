"""The Codex study's pre-registration: what it freezes, and that a change after the freeze shows up."""

from pathlib import Path
from unittest import mock
import json
import sys
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lab"))

from patton_lab.codex import prereg, runner  # noqa: E402


class DesignTests(unittest.TestCase):
    def test_the_design_is_the_one_the_person_chose(self):
        self.assertEqual(prereg.MODEL, "gpt-6.1-sol")
        self.assertEqual(prereg.REPS, 3)
        self.assertEqual(prereg.ARMS, ("X-R0", "X-R0S", "X-R1", "X-R1S", "X-R1D"))
        self.assertEqual(len(prereg.FIXTURES), 16)
        self.assertEqual(sum(prereg.planned_runs().values()), 240)

    def test_every_registered_arm_exists_in_the_runner(self):
        for name in prereg.ARMS:
            self.assertIn(name, runner.ARMS)
        for p in prereg.PREDICTIONS:
            for arm in p.arm_a.split("+") + ([p.arm_b] if p.arm_b else []):
                if not arm.startswith("R2"):
                    self.assertIn(arm, prereg.ARMS, p.id)

    def test_the_thresholds_are_the_claude_code_studys(self):
        from patton_lab.cc import prereg as cc
        self.assertEqual([(p.id, p.kind, p.metric, p.threshold) for p in prereg.PREDICTIONS],
                         [(p.id, p.kind, p.metric, p.threshold) for p in cc.PREDICTIONS])
        self.assertEqual((prereg.SESOI, prereg.FLOOR, prereg.CEILING_FLOOR), (cc.SESOI, cc.FLOOR, cc.CEILING_FLOOR))

    def test_the_bypass_allowance_follows_the_rule(self):
        self.assertEqual(prereg.bypass_allowance(), 1)  # floor(0.02 * 2 arms * 16 fixtures * 3 reps)

    def test_the_quota_stops_leave_most_of_the_week(self):
        self.assertLessEqual(prereg.WEEKLY_STOP, 60)
        self.assertLess(prereg.FIVE_HOUR_STOP, 100)


class FreezeTests(unittest.TestCase):
    def test_the_frozen_inputs_cover_everything_that_decides_a_score(self):
        for rel in ("gate/patton_gate.py", "lab/patton_lab/codex/adapter.py", "lab/patton_lab/codex/recorder.py",
                    "lab/patton_lab/codex/runner.py", "lab/patton_lab/codex/report.py", "lab/patton_lab/cc/metrics.py",
                    "lab/patton_lab/cc/approvers.py", "lab/patton_lab/cc/diffgate.py", "lab/patton_lab/cc/arms.py",
                    "lab/patton_lab/fixtures/e1.py", "lab/patton_lab/score.py", "lab/patton_lab/stats.py",
                    "lab/patton_lab/isolate.py", prereg.CATALOG_REL):
            self.assertIn(rel, prereg.FROZEN_FILES)

    def test_the_rendered_text_holds_every_hash_and_the_binary_pins(self):
        text = prereg.render()
        for rel in prereg.FROZEN_FILES:
            self.assertIn(prereg.file_hash(rel), text, rel)
        self.assertIn(prereg.CODEX_BINARY_SHA256, text)
        self.assertIn(prereg.CODE_MODE_HOST_SHA256, text)
        self.assertIn(runner.skill_hash(), text)

    def test_a_changed_input_makes_the_check_fail(self):
        real = prereg.file_hash
        with mock.patch.object(prereg, "file_hash", side_effect=lambda rel: "0" * 64 if rel.endswith("adapter.py")
                               else real(rel)):
            self.assertEqual(prereg.main(["--check"]), 1)

    def test_the_committed_file_matches(self):
        self.assertEqual(prereg.main(["--check"]), 0)

    def test_what_the_worker_sees_is_what_the_calibration_ran(self):
        # The prereg says the adapter, recorder, gate and mission are unchanged since the calibration commit.
        import subprocess
        for rel in ("lab/patton_lab/codex/adapter.py", "lab/patton_lab/codex/recorder.py", "gate/patton_gate.py",
                    "lab/patton_lab/cc/arms.py"):
            done = subprocess.run(["git", "show", f"9cd71e1:{rel}"], cwd=REPO, capture_output=True)
            if done.returncode != 0:
                self.skipTest("this clone has no history back to the calibration commit")
            self.assertEqual(done.stdout, (REPO / rel).read_bytes(), rel)

    def test_the_catalog_copy_is_the_one_every_calibration_run_used(self):
        from patton_lab.codex import runner as r
        copy = json.loads((REPO / prereg.CATALOG_REL).read_text())
        digest = r._sha(json.dumps(copy, sort_keys=True).encode())
        runs = sorted((REPO / "lab" / "codex_runs" / prereg.MODEL).glob("*.json"))
        self.assertGreater(len(runs), 0)
        for path in runs:
            self.assertEqual(json.loads(path.read_text())["catalog_hash"], digest, path.name)

    def test_the_catalog_copy_is_the_registered_models_entry_and_nothing_of_the_account(self):
        entry = json.loads((REPO / prereg.CATALOG_REL).read_text())
        self.assertEqual(entry["slug"], prereg.MODEL)
        text = json.dumps(entry)
        self.assertNotIn("@", text)
        cache = Path.home() / ".codex" / "models_cache.json"
        if cache.exists():  # the cache's account identity stays out of the copy
            identity = json.loads(cache.read_text()).get("identity")
            if isinstance(identity, str) and len(identity) > 8:
                self.assertNotIn(identity, text)
        auth = runner.recorder_mod.ChatGPTAuth()
        if auth.path.exists():
            for secret in auth.secrets():
                self.assertNotIn(secret, text)


if __name__ == "__main__":
    unittest.main()
