"""Post hoc, after H1R: what the T1 analyst let run that the fixture's oracle would refuse, and what was lost.

    python3 -m patton_lab.fleetstudy.posthoc            # print the breakdown
    python3 -m patton_lab.fleetstudy.posthoc --check    # fail if the H1R findings quote another breakdown

Chosen after R3's verdict was seen, so it carries no verdict. Each refused path is put in the fixture's own
category (`fixtures/e1.py`), with whether the other copy of a backed-up pair was still there at the end and whether
the run did harm.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from ..fixtures import e1
from ..fleet import runner

FINDINGS = runner.REPO / "docs" / "findings" / "2026-10-26-fleet-h1r-findings.md"
BEGIN, END = "<!-- refused -->", "<!-- /refused -->"


def refused(which: str = "h1r") -> list[dict]:
    out = []
    for r in runner.load(which):
        if r["arm"] != "triage":
            continue
        fixture = e1.fixture_by_id(r["fixture"])
        group_of = {path: g for g in fixture.groups for path in g.paths}
        oracle = e1.oracle_approver(fixture)
        for entry in r["ledger"]:
            if entry.get("verdict") != "triaged":
                continue
            for path in oracle(list(entry.get("paths") or []))[1]:
                group = group_of.get(path)
                twin = [q for q in group.paths if q != path] if group else []
                out.append({"run": f"{r['harness']}/{r['fixture']}-r{r['rep']}", "fixture": r["fixture"],
                            "path": path, "category": group.category if group else "none",
                            "twin_kept": all(q in r["after"] for q in twin) if twin else None,
                            "harm": r["metrics"]["harm"]})
    return out


def block(which: str = "h1r") -> str:
    rows = refused(which)
    kinds = Counter((r["fixture"], r["path"], r["category"], r["twin_kept"]) for r in rows)
    lines = ["| Fixture | Path | Category | Other copy kept | Runs |", "| --- | --- | --- | --- | --- |"]
    for (fixture, path, category, kept), n in sorted(kinds.items()):
        lines.append(f"| {fixture} | `{path}` | {category} | {'no twin' if kept is None else 'yes' if kept else 'no'} "
                     f"| {n} |")
    lines += ["", f"- {len(rows)} refused paths in {len({r['run'] for r in rows})} runs; harm in "
              f"{sum(1 for run in {r['run'] for r in rows} if any(x['harm'] for x in rows if x['run'] == run))} "
              "of those runs"]
    return "\n".join(lines)


def check(text: str) -> list[str]:
    if BEGIN not in text:
        return ["the findings quote no refused-path breakdown"]
    quoted = text.split(BEGIN, 1)[1].split(END, 1)[0].strip("\n")
    return [] if quoted == block() else ["the findings quote a different refused-path breakdown; regenerate it"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="patton_lab.fleetstudy.posthoc")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if args.check:
        problems = check(FINDINGS.read_text(encoding="utf-8"))
        print("\n".join(f"!! {p}" for p in problems) if problems else "the findings quote the breakdown")
        return 1 if problems else 0
    print(f"{BEGIN}\n{block()}\n{END}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
