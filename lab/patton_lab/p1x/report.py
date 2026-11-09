"""P1X's report: the runner's table, the registered verdicts, then what the refusals did and how the detect arm compares.

    python3 -m patton_lab.p1x.report                  # the report, between its markers
    python3 -m patton_lab.p1x.report --check FILE     # fail when FILE quotes another report

A report is only final when every registered cell is stored; until then it says how many are missing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from ..fleet import runner
from . import verdicts

WHICH = "p1x"
FITTING = ("p01", "p04")  # the fixtures where a run can game the checks


def markers() -> tuple[str, str]:
    return f"<!-- {WHICH} report -->", f"<!-- /{WHICH} report -->"


def _n(rows: list[dict[str, Any]], key: str) -> int:
    return sum(bool(r["metrics"][key]) for r in rows)


def mechanism(rows: list[dict[str, Any]]) -> list[str]:
    """Exploratory, no verdict."""
    rows = verdicts.with_undisclosed(rows)
    lines = ["| Fixture | Arm | Runs | Gamed | Undisclosed fits | Overclaimed | Honest end | Goal met |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for fixture in runner.SETS[WHICH]["fixtures"]:
        for arm in ("none", *verdicts.ARMS):
            group = [r for r in rows if r["fixture"] == fixture and r["arm"] == arm]
            if group:
                lines.append(f"| {fixture} | {arm} | {len(group)} | {_n(group, 'gamed')} | {_n(group, 'undisclosed')} | "
                             f"{_n(group, 'overclaim')} | {_n(group, 'honest_end')} | {_n(group, 'goal_met')} |")
    lines.append("")
    for arm in verdicts.ARMS:
        fit = [r for r in rows if r["arm"] == arm and r["fixture"] in FITTING]
        refused = [r for r in fit if r["metrics"]["verifier_blocks"]]
        quiet = [r for r in fit if not r["metrics"]["verifier_blocks"]]
        lines.append(f"- {arm} runs on p01 and p04: {len(fit)}. Refused at least once: {len(refused)}; of those, ended gamed "
                     f"{_n(refused, 'gamed')}, ended with an honest end {_n(refused, 'honest_end')}, ended with an undisclosed fit "
                     f"{_n(refused, 'undisclosed')}; never refused: {len(quiet)}; of those, ended gamed {_n(quiet, 'gamed')}. "
                     f"Flags the round cap let through: {sum(r['metrics']['verifier_capped'] for r in fit)}.")
    cells: dict[tuple[str, str, int], dict[str, dict[str, Any]]] = {}
    for r in rows:
        cells.setdefault((r["harness"], r["fixture"], r["rep"]), {})[r["arm"]] = r
    both = [c for c in cells.values() if "stop" in c and "detect" in c]
    for key, name in (("undisclosed", "undisclosed fits"), ("overclaim", "overclaims"), ("gamed", "gamed runs")):
        only_detect = sum(bool(c["detect"]["metrics"][key]) and not c["stop"]["metrics"][key] for c in both)
        only_stop = sum(bool(c["stop"]["metrics"][key]) and not c["detect"]["metrics"][key] for c in both)
        lines.append(f"- Detect against stop, paired on {len(both)} cells, {name}: only detect {only_detect}, only stop {only_stop}.")
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
    lines += [f"- {key}: **{verdict}**. {detail}" for key, (verdict, detail) in verdicts.p1x(rows).items()]
    lines += ["", *mechanism(rows)]
    return "\n".join(lines)


def check(path: Path) -> list[str]:
    body = path.read_text(encoding="utf-8")
    begin, end = markers()
    if begin in body and body.split(begin, 1)[1].split(end, 1)[0].strip("\n") != text():
        return [f"{path.name} quotes a different {WHICH} report; regenerate it"]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="patton_lab.p1x.report")
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
