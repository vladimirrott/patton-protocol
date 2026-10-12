"""Tests for lab/patton_lab/cassette.py: strict record/replay of model calls."""

from pathlib import Path
import gzip
import sys
from tempfile import TemporaryDirectory
import unittest

LAB_ROOT = Path(__file__).resolve().parents[1] / "lab"
if str(LAB_ROOT) not in sys.path:
    sys.path.insert(0, str(LAB_ROOT))

from patton_lab.cassette import Cassette, CassetteMiss, request_key  # noqa: E402


class RequestKeyTests(unittest.TestCase):
    def test_key_ignores_dict_order(self):
        a = {"model": "m", "messages": [{"role": "user", "content": "hi"}], "salt": "s"}
        b = {"salt": "s", "messages": [{"content": "hi", "role": "user"}], "model": "m"}
        self.assertEqual(request_key(a), request_key(b))

    def test_salt_changes_key(self):
        a = {"model": "m", "salt": "job1"}
        b = {"model": "m", "salt": "job2"}
        self.assertNotEqual(request_key(a), request_key(b))

    def test_key_is_hex_sha256(self):
        key = request_key({"x": 1})
        self.assertEqual(len(key), 64)
        int(key, 16)


class CassetteTests(unittest.TestCase):
    def test_put_then_reload(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.jsonl.gz"
            cassette = Cassette(path, "record")
            cassette.put("k1", {"response": 1})
            cassette.put("k2", {"response": 2})
            again = Cassette(path, "replay")
            self.assertEqual(again.get("k1"), {"response": 1})
            self.assertEqual(again.get("k2"), {"response": 2})
            self.assertEqual(len(again), 2)

    def test_replay_cannot_put(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.jsonl.gz"
            Cassette(path, "record").put("k", {"r": 1})
            with self.assertRaises(RuntimeError):
                Cassette(path, "replay").put("k2", {"r": 2})

    def test_replay_miss_is_tracked_and_fails_at_the_end(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.jsonl.gz"
            Cassette(path, "record").put("k", {"r": 1})
            replay = Cassette(path, "replay")
            self.assertIsNone(replay.get("absent"))
            replay.note_miss("absent")
            with self.assertRaises(CassetteMiss):
                replay.assert_no_misses()

    def test_replay_of_missing_file_raises(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                Cassette(Path(tmp) / "absent.jsonl.gz", "replay")

    def test_truncated_tail_survives_in_record_mode_only(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.jsonl.gz"
            cassette = Cassette(path, "record")
            cassette.put("k1", {"r": 1})
            cassette.put("k2", {"r": 2})
            whole = path.read_bytes()
            cassette.put("k3", {"r": 3})
            third = path.read_bytes()[len(whole):]
            path.write_bytes(whole + third[: len(third) // 2])

            with self.assertRaises(Exception):
                Cassette(path, "replay")

            recovered = Cassette(path, "record")
            self.assertEqual(recovered.get("k1"), {"r": 1})
            self.assertEqual(recovered.get("k2"), {"r": 2})
            self.assertIsNone(recovered.get("k3"))
            # The rewrite drops the broken member, so a replay now loads cleanly.
            self.assertEqual(len(Cassette(path, "replay")), 2)

    def test_duplicate_put_keeps_the_first_entry(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.jsonl.gz"
            cassette = Cassette(path, "record")
            cassette.put("k", {"r": 1})
            cassette.put("k", {"r": 2})
            self.assertEqual(Cassette(path, "replay").get("k"), {"r": 1})

    def test_file_is_gzip_jsonl(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.jsonl.gz"
            Cassette(path, "record").put("k", {"r": 1})
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                lines = [line for line in handle.read().splitlines() if line]
            self.assertEqual(len(lines), 1)
            self.assertIn('"key":"k"', lines[0])


if __name__ == "__main__":
    unittest.main()
