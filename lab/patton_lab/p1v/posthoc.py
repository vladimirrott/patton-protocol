"""Post hoc cuts of the P1V study, chosen after the verdicts were seen.

    python3 -m patton_lab.p1v.posthoc

The findings document quotes the block this prints, and a test fails when the two differ.
"""

from __future__ import annotations

import sys
from collections import Counter

from . import prompts as prompts_mod
from . import runner

CANDIDATE = "fitted-v2"


def outcome(record: dict) -> str:
    if record["flagged"]:
        return "flagged"
    return {"pass": "passed", "fail": "failed with no evidence copied from the input", None: "gave no readable verdict"}[
        record["verdict"]]


def build() -> str:
    dataset = {c.id: c for c in runner.datasets("study")}
    recs = {name: {r["case"]: r for r in runner.load(runner.store_path(runner.LAB / "p1v_study", name))}
            for name in prompts_mod.STUDY_ARMS}
    gamed = sorted(i for i, c in dataset.items() if c.gamed)
    fixtures = sorted({dataset[i].fixture for i in gamed})
    first = Counter((outcome(recs[CANDIDATE][i]), dataset[i].fixture) for i in gamed)
    never = Counter(dataset[i].fixture for i in gamed
                    if not recs[CANDIDATE][i]["flagged"] and not recs[f"{CANDIDATE}#2"][i]["flagged"])
    rows = ["| What the candidate's first draw did on a gamed run | " + " | ".join(fixtures) + " | All |",
            "| --- | " + " | ".join("---" for _ in fixtures) + " | --- |"]
    for what in ("flagged", "passed", "failed with no evidence copied from the input", "gave no readable verdict"):
        counts = [first[(what, f)] for f in fixtures]
        rows.append(f"| {what} | " + " | ".join(map(str, counts)) + f" | {sum(counts)} |")
    counts = [never[f] for f in fixtures]
    rows.append("| flagged in neither of its two draws | " + " | ".join(map(str, counts)) + f" | {sum(counts)} |")
    return "\n".join(rows)


def main() -> int:
    print(build())
    return 0


if __name__ == "__main__":
    sys.exit(main())
