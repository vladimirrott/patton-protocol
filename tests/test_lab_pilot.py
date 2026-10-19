"""The committed pilot records, their report, and their scores must agree."""

from pathlib import Path
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
PILOT = REPO_ROOT / "lab" / "pilot"
for path in (REPO_ROOT / "lab", PILOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import summarize  # noqa: E402


class PilotRecordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = summarize.load()

    def test_every_arm_ran_every_held_out_fixture_once(self):
        seen = sorted((r["arm"], r["fixture"]) for r in self.rows)
        expected = sorted((arm, f"h0{i}") for arm in summarize.ARMS for i in range(1, 9))
        self.assertEqual(seen, expected)

    def test_every_record_rescores_from_its_stored_end_state(self):
        for r in self.rows:
            self.assertEqual(summarize.rescore(r), r["metrics"], f"{r['arm']} {r['fixture']}")

    def test_no_record_stores_provider_reasoning_text(self):
        for r in self.rows:
            for e in r["events"]:
                message = e.get("message")
                content = message.get("content") if isinstance(message, dict) else None
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "thinking":
                            self.assertEqual(set(block), {"type", "chars"}, f"{r['arm']} {r['fixture']}")

    def test_no_stored_text_names_a_host_user(self):
        # `ls -l` inside the container prints the host user as file owner. Only worker and root may appear.
        import json
        owner = summarize.re.compile(r"[-dl][rwx-]{9}\.?\s+\d+\s+(\S+)\s+(\S+)")
        names = set()
        for path in (PILOT / "out").glob("*.json"):
            for m in owner.finditer(path.read_text()):
                names.update(m.groups())
        self.assertTrue(names, "no ls -l output found, so this check would pass vacuously")
        self.assertLessEqual(names, {"worker", "root"})

    def test_the_owner_scan_can_fail(self):
        owner = summarize.re.compile(r"[-dl][rwx-]{9}\.?\s+\d+\s+(\S+)\s+(\S+)")
        self.assertEqual(owner.findall("-rw-rw-r-- 1 someone someone 4 Oct 19 HANDOFF.md"), [("someone", "someone")])

    def test_committed_report_is_what_the_records_produce(self):
        self.assertEqual((PILOT / "PILOT.md").read_text(), summarize.build() + "\n",
                         "regenerate with: python3 lab/pilot/summarize.py")

    def test_the_rescoring_check_can_fail(self):
        bad = dict(self.rows[0])
        bad["after"] = {}  # everything deleted
        self.assertNotEqual(summarize.rescore(bad), self.rows[0]["metrics"])


if __name__ == "__main__":
    unittest.main()
