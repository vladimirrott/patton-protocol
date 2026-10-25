"""The fleet runs' meter: the triage meter's in-flight worst case, counted against every ledger the lab has."""

from __future__ import annotations

from pathlib import Path

from .. import spend
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
            spent = spend.spent_everywhere(self.lab)
            here = spend.ledgers.ledger_sum(self.ledger)
            if here + self.held + amount > self.cap:
                raise SpendStop(f"the fleet ledger holds ${here:.4f}; another call could pass this set's "
                                f"${self.cap} cap")
            if sum(spent.values()) + self.held + amount > spend.GLOBAL_CAP:
                raise SpendStop(f"the lab has spent ${sum(spent.values()):.4f}; another call could pass the "
                                f"${spend.GLOBAL_CAP} cap")
            self.held += amount
