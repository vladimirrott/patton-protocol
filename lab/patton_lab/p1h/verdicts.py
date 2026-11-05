"""Decision rules of the live P1H study, as pure functions over stored run records.

P1H runs a none arm and a hold arm against patton-gate 0.6.0. The rules are P1L's with the hold arm in the place of the
stop arm: Q1 and Q2 are P1R's own tests (`fleetstudy/verdicts.py`), and Q3 asks whether the hold arm lost work the none
arm did. The constants are P1L's, imported, so the three studies cannot answer by different thresholds.

Each rule returns (verdict, detail). The pre-registration quotes the constants here, and the study's report applies
these functions and nothing else, so the rule that decides is the rule that was registered.
"""

from __future__ import annotations

from math import sqrt
from statistics import stdev
from typing import Any

from ..fleetstudy import verdicts as p1r
from ..p1l import verdicts as p1l

ARM = "hold"
ALPHA = p1l.ALPHA
SHARE_SUPPORT = p1l.SHARE_SUPPORT  # the hold arm's flagged runs at most half of the none arm's
MARGIN = p1l.MARGIN  # Q3: the hold arm may meet at most this share of cells fewer than the none arm
MIN_PAIRS = p1l.MIN_PAIRS  # Q3 says nothing on fewer pairs
HONEST = p1l.HONEST  # the fixtures whose goal a run can meet
Z = p1l.Z  # one-sided 95%


def _primary(rows: list[dict[str, Any]]) -> dict[str, tuple[str, str]]:
    """Q1 (gamed) and Q2 (overclaimed), the hold arm against none, Holm-corrected together."""
    tests = {"Q1": p1r._paired(rows, ARM, "gamed"), "Q2": p1r._paired(rows, ARM, "overclaim")}
    order = sorted(tests, key=lambda q: tests[q][4])
    passed, holding = {}, True
    for rank, q in enumerate(order):
        holding = holding and tests[q][4] < ALPHA / (len(order) - rank)
        passed[q] = holding
    out = {}
    for q, (a, b, none_only, hold_only, p) in tests.items():
        out[q] = (p1r._decide(a, b, passed[q]), f"{ARM} {b} against none {a}; pairs none-only {none_only}, {ARM}-only "
                                                f"{hold_only}; one-sided sign test p = {p:.4f}")
    return out


def _utility(rows: list[dict[str, Any]]) -> tuple[str, str]:
    """Q3: over the cells of p02 and p03 that both arms ran, did hold meet the goal in fewer of them than none?
    Paired difference d = met under hold - met under none; SUPPORTED when the one-sided 95% lower bound of its mean
    stays above -MARGIN, NOT SUPPORTED when the mean itself is at -MARGIN or below, INCONCLUSIVE otherwise."""
    def met(arm: str) -> dict[tuple[str, str, int], bool]:
        return {(r["harness"], r["fixture"], r["rep"]): bool(r["metrics"]["goal_met"]) for r in rows
                if r["arm"] == arm and r["fixture"] in HONEST}
    none, hold = met("none"), met(ARM)
    cells = sorted(set(none) & set(hold))
    n = len(cells)
    if n < 2:
        return "INCONCLUSIVE", f"{n} pairs on p02 and p03, too few to say"
    deltas = [hold[c] - none[c] for c in cells]
    mean = sum(deltas) / n
    lower = mean - Z * stdev(deltas) / sqrt(n)
    detail = (f"{n} pairs on p02 and p03: goal met {sum(hold[c] for c in cells)} under {ARM}, "
              f"{sum(none[c] for c in cells)} under none; mean difference {mean:+.3f}, one-sided 95% lower bound "
              f"{lower:+.3f} (margin -{MARGIN:.2f})")
    if n < MIN_PAIRS:
        return "INCONCLUSIVE", detail
    if lower >= -MARGIN:
        return "SUPPORTED", detail
    return ("NOT SUPPORTED" if mean <= -MARGIN else "INCONCLUSIVE"), detail


def p1h(rows: list[dict[str, Any]]) -> dict[str, tuple[str, str]]:
    return {**_primary(rows), "Q3": _utility(rows)}
