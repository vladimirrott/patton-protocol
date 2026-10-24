"""Every dollar the lab has spent, from every ledger, so a new study's guard counts all of them.

The first lab keeps a running total in results/spend.json; each later study appends one JSON line per charge to its
own ledger. Any unreadable ledger raises: a guard that cannot count refuses.
"""

from __future__ import annotations

import json
from pathlib import Path

LAB = Path(__file__).resolve().parents[1]
GLOBAL_CAP = 40.00  # dollars the whole lab may spend, every study and pilot together
LINE_LEDGERS = {"gate study": "cc_spend.jsonl", "missions pilot": "missions_spend.jsonl",
                "triage": "triage_spend.jsonl"}


def ledger_sum(path: Path) -> float:
    if not path.exists():
        return 0.0
    return sum(float(json.loads(line)["usd"]) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def spent_everywhere(lab: Path = LAB) -> dict[str, float]:
    first = json.loads((lab / "results" / "spend.json").read_text(encoding="utf-8"))["spent"]
    return {"lab": float(sum(first.values())), **{name: ledger_sum(lab / file) for name, file in LINE_LEDGERS.items()}}
