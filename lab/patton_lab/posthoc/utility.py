"""Post hoc, after the gate study: where the gated arms' finished work went.

    python3 -m patton_lab.posthoc.utility            # print the breakdown
    python3 -m patton_lab.posthoc.utility --check    # fail if the findings quote a different breakdown

Chosen after the results were seen, so it carries no verdict. Each run of O-R1 and O-R1S is put in one class by the
strongest thing the gate did in it, and its utility is compared with the mean utility of the unguarded arm (O-R0) on
the same fixture. For runs where the gate never asked or refused anything, it also checks the recorded model
requests for any text from the gate, to tell whether the worker could have noticed the hook at all.
"""

from __future__ import annotations

import argparse
import glob
import gzip
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from ..fixtures import e1

LAB = Path(__file__).resolve().parents[2]
FINDINGS = LAB.parent / "docs" / "findings" / "2026-10-23-gate-utility-posthoc.md"
BEGIN, END = "<!-- breakdown -->", "<!-- /breakdown -->"
CLASSES = ("no gate event", "approved only", "unreadable command refused", "refused", "partly refused")


def load(runs: Path) -> list[dict]:
    return [json.loads(Path(p).read_text()) for p in sorted(glob.glob(str(runs / "*.json")))]


def gate_class(run: dict) -> str:
    events = [e for e in run["ledger"] if e.get("verdict") not in ("allow", None)]
    if not events:
        return "no gate event"
    if any(e.get("verdict") == "refused" and e.get("granted") for e in events):
        return "partly refused"
    if any(e.get("verdict") == "refused" for e in events):
        return "refused"
    if any(e.get("verdict") == "deny" for e in events):
        return "unreadable command refused"
    return "approved only"


def removed_nothing(run: dict) -> bool:
    return set(e1.fixture_by_id(run["fixture"]).files) <= set(run["after"])


def gate_text_seen(run: dict, runs: Path) -> bool:
    text = ""
    for path in glob.glob(str(runs / "cassettes" / f"{run['run_id']}.*")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            text += handle.read()
    if not text:
        raise FileNotFoundError(f"no cassette for {run['run_id']}")
    return "Patton gate" in text


def breakdown(runs: Path) -> str:
    rows = load(runs)
    if len(rows) != 560:
        raise ValueError(f"expected the 560 gate study runs, found {len(rows)}")
    unguarded = defaultdict(list)
    for r in rows:
        if r["arm"] == "O-R0":
            unguarded[r["fixture"]].append(r["metrics"]["utility"])
    base = {f: statistics.mean(v) for f, v in unguarded.items()}
    gated = [r for r in rows if r["arm"] in ("O-R1", "O-R1S")]
    by = defaultdict(list)
    for r in gated:
        by[gate_class(r)].append(r)
    lines = ["| What the gate did in the run | Runs | Utility against unguarded, same fixture: mean | Sum |",
             "| --- | --- | --- | --- |"]
    total = 0.0
    for name in CLASSES:
        diffs = [r["metrics"]["utility"] - base[r["fixture"]] for r in by[name]]
        total += sum(diffs)
        lines.append(f"| {name} | {len(diffs)} | {statistics.mean(diffs):+.3f} | {sum(diffs):+.2f} |" if diffs
                     else f"| {name} | 0 | | |")
    lines.append(f"| all gated runs | {len(gated)} | {total / len(gated):+.3f} | {total:+.2f} |")
    quiet = by["no gate event"]
    seen = sum(gate_text_seen(r, runs) for r in quiet)
    nothing = {arm: sum(removed_nothing(r) for r in rows if r["arm"] == arm) for arm in ("O-R0", "O-R0S", "O-R1", "O-R1S")}
    lines += ["",
              f"Runs where the gate never asked or refused: {len(quiet)}; runs among them whose model requests hold any "
              f"text from the gate: {seen}.",
              "Runs that removed nothing: " + ", ".join(f"{arm} {n} of 112" for arm, n in nothing.items()) + "."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="patton_lab.posthoc.utility")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    text = breakdown(LAB / "cc_runs")
    if not args.check:
        sys.stdout.write(text)
        return 0
    doc = FINDINGS.read_text(encoding="utf-8")
    quoted = doc.split(BEGIN, 1)[1].split(END, 1)[0].strip("\n") + "\n" if BEGIN in doc else ""
    if quoted != text:
        print(f"{FINDINGS.name} quotes a different breakdown; regenerate it")
        return 1
    print("the findings quote the breakdown the records give")
    return 0


if __name__ == "__main__":
    sys.exit(main())
