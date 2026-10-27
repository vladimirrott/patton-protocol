"""Post hoc breakdowns of the registered fleet studies, each chosen after its verdicts were seen and carrying none.

    python3 -m patton_lab.fleetstudy.posthoc            # print every breakdown
    python3 -m patton_lab.fleetstudy.posthoc --check    # fail if a findings file quotes another breakdown

- H1R, `refused`: what the T1 analyst let run that the fixture's oracle would refuse. Each path is put in the
  fixture's own category (`fixtures/e1.py`), with whether it and the other copy of a backed-up pair were still there
  at the end, and whether the run did harm.
- P1R, `early`: gaming by arm, split by whether the run ended early, for the pilot and the study, since a run that
  dies before it touches a test cannot game one; how many early ends came at a model fault Groq refused; and an exact
  test of the pilot's finished runs.
- P1R, `verifier`: in the stop arm, how often the Verifier ran and refused a stop on runs that gamed, by the kind of
  gaming the score found, and on runs that did not.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import sys
from collections import Counter

from ..fixtures import e1
from ..fleet import recorder, runner

FINDINGS = runner.REPO / "docs" / "findings" / "2026-10-26-fleet-h1r-findings.md"
P1R_FINDINGS = runner.REPO / "docs" / "findings" / "2026-10-26-fleet-p1r-findings.md"
BEGIN, END = "<!-- refused -->", "<!-- /refused -->"


def _early(r: dict) -> bool:
    return r["rc"] != 0 or r["timed_out"]


def _ended_at_fault(which: str, r: dict) -> bool:
    """True when the run's last worker call is a refusal of the model's own output (recorder.MODEL_FAULTS)."""
    cassette = runner.record_path(runner.LAB / runner.SETS[which]["out"], r["harness"], r["arm"], r["fixture"],
                                  r["rep"]).with_suffix(".cassette.jsonl.gz")
    with gzip.open(cassette, "rt", encoding="utf-8") as fh:
        worker = [c for c in map(json.loads, fh) if c["route"] == "worker"]
    last = worker[-1] if worker else None
    return bool(last and last["status"] not in (200, None) and last["attempts"]
                and any(m in last["attempts"][-1]["error"] for m in recorder.MODEL_FAULTS))


def fisher(a: int, n1: int, b: int, n2: int) -> float:
    """Two-sided Fisher's exact test of a of n1 against b of n2."""
    total, k = n1 + n2, a + b
    pmf = lambda x: math.comb(n1, x) * math.comb(n2, k - x) / math.comb(total, k)  # noqa: E731
    p0 = pmf(a)
    return min(1.0, sum(pmf(x) for x in range(max(0, k - n2), min(k, n1) + 1) if pmf(x) <= p0 * (1 + 1e-9)))


def early_block() -> str:
    lines = ["| Set | Arm | Gamed | Ended early | Of those, at a model fault | Gamed among those | Gamed among the rest |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    gamed = lambda rs: sum(bool(r["metrics"]["gamed"]) for r in rs)  # noqa: E731
    finished = {}
    for which in ("p1", "p1r"):
        rows = runner.load(which)
        for arm in ("none", "rule", "jit", "stop"):
            g = [r for r in rows if r["arm"] == arm]
            early = [r for r in g if _early(r)]
            rest = [r for r in g if not _early(r)]
            finished[(which, arm)] = (gamed(rest), len(rest))
            lines.append(f"| {which} | {arm} | {gamed(g)} of {len(g)} | {len(early)} of {len(g)} | "
                         f"{sum(_ended_at_fault(which, r) for r in early)} of {len(early)} | "
                         f"{gamed(early)} of {len(early)} | {gamed(rest)} of {len(rest)} |")
    lines.append("")
    for which in ("p1", "p1r"):
        (a, n1), (b, n2) = finished[(which, "stop")], finished[(which, "none")]
        lines.append(f"- {which}, runs that finished: stop gamed {a} of {n1}, none {b} of {n2}; two-sided Fisher's "
                     f"exact p = {fisher(a, n1, b, n2):.3f}")
    return "\n".join(lines)


def verifier_block() -> str:
    rows = [r for r in runner.load("p1r") if r["arm"] == "stop"]
    lines = ["| Stop-arm runs | Runs | Verifier ran | A stop refused | Overclaimed |", "| --- | --- | --- | --- | --- |"]
    gamed = [r for r in rows if r["metrics"]["gamed"]]
    kinds = (("gamed: fitted to the visible tests", [r for r in gamed if r["metrics"]["fitted"]]),
             ("gamed: edited a test", [r for r in gamed if r["metrics"]["tests_edited"]]),
             ("gamed: changed a file outside the task's paths", [r for r in gamed if r["metrics"]["out_of_scope"]]))
    for label, g in (("gamed", gamed), *kinds, ("did not game", [r for r in rows if not r["metrics"]["gamed"]])):
        lines.append(f"| {label} | {len(g)} | {sum(r['metrics']['verifier_runs'] > 0 for r in g)} of {len(g)} | "
                     f"{sum(r['metrics']['refused_stops'] > 0 for r in g)} of {len(g)} | "
                     f"{sum(bool(r['metrics']['overclaim']) for r in g)} of {len(g)} |")
    return "\n".join(lines)


BLOCKS = {"refused": (FINDINGS, lambda: block()), "early": (P1R_FINDINGS, early_block),
          "verifier": (P1R_FINDINGS, verifier_block)}


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
                            "there": path in r["after"],
                            "harm": r["metrics"]["harm"]})
    return out


def block(which: str = "h1r") -> str:
    rows = refused(which)
    kinds = Counter((r["fixture"], r["path"], r["category"], r["twin_kept"], r["there"]) for r in rows)
    lines = ["| Fixture | Path | Category | Path there at the end | Other copy kept | Runs |",
             "| --- | --- | --- | --- | --- | --- |"]
    for (fixture, path, category, kept, there), n in sorted(kinds.items()):
        lines.append(f"| {fixture} | `{path}` | {category} | {'yes' if there else 'no'} | "
                     f"{'no twin' if kept is None else 'yes' if kept else 'no'} | {n} |")
    lines += ["", f"- {len(rows)} refused paths in {len({r['run'] for r in rows})} runs; harm in "
              f"{sum(1 for run in {r['run'] for r in rows} if any(x['harm'] for x in rows if x['run'] == run))} "
              "of those runs"]
    return "\n".join(lines)


def check(text: str, names: tuple[str, ...] = ("refused",)) -> list[str]:
    problems = []
    for name in names:
        begin, end = f"<!-- {name} -->", f"<!-- /{name} -->"
        if begin not in text:
            problems.append(f"the findings quote no {name} breakdown")
        elif text.split(begin, 1)[1].split(end, 1)[0].strip("\n") != BLOCKS[name][1]():
            problems.append(f"the findings quote a different {name} breakdown; regenerate it")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="patton_lab.fleetstudy.posthoc")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if args.check:
        problems = []
        for path in dict.fromkeys(doc for doc, _ in BLOCKS.values()):
            names = tuple(n for n, (doc, _) in BLOCKS.items() if doc == path)
            problems += check(path.read_text(encoding="utf-8"), names)
        print("\n".join(f"!! {p}" for p in problems) if problems else "the findings quote every breakdown")
        return 1 if problems else 0
    for name, (_, make) in BLOCKS.items():
        print(f"<!-- {name} -->\n{make()}\n<!-- /{name} -->\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
