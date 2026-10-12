"""Tests for lab/patton_lab/memo.py: code execution replays from the record, never re-runs."""

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

LAB_ROOT = Path(__file__).resolve().parents[1] / "lab"
if str(LAB_ROOT) not in sys.path:
    sys.path.insert(0, str(LAB_ROOT))

from patton_lab.cassette import Cassette, CassetteMiss  # noqa: E402
from patton_lab.memo import ExecMemo  # noqa: E402


class MemoTests(unittest.TestCase):
    def test_record_then_replay_serves_the_first_output(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "exec.jsonl.gz"
            runs = []

            def thunk():
                runs.append(1)
                return 0, f"run {len(runs)}"

            record = ExecMemo(Cassette(path, "record"))
            self.assertEqual(record.run("tool", "print(1)", {"a": "h1"}, thunk), (0, "run 1"))
            self.assertEqual(record.run("tool", "print(1)", {"a": "h1"}, thunk), (0, "run 1"))
            replay = ExecMemo(Cassette(path, "replay"))
            self.assertEqual(replay.run("tool", "print(1)", {"a": "h1"}, thunk), (0, "run 1"))
            self.assertEqual(len(runs), 1)

    def test_replay_miss_raises_and_never_runs_the_code(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "exec.jsonl.gz"
            ExecMemo(Cassette(path, "record")).run("tool", "print(1)", {"a": "h1"}, lambda: (0, "x"))
            replay = ExecMemo(Cassette(path, "replay"))
            ran = []
            for code, state in (("print(2)", {"a": "h1"}), ("print(1)", {"a": "h2"})):
                with self.assertRaises(CassetteMiss):
                    replay.run("tool", code, state, lambda: ran.append(1) or (0, "y"))
            self.assertEqual(ran, [])
            self.assertEqual(len(replay.cassette.misses), 2)


if __name__ == "__main__":
    unittest.main()
