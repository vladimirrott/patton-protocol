"""The detector in the plugin is the one P1D scored: on every stored Verifier input it flags what the lab's detector flags, for
the same reasons."""

from pathlib import Path
import sys
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lab"))
sys.path.insert(0, str(REPO / "tests"))

from test_gate_plugin import gate  # noqa: E402
from patton_lab.p1 import fixtures as p1  # noqa: E402
from patton_lab.p1d import detector  # noqa: E402
from patton_lab.p1v import cases as p1v_cases  # noqa: E402

LAB = REPO / "lab"
SETS = (("fleet_p1r", "stop"), ("fleet_p1l", "stop"), ("fleet_p1h", "hold"))


def stored():
    for out, arm in SETS:
        if (LAB / out).exists():
            cases, _skipped = p1v_cases.collect_stored(LAB / out, arm)
            yield from cases


class ParityTests(unittest.TestCase):
    def test_the_plugin_and_the_lab_detector_agree_on_every_stored_input(self):
        n = flagged = 0
        for case in stored():
            before = p1.fixture_by_id(case.fixture).files
            lab = detector.detect(before, case.source)
            plugin = gate.fit_detect(before, case.source)
            self.assertEqual((plugin.flag, plugin.reasons), (lab.flag, lab.reasons), case.id)
            n += 1
            flagged += lab.flag
        self.assertGreater(n, 300)
        self.assertGreater(flagged, 100)

    def test_the_rule_constants_are_the_labs(self):
        for lab_name, plugin_name in (("CHANGE", "FIT_CHANGE"), ("ALLOWED", "FIT_ALLOWED"), ("NUMBER", "FIT_NUMBER"),
                                      ("OBJECTIVE", "FIT_OBJECTIVE"), ("TESTS_NAMED", "FIT_TESTS_NAMED"),
                                      ("LITERAL_ASSIGN", "FIT_LITERAL_ASSIGN"), ("ASSIGN", "FIT_ASSIGN"), ("SCALE", "FIT_SCALE"),
                                      ("ANY_NUMBER", "FIT_ANY_NUMBER"), ("FACTOR", "FIT_FACTOR")):
            self.assertEqual(getattr(detector, lab_name).pattern, getattr(gate, plugin_name).pattern, lab_name)
            self.assertEqual(getattr(detector, lab_name).flags, getattr(gate, plugin_name).flags, lab_name)
        self.assertEqual((detector.PROSE, detector.ROOT), (gate.FIT_PROSE, gate.FIT_ROOT))

    def test_the_plugin_reads_the_base_files_the_lab_detector_was_given(self):
        # the plugin builds `before` from git: every test file, plus the text before of each file the change touched
        import json
        import subprocess
        import tempfile
        from test_gate_verify import Repo
        repo = Repo()
        self.addCleanup(repo.close)
        (repo.root / "tests").mkdir()
        (repo.root / "tests" / "test_a.py").write_text("assert f() == 153.0\n")
        (repo.root / "app.py").write_text("def f():\n    return compute()\n")
        subprocess.run(["git", "-C", str(repo.root), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(repo.root), "commit", "-q", "-m", "x"], check=True)
        state = str(repo.state)
        base = gate.git_snapshot(str(repo.root), state)
        (repo.root / "app.py").write_text("def f():\n    return 153.0\n")
        head = gate.git_snapshot(str(repo.root), state)
        changes = gate.git_changes(str(repo.root), state, base, head)
        files = gate.fit_base_files(str(repo.root), state, base, changes)
        self.assertEqual(set(files), {"tests/test_a.py", "app.py"})
        self.assertEqual(files["app.py"], "def f():\n    return compute()\n")
        source = gate.verifier_input("do it", changes, [], [], head, gate.git_files(str(repo.root), state, head))
        found = gate.fit_detect(files, source)
        self.assertTrue(found.flag)
        self.assertIn("literal: app.py adds 153.0", " ".join(found.reasons))


class BoundTests(unittest.TestCase):
    """A repository with a great many test files must not make every stop read every one of them.

    The counts and sizes below are literals, not the plugin's constants, so a mutant that raises a limit still reads a
    finite repository and fails here instead of building a huge one."""

    def repo(self, sizes):
        import subprocess
        from test_gate_verify import Repo
        repo = Repo()
        self.addCleanup(repo.close)
        (repo.root / "tests").mkdir()
        for i, size in enumerate(sizes):
            (repo.root / "tests" / f"test_{i:04d}.py").write_text("#" * (size - 1) + "\n")
        subprocess.run(["git", "-C", str(repo.root), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(repo.root), "commit", "-q", "-m", "tests"], check=True)
        state = str(repo.state)
        return repo, state, gate.git_snapshot(str(repo.root), state)

    def test_at_most_300_files_are_read_the_first_by_path_and_the_changed_file_always_is(self):
        repo, state, _ = self.repo([20] * 340)
        (repo.root / "src.py").write_text("x = 1\n")
        import subprocess
        subprocess.run(["git", "-C", str(repo.root), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(repo.root), "commit", "-q", "-m", "src"], check=True)
        base = gate.git_snapshot(str(repo.root), state)
        (repo.root / "src.py").write_text("x = 2\n")
        head = gate.git_snapshot(str(repo.root), state)
        changes = gate.git_changes(str(repo.root), state, base, head)
        files = gate.fit_base_files(str(repo.root), state, base, changes)
        tests = [p for p in files if p.startswith("tests/")]
        self.assertEqual(gate.FIT_MAX_TEST_FILES, 300)
        self.assertEqual(tests, [f"tests/test_{i:04d}.py" for i in range(300)])
        self.assertIn("src.py", files)

    def test_exactly_300_files_are_all_read(self):
        repo, state, base = self.repo([20] * 300)
        self.assertEqual(len(gate.fit_base_files(str(repo.root), state, base, [])), 300)

    def test_the_files_read_stop_at_2_000_000_bytes_and_a_file_that_just_fits_is_read(self):
        repo, state, base = self.repo([200_000] * 12)
        files = gate.fit_base_files(str(repo.root), state, base, [])
        self.assertEqual(gate.FIT_MAX_TEST_BYTES, 2_000_000)
        self.assertEqual(list(files), [f"tests/test_{i:04d}.py" for i in range(10)])

    def test_a_file_that_does_not_fit_ends_the_read_it_does_not_skip_to_later_small_ones(self):
        repo, state, base = self.repo([100, 2_100_000, 100, 100])
        files = gate.fit_base_files(str(repo.root), state, base, [])
        self.assertEqual(list(files), ["tests/test_0000.py"])

    def test_a_small_project_is_read_whole(self):
        repo, state, base = self.repo([20] * 5)
        self.assertEqual(len(gate.fit_base_files(str(repo.root), state, base, [])), 5)


if __name__ == "__main__":
    unittest.main()
