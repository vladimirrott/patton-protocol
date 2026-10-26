"""Every dollar the lab has spent, from every ledger, for guards written after the triage study.

`ledgers.py` is frozen by the triage pre-registration, so a new ledger is listed here instead; every new study's
guard counts this list, which holds every ledger `ledgers.py` counts and the ones that came after it.
"""

from __future__ import annotations

from pathlib import Path

from . import ledgers

LAB = ledgers.LAB
# The CTO approved $50 more on 2026-10-25. ledgers.py keeps the $40 the closed studies ran under.
GLOBAL_CAP = ledgers.GLOBAL_CAP + 50.00
LINE_LEDGERS = {**ledgers.LINE_LEDGERS, "verify": "verify_spend.jsonl", "fleet": "fleet_spend.jsonl",
                # the registered fleet studies, each under its own cap
                "fleet h1r": "fleet_h1r_spend.jsonl", "fleet p1r": "fleet_p1r_spend.jsonl"}


def spent_everywhere(lab: Path = LAB) -> dict[str, float]:
    first = ledgers.spent_everywhere(lab)["lab"]
    return {"lab": first, **{name: ledgers.ledger_sum(lab / file) for name, file in LINE_LEDGERS.items()}}
