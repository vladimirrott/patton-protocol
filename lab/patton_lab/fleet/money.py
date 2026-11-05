"""The fleet runs' meter: the triage meter's in-flight worst case, counted against every ledger the lab has.

It counts every `lab/*_spend.jsonl` on disk (`spend_all`), not the fixed list in `spend.py`, which the earlier
pre-registrations froze and which leaves out the ledgers written after them (P1V, P1M).
"""

from __future__ import annotations

from pathlib import Path

from .. import spend, spend_all
from ..triage import runner as triage

LAB = spend.LAB
LEDGER = LAB / spend.LINE_LEDGERS["fleet"]
SpendStop = triage.SpendStop


class Meter(triage.Meter):
    """Refuses a call whose worst case could carry this run set's ledger past `cap`, or the lab past its cap."""

    def __init__(self, lab: Path = LAB, ledger: Path = LEDGER, cap: float = 0.0) -> None:
        super().__init__(lab, ledger, cap)

    def reserve(self, amount: float) -> None:
        with self.lock:
            spent = spend_all.spent_everywhere(self.lab)
            here = spend_all.ledger_sum(self.ledger) if self.ledger.exists() else 0.0
            if here + self.held + amount > self.cap:
                raise SpendStop(f"the fleet ledger holds ${here:.4f}; another call could pass this set's "
                                f"${self.cap} cap")
            if sum(spent.values()) + self.held + amount > spend_all.GLOBAL_CAP:
                raise SpendStop(f"the lab has spent ${sum(spent.values()):.4f}; another call could pass the "
                                f"${spend_all.GLOBAL_CAP} cap")
            self.held += amount
