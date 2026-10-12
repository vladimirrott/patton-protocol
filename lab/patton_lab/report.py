"""Build lab/REPORT.md from the results files.

Every number the report prints goes through `Numbers.fmt`, which keeps the
printed string. A test builds a report from synthetic results and checks that
every decimal in the markdown is one of those strings, so no number in the
report can be typed by hand.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from . import stats
from .experiments import EXPERIMENTS, SPEND_ORDER
from .prereg import CEILING_FLOOR, PREDICTIONS, SESOI, Prediction

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.validate_report import report_problems  # noqa: E402

BASE_RATES = (0.10, 0.25, 0.50)
REPORT_ARMS = ("B", "C", "D", "E", "F", "G")


class Numbers:
    def __init__(self) -> None:
        self.printed: list[str] = []

    def fmt(self, value: float | None, digits: int = 3) -> str:
        if value is None:
            return "n/a"
        text = f"{value:.{digits}f}"
        self.printed.append(text)
        return text

    def count(self, value: int) -> str:
        text = str(int(value))
        self.printed.append(text)
        return text


def load(root: Path) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for name in list(SPEND_ORDER) + ["SMOKE"]:
        path = root / "results" / f"{name}.jsonl"
        if path.exists():
            out[name] = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return out


def _value(record: dict[str, Any], metric: str) -> float | None:
    value = record.get("metrics", {}).get(metric)
    if value is None:
        return None
    return float(value)


def _case_filter(record: dict[str, Any], case_filter: str) -> bool:
    metrics = record.get("metrics", {})
    if not case_filter:
        return True
    if case_filter == "mutated":
        return metrics.get("kind") not in (None, "none")
    return metrics.get("variant") == case_filter or metrics.get("kind") == case_filter


def rows(data: dict[str, list[dict[str, Any]]], exp: str, arm: str, metric: str, case_filter: str = "",
         engaged_only: bool = False) -> list[dict[str, Any]]:
    out = []
    for record in data.get(exp, []):
        if record["arm"] != arm or not _case_filter(record, case_filter):
            continue
        if engaged_only and not record.get("metrics", {}).get("engaged", True):
            continue
        value = _value(record, metric)
        if value is not None:
            out.append({"case": record["case"], "arm": arm, metric: value})
    return out


def contrast(data, p: Prediction, engaged_only: bool = False) -> dict[str, Any] | None:
    a = stats.fixture_means(rows(data, p.exp_a, p.arm_a, p.metric, p.case_filter, engaged_only), p.arm_a, p.metric)
    b = stats.fixture_means(rows(data, p.exp_b, p.arm_b, p.metric, p.case_filter, engaged_only), p.arm_b, p.metric)
    common = sorted(set(a) & set(b))
    if not common:
        return None
    diffs = stats.paired_diffs({c: a[c] for c in common}, {c: b[c] for c in common})
    mean = sum(diffs) / len(diffs)
    ci90 = stats.bootstrap_ci(diffs, alpha=0.10)
    return {
        "n": len(common),
        "a": sum(a[c] for c in common) / len(common),
        "b": sum(b[c] for c in common) / len(common),
        "point": mean,
        "ci90": ci90,
        "lower": stats.one_sided_lower(diffs, 0.05),
        "upper": stats.one_sided_upper(diffs, 0.05),
        "sign_p": stats.sign_test_p(diffs),
    }


def predictions_section(data, n: Numbers) -> list[str]:
    lines = ["## Pre-registered predictions", "",
             "| ID | Prediction | n | A | B | A − B | 90% CI | one-sided 95% | Verdict |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for p in PREDICTIONS:
        if p.kind == "exact_zero":
            values = [r[p.metric] for r in rows(data, p.exp_a, p.arm_a, p.metric)]
            if not values:
                lines.append(f"| {p.id} | {p.text} | 0 | | | | | | not run |")
                continue
            verdict = "SUPPORTED" if all(v == 0 for v in values) else "NOT SUPPORTED"
            lines.append(f"| {p.id} | {p.text} | {n.count(len(values))} | {n.fmt(sum(values) / len(values))} | | | | | {verdict} |")
            continue
        result = contrast(data, p)
        if result is None:
            lines.append(f"| {p.id} | {p.text} | 0 | | | | | | not run |")
            continue
        if p.kind == "non_inferiority":
            verdict = stats.non_inferiority(result["upper"], SESOI)
            bound = n.fmt(result["upper"])
        else:
            verdict = stats.verdict(result["point"], result["lower"], result["ci90"], p.threshold)
            bound = n.fmt(result["lower"])
        flag = stats.flag_ceiling_floor((result["a"] + result["b"]) / 2, *CEILING_FLOOR)
        if flag:
            verdict = f"{flag} (no verdict)"
        lines.append(
            f"| {p.id} | {p.text} | {n.count(result['n'])} | {n.fmt(result['a'])} | {n.fmt(result['b'])} | "
            f"{n.fmt(result['point'])} | [{n.fmt(result['ci90'][0])}, {n.fmt(result['ci90'][1])}] | {bound} | {verdict} |"
        )
    return lines + [""]


def null_check(data, n: Numbers) -> list[str]:
    lines = ["## Null check (A − A2)", ""]
    for metric in ("harm", "utility"):
        a = stats.fixture_means(rows(data, "E1", "A", metric), "A", metric)
        b = stats.fixture_means(rows(data, "E1", "A2", metric), "A2", metric)
        common = sorted(set(a) & set(b))
        if not common:
            lines.append(f"- {metric}: not run")
            continue
        diffs = stats.paired_diffs({c: a[c] for c in common}, {c: b[c] for c in common})
        low, high = stats.bootstrap_ci(diffs, alpha=0.05)
        holds = low <= 0 <= high
        lines.append(f"- {metric}: mean {n.fmt(sum(diffs) / len(diffs))}, 95% CI [{n.fmt(low)}, {n.fmt(high)}], "
                     f"{'contains 0' if holds else 'EXCLUDES 0: the instrument drifted; E1 verdicts are withheld'}")
    return lines + [""]


def e1_arms_section(data, n: Numbers, exp: str = "E1") -> list[str]:
    records = data.get(exp, [])
    if not records:
        return []
    lines = [f"## {exp} by arm", "",
             "| Arm | Runs | Harm | Utility | No-tool runs | Unneeded halts per run | Runs that read /protocol | Report valid |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for arm in sorted({r["arm"] for r in records}):
        mine = [r for r in records if r["arm"] == arm]
        harm = [r["metrics"]["harm"] for r in mine]
        utility = [r["metrics"]["utility"] for r in mine]
        idle = sum(1 for r in mine if not r["metrics"]["engaged"])
        halts = sum(r["metrics"]["unneeded_halts"] for r in mine) / len(mine)
        reads = sum(1 for r in mine if r.get("protocol_reads"))
        valid = sum(1 for r in mine if not report_problems(r.get("final_text", "")))
        lines.append(f"| {arm} | {n.count(len(mine))} | {n.fmt(sum(harm) / len(harm))} | {n.fmt(sum(utility) / len(utility))} | "
                     f"{n.count(idle)} | {n.fmt(halts)} | {n.count(reads)} | {n.count(valid)} |")
    return lines + [""]


def e2_section(data, n: Numbers) -> list[str]:
    records = data.get("E2", []) + data.get("E2T", [])
    if not records:
        return []
    share = sum(1 for r in records if r["metrics"]["variant"] != "correct") / len(records)
    lines = ["## E2 Verifier as detector", "",
             "Positives are the defective candidates of a class; negatives are the correct candidates, judged on the "
             f"same verdict field. Precision is shown at stated base rates because the study's own defective share "
             f"({n.fmt(share, 2)}) inflates it.", "",
             "| Arm | Class | TPR | FPR | MCC | Youden J | Precision at " + " / ".join(n.fmt(b, 2) for b in BASE_RATES) + " |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    fields = {"bug": "code_correct", "lie": "scope_respected", "stale": "digest_matches"}
    for arm in sorted({r["arm"] for r in records}):
        mine = [r for r in records if r["arm"] == arm]
        for cls, field in fields.items():
            pos = [r for r in mine if r["metrics"]["variant"] == cls]
            neg = [r for r in mine if r["metrics"]["variant"] == "correct"]
            if not pos or not neg:
                continue
            tp = sum(1 for r in pos if r["metrics"]["flagged"][field])
            fp = sum(1 for r in neg if r["metrics"]["flagged"][field])
            tpr, fpr = tp / len(pos), fp / len(neg)
            precisions = " / ".join(n.fmt(stats.precision_at(b, tpr, fpr)) for b in BASE_RATES)
            lines.append(f"| {arm} | {cls} | {n.fmt(tpr)} | {n.fmt(fpr)} | "
                         f"{n.fmt(stats.mcc(tp, fp, len(pos) - tp, len(neg) - fp))} | {n.fmt(stats.youden_j(tpr, fpr))} | "
                         f"{precisions} |")
    return lines + [""]


def simple_section(data, n: Numbers, exp: str, title: str, metrics: tuple[str, ...]) -> list[str]:
    records = data.get(exp, [])
    if not records:
        return []
    lines = [f"## {title}", "", "| Arm | Runs | " + " | ".join(metrics) + " |",
             "| --- | --- | " + " | ".join("---" for _ in metrics) + " |"]
    for arm in sorted({r["arm"] for r in records}):
        mine = [r for r in records if r["arm"] == arm]
        cells = []
        for metric in metrics:
            values = [float(r["metrics"][metric]) for r in mine if r["metrics"].get(metric) is not None]
            cells.append(n.fmt(sum(values) / len(values)) if values else "n/a")
        lines.append(f"| {arm} | {n.count(len(mine))} | " + " | ".join(cells) + " |")
    return lines + [""]


def build(root: Path) -> tuple[str, Numbers]:
    data = load(root)
    n = Numbers()
    models = sorted({m for records in data.values() for r in records for m in r.get("models_returned", [])})
    runs = sum(len(v) for v in data.values())
    spent = 0.0
    ledger = root / "results" / "spend.json"
    if ledger.exists():
        spent = sum(json.loads(ledger.read_text(encoding="utf-8"))["spent"].values())
    lines = [
        "# Patton Lab report",
        "",
        f"Models: {', '.join(models) or 'none recorded'}. Runs recorded: {n.count(runs)}. "
        f"Actual spend: ${n.fmt(spent, 4)}. Generated by `python3 -m patton_lab.run report` from `lab/results/`; "
        "do not edit by hand. Pre-registration: `lab/prereg/2026-10-12-lab-preregistration.md`.",
        "",
    ]
    lines += predictions_section(data, n)
    lines += null_check(data, n)
    lines += e1_arms_section(data, n, "E1")
    lines += e1_arms_section(data, n, "E7G")
    lines += e2_section(data, n)
    lines += simple_section(data, n, "E3", "E3 stale evidence, no tool", ("correct", "false_accept", "false_reject"))
    lines += simple_section(data, n, "E3T", "E3 stale evidence, digest tool", ("correct", "false_accept", "false_reject"))
    lines += simple_section(data, n, "E4", "E4 delegation (exploratory)",
                            ("delegated", "over_delegation", "overlap_violation", "out_of_boundary_writes", "hidden_pass"))
    lines += simple_section(data, n, "E7S", "E7 budget and overlap scenarios (exploratory)",
                            ("overrun", "fabrication", "honest_partial", "lost_updates", "reported_blocked"))
    lines += e1_arms_section(data, n, "E1E")
    lines += e1_arms_section(data, n, "E1R")
    lines += e1_arms_section(data, n, "E1H")
    return "\n".join(lines) + "\n", n


def write_report(root: Path) -> int:
    text, _ = build(root)
    path = root / "REPORT.md"
    path.write_text(text, encoding="utf-8")
    print(f"wrote {path}")
    return 0
