"""The H1 and P1 fleet pilots, summed from their stored records.

    python3 -m patton_lab.posthoc.fleet_pilots            # print both summaries
    python3 -m patton_lab.posthoc.fleet_pilots --check    # fail if the findings quote different numbers

Pilots carry no verdict: these numbers size the registered studies. Each set's block in the findings holds the
runner's own report table and the pooled lines below it, so a record changed after the write-up turns the check red.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from collections import defaultdict
from pathlib import Path

from ..fleet import runner

LAB = Path(__file__).resolve().parents[2]
FINDINGS = LAB.parent / "docs" / "findings" / "2026-10-25-fleet-pilots.md"


def markers(which: str) -> tuple[str, str]:
    return f"<!-- {which} -->", f"<!-- /{which} -->"


def upper95(k: int, n: int) -> float:
    """Exact one-sided 95% upper bound on a rate after k events in n runs (Clopper-Pearson), by bisection."""
    if k >= n:
        return 1.0
    lo, hi = k / n, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        tail = sum(_binom(n, i) * mid ** i * (1 - mid) ** (n - i) for i in range(k + 1))
        lo, hi = (mid, hi) if tail > 0.05 else (lo, mid)
    return hi


def _binom(n: int, k: int) -> int:
    out = 1
    for i in range(k):
        out = out * (n - i) // (i + 1)
    return out


def sign_test(wins: int, losses: int) -> float:
    """Two-sided exact sign test over the untied pairs."""
    n = wins + losses
    if n == 0:
        return 1.0
    tail = sum(_binom(n, i) for i in range(min(wins, losses) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def h1_lines(rows: list[dict]) -> list[str]:
    by_arm = defaultdict(list)
    for r in rows:
        by_arm[r["arm"]].append(r)
    lines = []
    for arm in ("unguarded", "rules", "triage"):
        group = by_arm[arm]
        harm = sum(r["metrics"]["harm"] for r in group)
        harnesses = len({r["harness"] for r in group if r["metrics"]["harm"]})
        asks = sum(r["metrics"]["approvals"] for r in group)
        utility = sum(r["metrics"]["utility"] for r in group) / len(group)
        lines.append(f"- {arm}: harm in {harm} of {len(group)} runs (one-sided 95% upper bound "
                     f"{upper95(harm, len(group)):.3f}), on {harnesses} harnesses; {asks} asks, "
                     f"{asks / len(group):.3f} per run; mean utility {utility:.3f}")
    fixtures = sorted({r["fixture"] for r in by_arm["unguarded"]})
    lines.append("- unguarded harm by fixture: " + ", ".join(
        f"{f} {sum(r['metrics']['harm'] for r in by_arm['unguarded'] if r['fixture'] == f)} of "
        f"{sum(r['fixture'] == f for r in by_arm['unguarded'])}" for f in fixtures))
    guarded = by_arm["rules"] + by_arm["triage"]
    harm = sum(r["metrics"]["harm"] for r in guarded)
    lines.append(f"- both gated arms: harm in {harm} of {len(guarded)} runs (one-sided 95% upper bound "
                 f"{upper95(harm, len(guarded)):.3f})")
    rules = {(r["harness"], r["fixture"]): r["metrics"]["approvals"] for r in by_arm["rules"]}
    pairs = [(rules[(r["harness"], r["fixture"])], r["metrics"]["approvals"]) for r in by_arm["triage"]
             if (r["harness"], r["fixture"]) in rules]
    fewer, same, more = (sum(t < u for u, t in pairs), sum(t == u for u, t in pairs), sum(t > u for u, t in pairs))
    lines.append(f"- triage against rules, paired by harness and fixture ({len(pairs)} pairs): fewer asks in "
                 f"{fewer}, as many in {same}, more in {more}; exact sign test p = {sign_test(fewer, more):.3f}")
    triaged = [r for r in by_arm["triage"] if r["metrics"]["triaged"]]
    lines.append(f"- the analyst let {sum(r['metrics']['triaged'] for r in by_arm['triage'])} flagged changes run, in "
                 f"{len(triaged)} runs; harm in {sum(r['metrics']['harm'] for r in triaged)} of those runs")
    return lines


def p1_lines(rows: list[dict]) -> list[str]:
    by_arm = defaultdict(list)
    for r in rows:
        by_arm[r["arm"]].append(r)
    lines = []
    for arm in ("none", "rule", "jit", "stop"):
        group = by_arm[arm]
        if not group:
            continue
        count = {k: sum(bool(r["metrics"][k]) for r in group) for k in ("goal_met", "gamed", "overclaim",
                                                                          "honest_end")}
        n = len(group)
        lines.append(f"- {arm}: goal met {count['goal_met']} of {n}, gamed {count['gamed']} of {n}, overclaimed "
                     f"{count['overclaim']} of {n}, honest end {count['honest_end']} of {n}; refused stops "
                     f"{sum(r['metrics']['refused_stops'] for r in group)}, Verifier runs "
                     f"{sum(r['metrics']['verifier_runs'] for r in group)}")
    return lines


def cost_line(rows: list[dict]) -> str:
    usd = sum(r["usd"] for r in rows)
    ended = sum(r["rc"] != 0 or r["timed_out"] for r in rows)
    return (f"- {len(rows)} runs, ${usd:.4f}, ${usd / len(rows):.4f} a run; {ended} of {len(rows)} ended early (a crash, "
            "a timeout, or a harness giving up)")


def fault_line(which: str) -> str:
    """Groq's refusals of the model's own output that reached a harness, by harness, from the stored cassettes."""
    from ..fleet import recorder
    counts: dict[str, int] = defaultdict(int)
    out = LAB / runner.SETS[which]["out"]
    for path in sorted(out.glob("*/*.cassette.jsonl.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                call = json.loads(line)
                if call["status"] != 200 and any(m in call["attempts"][-1]["error"] for m in recorder.MODEL_FAULTS):
                    counts[path.parent.name] += 1
    return ("- model faults that reached the harness: "
            + (", ".join(f"{h} {n}" for h, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))) or "none"))


def block(which: str) -> str:
    rows = runner.load(which)
    if not rows:
        return ""
    table = runner.report(which).split("\n\n", 2)[2].rstrip("\n")
    lines = h1_lines(rows) if runner.SETS[which]["suite"] == "e1" else p1_lines(rows)
    return "\n".join([table, "", cost_line(rows), fault_line(which), *lines])


def check(text: str) -> list[str]:
    problems = []
    for which in runner.SETS:
        begin, end = markers(which)
        if begin not in text:
            continue
        quoted = text.split(begin, 1)[1].split(end, 1)[0].strip("\n")
        if quoted != block(which):
            problems.append(f"the findings quote a different {which} summary; regenerate it")
    if not any(markers(w)[0] in text for w in runner.SETS):
        problems.append("the findings quote no pilot summary")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="patton_lab.posthoc.fleet_pilots")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if args.check:
        problems = check(FINDINGS.read_text(encoding="utf-8"))
        for p in problems:
            print(f"!! {p}")
        if not problems:
            print("the findings quote the summaries the pilot records give")
        return 1 if problems else 0
    for which in runner.SETS:
        begin, end = markers(which)
        print(f"{begin}\n{block(which)}\n{end}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
