"""Every dollar the lab has spent, counted from the files on disk, for guards written after the fleet studies.

`ledgers.py` and `spend.py` are frozen by earlier pre-registrations, so each new ledger had to be listed by editing a
frozen file. This module counts every `lab/*_spend.jsonl` it finds instead, so a ledger cannot be left out of a cap
guard. An unreadable ledger raises: a guard that cannot count refuses.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import ledgers, spend

LAB = ledgers.LAB
GLOBAL_CAP = spend.GLOBAL_CAP


def ledger_sum(path: Path) -> float:
    total = 0.0
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            total += float(json.loads(line)["usd"])
        except (ValueError, KeyError, TypeError) as err:
            raise ValueError(f"{path.name} line {number} is not a charge: {err}") from err
    return total


def spent_everywhere(lab: Path = LAB) -> dict[str, float]:
    first = json.loads((lab / "results" / "spend.json").read_text(encoding="utf-8"))["spent"]
    return {"lab": float(sum(first.values())),
            **{path.stem: ledger_sum(path) for path in sorted(lab.glob("*_spend.jsonl"))}}
