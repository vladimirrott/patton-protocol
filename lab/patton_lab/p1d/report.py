"""P1D's report: the rules' verdicts, the tables, and what the detector and the Verifier each caught.

    python3 -m patton_lab.p1d.report                  # the report, between its markers
    python3 -m patton_lab.p1d.report --check FILE     # fail when FILE quotes another report
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from ..p1v import report as p1v_report
from . import study

WHICH = "p1d"
ALPHA, RECALL_LIMIT, FALSE_FLAG_LIMIT = p1v_report.ALPHA, p1v_report.RECALL_LIMIT, p1v_report.FALSE_FLAG_LIMIT


def markers() -> tuple[str, str]:
    return f"<!-- {WHICH} report -->", f"<!-- /{WHICH} report -->"


def rate(k: int, n: int) -> str:
    return f"{k} of {n} ({k / n:.3f})" if n else "none"


def counts(rows: list[study.Row]) -> dict[str, tuple[int, int, int]]:
    """For each kind: (runs, detector flags, Verifier flags)."""
    out = {}
    for kind in ("gamed", "met", "unmet"):
        mine = [r for r in rows if r.kind == kind]
        out[kind] = (len(mine), sum(r.detector for r in mine), sum(r.verifier for r in mine))
    return out


def verdicts(rows: list[study.Row]) -> dict[str, tuple[str, str]]:
    gamed = [r for r in rows if r.kind == "gamed"]
    met = [r for r in rows if r.kind == "met"]
    out: dict[str, tuple[str, str]] = {}
    k, n = sum(r.detector for r in gamed), len(gamed)
    out["D1"] = (p1v_report.verdict_recall(k, n) if n else "INCONCLUSIVE",
                 f"the detector flagged {rate(k, n)} of the gamed runs, 95% lower bound "
                 f"{p1v_report.exact_lower(k, n):.3f}" if n else "no gamed runs")
    k2, n2 = sum(r.detector for r in met), len(met)
    out["D2"] = (p1v_report.verdict_false(k2, n2) if n2 else "INCONCLUSIVE",
                 f"the detector flagged {rate(k2, n2)} of the runs that did the work, 95% upper bound "
                 f"{p1v_report.exact_upper(k2, n2):.3f}" if n2 else "no met runs")
    b = sum(r.detector and not r.verifier for r in gamed)
    c = sum(r.verifier and not r.detector for r in gamed)
    out["D3"] = (p1v_report.verdict_paired(b, c),
                 f"only the detector {b}, only the Verifier {c}, sign test p = {p1v_report.sign_p(b, c):.4g}")
    return out


def text() -> str:
    rows = study.test_rows()
    dev = study.development()
    lines = [f"{len(rows)} stored Verifier inputs: {', '.join(f'{s} {sum(r.study == s for r in rows)}' for s, _o, _a in study.SETS)}.",
             "", "| Kind | Runs | Detector flags | Verifier flags |", "| --- | --- | --- | --- |"]
    for kind, (n, d, v) in counts(rows).items():
        lines.append(f"| {kind} | {n} | {d} | {v} |")
    lines += ["", *[f"- {key}: **{verdict}**. {detail}" for key, (verdict, detail) in verdicts(rows).items()], "",
              "| Study | Fixture | Kind | Runs | Detector flags | Verifier flags |", "| --- | --- | --- | --- | --- | --- |"]
    groups: dict[tuple[str, str, str], list[study.Row]] = {}
    for r in rows:
        groups.setdefault((r.study, r.fixture, r.kind), []).append(r)
    for (s, f, kind), mine in sorted(groups.items()):
        lines.append(f"| {s} | {f} | {kind} | {len(mine)} | {sum(r.detector for r in mine)} | {sum(r.verifier for r in mine)} |")
    gamed = [r for r in rows if r.kind == "gamed"]
    fired = Counter(rule for r in gamed for rule in study.rules(r.reasons))
    fits = [r for r in gamed if r.fitted]
    lines += ["", "Exploratory, no verdict:", "",
              f"- Gamed runs flagged by the detector or the Verifier: {sum(r.detector or r.verifier for r in gamed)} of {len(gamed)}; "
              f"by both: {sum(r.detector and r.verifier for r in gamed)}; by neither: {sum(not (r.detector or r.verifier) for r in gamed)}.",
              f"- Gamed runs whose code fitted the visible tests (the oracle's `fitted`): {len(fits)}; the detector flagged "
              f"{sum(r.detector for r in fits)} and the Verifier {sum(r.verifier for r in fits)}.",
              "- Gamed runs flagged, by the rule that fired (a run can fire several): "
              + (", ".join(f"{rule} {n}" for rule, n in sorted(fired.items())) or "none") + ".",
              f"- Runs that did not do the work and did not game (unmet) and that the detector flagged: "
              f"{sum(r.detector for r in rows if r.kind == 'unmet')} of {sum(r.kind == 'unmet' for r in rows)}.",
              f"- Development set (P1R's stop-arm inputs, which the detector was tuned on): the detector flagged "
              f"{sum(r.detector for r in dev if r.kind == 'gamed')} of {sum(r.kind == 'gamed' for r in dev)} gamed runs and "
              f"{sum(r.detector for r in dev if r.kind == 'met')} of {sum(r.kind == 'met' for r in dev)} met runs."]
    return "\n".join(lines)


def check(path: Path) -> list[str]:
    body = path.read_text(encoding="utf-8")
    begin, end = markers()
    if begin in body and body.split(begin, 1)[1].split(end, 1)[0].strip("\n") != text():
        return [f"{path.name} quotes a different {WHICH} report; regenerate it"]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="patton_lab.p1d.report")
    parser.add_argument("--check", type=Path)
    args = parser.parse_args(argv)
    if args.check:
        problems = check(args.check)
        print("\n".join(f"!! {p}" for p in problems) if problems else "the findings quote the registered report")
        return 1 if problems else 0
    begin, end = markers()
    print(f"{begin}\n{text()}\n{end}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
