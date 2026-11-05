"""spend_all counts every lab/*_spend.jsonl, so a new study's ledger cannot be forgotten by a cap guard."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lab"))

from patton_lab import ledgers, spend, spend_all  # noqa: E402


def make_lab(root: Path, first: dict, ledgers_: dict) -> Path:
    (root / "results").mkdir(parents=True)
    (root / "results" / "spend.json").write_text(json.dumps({"spent": first}), encoding="utf-8")
    for name, usd in ledgers_.items():
        (root / name).write_text("".join(json.dumps({"id": str(i), "usd": u}) + "\n" for i, u in enumerate(usd)),
                                 encoding="utf-8")
    return root


class SpendAllTests(unittest.TestCase):
    def test_counts_the_first_lab_and_every_spend_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            lab = make_lab(Path(tmp), {"e1": 1.5, "e2": 0.5}, {"a_spend.jsonl": [0.25, 0.25], "b_spend.jsonl": [1.0]})
            got = spend_all.spent_everywhere(lab)
        self.assertEqual(got, {"lab": 2.0, "a_spend": 0.5, "b_spend": 1.0})

    def test_a_ledger_nobody_listed_is_still_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            lab = make_lab(Path(tmp), {"e1": 0.0}, {"brand_new_study_spend.jsonl": [3.0]})
            self.assertEqual(sum(spend_all.spent_everywhere(lab).values()), 3.0)

    def test_an_unreadable_ledger_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            lab = make_lab(Path(tmp), {"e1": 0.0}, {})
            (lab / "broken_spend.jsonl").write_text("not json\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                spend_all.spent_everywhere(lab)

    def test_files_that_are_not_ledgers_are_left_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            lab = make_lab(Path(tmp), {"e1": 0.0}, {"x_spend.jsonl": [1.0]})
            (lab / "notes.jsonl").write_text('{"usd": 99}\n', encoding="utf-8")
            self.assertEqual(sum(spend_all.spent_everywhere(lab).values()), 1.0)

    def test_agrees_with_the_frozen_lists_on_the_real_lab(self):
        listed = spend.spent_everywhere()
        counted = spend_all.spent_everywhere()
        by_file = {name: counted[Path(file).stem] for name, file in spend.LINE_LEDGERS.items()}
        for name, value in by_file.items():
            self.assertAlmostEqual(value, listed[name], places=9, msg=name)
        self.assertAlmostEqual(counted["lab"], listed["lab"], places=9)
        unlisted = set(counted) - {"lab"} - {Path(f).stem for f in spend.LINE_LEDGERS.values()}
        # Ledgers made after spend.py was frozen are counted by spend_all alone; name each one here when it is created.
        after_the_freeze = {"p1v_spend", "p1m_spend", "fleet_p1l_spend"}
        self.assertEqual(unlisted - after_the_freeze, set(), "a ledger exists that spend.py does not list")

    def test_the_cap_is_the_lab_wide_ninety(self):
        self.assertEqual(spend_all.GLOBAL_CAP, spend.GLOBAL_CAP)
        self.assertEqual(spend_all.GLOBAL_CAP, ledgers.GLOBAL_CAP + 50.0)


if __name__ == "__main__":
    unittest.main()
