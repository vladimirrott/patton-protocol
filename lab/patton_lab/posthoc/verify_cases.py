"""Post hoc, after the Verifier replay: where its misses and its false flags sit.

    python3 -m patton_lab.posthoc.verify_cases            # print the table
    python3 -m patton_lab.posthoc.verify_cases --check    # fail if the findings quote a different table

Chosen after the results were seen, so it carries no verdict. For each model it counts the harmful runs by the
category of the files they lost (a run that lost files of two categories counts under each), how many of those the
Verifier flagged, and where its flags on clean runs sit.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

from ..fixtures import e1
from ..verify import cases as cases_mod
from ..verify import runner

LAB = Path(__file__).resolve().parents[2]
FINDINGS = LAB.parent / "docs" / "findings" / "2026-10-25-verify-findings.md"
BEGIN, END = "<!-- cases -->", "<!-- /cases -->"


def lost_categories(case) -> set[str]:
    fixture = e1.fixture_by_id(case.fixture)
    group_of = {p: g.category for g in fixture.groups for p in g.paths}
    return {group_of[p] for p, text in fixture.files.items()
            if case.after.get(p) != text and group_of.get(p) in e1.HARM_CATEGORIES}


def table() -> str:
    by_id = {c.id: c for c in cases_mod.collect(cases_mod.TEST_SOURCES)}
    lines = ["| Model | Count | Value |", "| --- | --- | --- |"]
    for model in runner.MODELS:
        records = runner.load(runner.store_path(LAB / "verify_study", model))
        harmful = [r for r in records if r["kind"] == "harmful"]
        seen, hit = Counter(), Counter()
        for record in harmful:
            for category in lost_categories(by_id[record["case"]]):
                seen[category] += 1
                hit[category] += record["verifier_decision"] == "block"
        for category in sorted(seen):
            lines.append(f"| {model} | harmful runs that lost {category} files, flagged | "
                         f"{hit[category]} of {seen[category]} |")
        missed = Counter(r["fixture"] for r in harmful if r["verifier_decision"] != "block")
        top, count = missed.most_common(1)[0]
        lines.append(f"| {model} | missed harmful runs in the fixture with the most ({top}) | "
                     f"{count} of {len(harmful) - sum(r['verifier_decision'] == 'block' for r in harmful)} |")
        flags = Counter(r["fixture"] for r in records if r["kind"] == "clean" and r["verifier_decision"] == "block")
        top, count = flags.most_common(1)[0]
        lines.append(f"| {model} | clean runs flagged in the fixture with the most ({top}) | "
                     f"{count} of {sum(flags.values())} |")
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
        print("the findings quote the table the stored answers give")
        return 0
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
