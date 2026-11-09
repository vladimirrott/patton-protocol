"""P1D's data: the Verifier inputs of P1L's stop arm and P1H's hold arm, each with the deterministic detector's flag, the
live Verifier's decision on the same input, and the oracle's label.

An input is the last Verifier call of a run (as P1V took it); a run that never reached the Verifier is not in the set.
The detector never saw these runs before the study: it was written against P1R's inputs (`development()`).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..p1 import fixtures as p1
from ..p1h import prereg as p1h_prereg
from ..p1l import prereg as p1l_prereg
from ..p1v import cases as p1v_cases
from . import detector

LAB = Path(__file__).resolve().parents[2]
SETS = (("p1l", "fleet_p1l", "stop"), ("p1h", "fleet_p1h", "hold"))
DEVELOPMENT = ("fleet_p1r", "stop")


@dataclass(frozen=True)
class Row:
    study: str
    id: str
    fixture: str
    kind: str
    fitted: bool
    detector: bool
    reasons: tuple[str, ...]
    verifier: bool


def rules(reasons) -> set[str]:
    """The names of the rules that fired: the word before the colon of each reason."""
    return {r.split(":", 1)[0] for r in reasons}


def _row(study: str, case, gate) -> Row:
    found = detector.detect(p1.fixture_by_id(case.fixture).files, case.source)
    decision = gate.judge(case.original, case.source)[2] if case.original is not None else "allow (not verified)"
    return Row(study, case.id, case.fixture, case.kind, case.fitted, found.flag, tuple(found.reasons), decision == "block")


def test_rows() -> list[Row]:
    gates = {"p1l": p1l_prereg.registered_gate(), "p1h": p1h_prereg.registered_gate()}
    rows: list[Row] = []
    for study, out, arm in SETS:
        cases, _skipped = p1v_cases.collect_stored(LAB / out, arm)
        rows += [_row(study, c, gates[study]) for c in cases]
    return rows


def development() -> list[Row]:
    """P1R's stop-arm inputs, which the detector was tuned on, scored with the shipped plugin's own judge."""
    cases, _skipped = p1v_cases.collect_stored(LAB / DEVELOPMENT[0], DEVELOPMENT[1])
    return [_row("p1r", c, p1v_cases.gate) for c in cases]
