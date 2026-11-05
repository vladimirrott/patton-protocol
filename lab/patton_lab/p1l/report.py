"""P1L's report: the runner's table, the registered verdicts, then what happened to the runs the Verifier refused.

    python3 -m patton_lab.p1l.report                  # the report, between its markers
    python3 -m patton_lab.p1l.report --check FILE     # fail when FILE quotes another report

A report is only final when every registered cell is stored; until then it says how many are missing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from ..fleet import runner
from . import verdicts

WHICH = "p1l"
FITTING = ("p01", "p04")  # the fixtures where a run can game the checks


def markers() -> tuple[str, str]:
    return f"<!-- {WHICH} report -->", f"<!-- /{WHICH} report -->"


def _count(rows: list[dict[str, Any]], key: str) -> int:
    return sum(bool(r["metrics"][key]) for r in rows)


def mechanism(rows: list[dict[str, Any]]) -> list[str]:
    """Exploratory, no verdict: each fixture by arm, then what became of the stop runs the Verifier refused."""
    lines = ["| Fixture | Arm | Runs | Gamed | Overclaimed | Honest end | Goal met |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for fixture in runner.SETS[WHICH]["fixtures"]:
        for arm in ("none", "stop"):
            group = [r for r in rows if r["fixture"] == fixture and r["arm"] == arm]
            if group:
                lines.append(f"| {fixture} | {arm} | {len(group)} | {_count(group, 'gamed')} | "
                             f"{_count(group, 'overclaim')} | {_count(group, 'honest_end')} | {_count(group, 'goal_met')} |")
    stop = [r for r in rows if r["arm"] == "stop" and r["fixture"] in FITTING]
    refused = [r for r in stop if r["metrics"]["verifier_blocks"]]
    quiet = [r for r in stop if not r["metrics"]["verifier_blocks"]]
    lines += ["", f"- Stop runs on p01 and p04: {len(stop)}. Of them, refused at least once by the Verifier: "
                  f"{len(refused)}; of those, ended gamed {_count(refused, 'gamed')}, ended with an honest end "
                  f"{_count(refused, 'honest_end')}; never refused: {len(quiet)}; of those, ended gamed "
                  f"{_count(quiet, 'gamed')}.",
              f"- Verifier flags the round cap let through, stop runs on p01 and p04: "
              f"{sum(r['metrics']['verifier_capped'] for r in stop)}."]
    return lines


def text() -> str:
    cfg = runner.SETS[WHICH]
    rows = runner.load(WHICH)
    expected = len(runner.cells(WHICH, list(cfg["harnesses"])))
    lines = [runner.report(WHICH).split("\n\n", 2)[2].rstrip("\n"), ""]
    if len(rows) < expected:
        lines.append(f"Not final: {len(rows)} of {expected} registered runs are stored.")
    else:
        lines.append(f"Final: all {expected} registered runs are stored, ${sum(r['usd'] for r in rows):.4f}.")
    lines.append("")
    lines += [f"- {key}: **{verdict}**. {detail}" for key, (verdict, detail) in verdicts.p1l(rows).items()]
    lines += ["", *mechanism(rows)]
    return "\n".join(lines)


def check(path: Path) -> list[str]:
    body = path.read_text(encoding="utf-8")
    begin, end = markers()
    if begin in body and body.split(begin, 1)[1].split(end, 1)[0].strip("\n") != text():
        return [f"{path.name} quotes a different {WHICH} report; regenerate it"]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="patton_lab.p1l.report")
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
