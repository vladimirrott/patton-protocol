"""Every dollar the lab has spent, from every ledger, for guards written after the triage study.

`ledgers.py` is frozen by the triage pre-registration, so a new ledger is listed here instead; every new study's
guard counts this list, which holds every ledger `ledgers.py` counts and the ones that came after it.
"""

from __future__ import annotations

from pathlib import Path

from . import ledgers

LAB = ledgers.LAB
GLOBAL_CAP = ledgers.GLOBAL_CAP
LINE_LEDGERS = {**ledgers.LINE_LEDGERS, "verify": "verify_spend.jsonl"}


def spent_everywhere(lab: Path = LAB) -> dict[str, float]:
    first = ledgers.spent_everywhere(lab)["lab"]
    return {"lab": first, **{name: ledgers.ledger_sum(lab / file) for name, file in LINE_LEDGERS.items()}}
