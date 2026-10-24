"""Post hoc, after the T1 triage study: how many distinct cases its zero misses rest on.

    python3 -m patton_lab.posthoc.triage_cases            # print the table
    python3 -m patton_lab.posthoc.triage_cases --check    # fail if the findings quote a different table

Chosen after the results were seen, so it carries no verdict. The registered bound is a fixture-cluster bootstrap,
and a bootstrap of a rate that is zero in every fixture is zero, whatever the sample size. This counts the distinct
critical cases (fixture and set of critical paths) behind the critical flags, since reps of a fixture repeat the
same case, and gives the exact one-sided 95% Clopper-Pearson upper bound for zero misses over the flags and over the
distinct cases. It also counts where the primary cell's false escalations sit.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

from ..fixtures import e1
from ..triage import flags, runner

LAB = Path(__file__).resolve().parents[2]
FINDINGS = LAB.parent / "docs" / "findings" / "2026-10-24-triage-findings.md"
BEGIN, END = "<!-- cases -->", "<!-- /cases -->"


def zero_upper(n: int, alpha: float = 0.05) -> float:
    """Exact one-sided upper bound on a rate when 0 of n independent trials hit."""
    return 1 - alpha ** (1 / n)


def table() -> str:
    data = flags.collect(flags.TEST_SOURCES).flags
    critical = [f for f in data if f.critical]
    cases = {(f.fixture, frozenset(p for p in f.paths if f.must[p] is True)) for f in critical}
    by_category: Counter = Counter()
    for fixture, paths in cases:
        group_of = {p: g.category for g in e1.fixture_by_id(fixture).groups for p in g.paths}
        for path in paths:
            by_category[group_of[path]] += 1
    lines = ["| Count | Value |", "| --- | --- |",
             f"| critical flags | {len(critical)} |",
             f"| fixtures they come from | {len({f.fixture for f in critical})} |",
             f"| distinct critical cases (fixture and critical paths) | {len(cases)} |",
             "| critical paths in those cases, by category | "
             + ", ".join(f"{c} {n}" for c, n in sorted(by_category.items())) + " |",
             f"| exact 95% upper bound for 0 misses over the flags | {zero_upper(len(critical)):.3f} |",
             f"| exact 95% upper bound for 0 misses over the distinct cases | {zero_upper(len(cases)):.3f} |"]
    by_id = {f.id: f for f in data}
    answers = runner.load(runner.store_path(LAB / "triage_study", "gpt-oss-120b", "blind"))
    false = [by_id[r["flag"]] for r in answers if by_id[r["flag"]].scored and not by_id[r["flag"]].critical
             and r["decision"]["action"] == "escalate"]
    top, count = Counter(f.fixture for f in false).most_common(1)[0] if false else ("none", 0)
    lines += [f"| gpt-oss-120b blind false escalations | {len(false)} |",
              f"| of those, in the fixture with the most ({top}) | {count} |"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    text = table()
    if argv[:1] == ["--check"]:
        doc = FINDINGS.read_text(encoding="utf-8")
        quoted = doc.split(BEGIN)[1].split(END)[0].strip() if BEGIN in doc and END in doc else None
        if quoted != text:
            print(f"{FINDINGS.name} quotes a different table; regenerate it")
            return 1
        print("the findings quote the table the stored flags give")
        return 0
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
