"""The registered fleet studies' report: the runner's table, then each prediction's verdict by the registered rule.

    python3 -m patton_lab.fleetstudy.report --set h1r|p1r
    python3 -m patton_lab.fleetstudy.report --check FINDINGS.md   # fail when the findings quote another report

A report is only final when every registered cell is stored; until then it says how many are missing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..fleet import runner
from . import verdicts

RULES = {"h1r": verdicts.h1r, "p1r": verdicts.p1r}


def markers(which: str) -> tuple[str, str]:
    return f"<!-- {which} report -->", f"<!-- /{which} report -->"


def text(which: str) -> str:
    cfg = runner.SETS[which]
    rows = runner.load(which)
    expected = len(runner.cells(which, list(cfg["harnesses"])))
    lines = [runner.report(which).split("\n\n", 2)[2].rstrip("\n"), ""]
    if len(rows) < expected:
        lines.append(f"Not final: {len(rows)} of {expected} registered runs are stored.")
    else:
        lines.append(f"Final: all {expected} registered runs are stored, ${sum(r['usd'] for r in rows):.4f}.")
    lines.append("")
    lines += [f"- {key}: **{verdict}**. {detail}" for key, (verdict, detail) in RULES[which](rows).items()]
    return "\n".join(lines)


def check(path: Path) -> list[str]:
    body = path.read_text(encoding="utf-8")
    problems = []
    for which in RULES:
        begin, end = markers(which)
        if begin in body and body.split(begin, 1)[1].split(end, 1)[0].strip("\n") != text(which):
            problems.append(f"{path.name} quotes a different {which} report; regenerate it")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="patton_lab.fleetstudy.report")
    parser.add_argument("--set", choices=tuple(RULES))
    parser.add_argument("--check", type=Path)
    args = parser.parse_args(argv)
    if args.check:
        problems = check(args.check)
        print("\n".join(f"!! {p}" for p in problems) if problems else "the findings quote the registered reports")
        return 1 if problems else 0
    which = args.set or "h1r"
    begin, end = markers(which)
    print(f"{begin}\n{text(which)}\n{end}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
