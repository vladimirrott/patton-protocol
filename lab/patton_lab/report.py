"""Build lab/REPORT.md from the results files, applying the pre-registered rules.

Every number the report prints goes through `Numbers.fmt` or `Numbers.count`,
which keep the printed string. A test builds a report from synthetic results
and checks that every decimal in the markdown is one of those strings, so no
number in the report can be typed by hand.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from . import stats
from .experiments import SPEND_ORDER
from .fixtures import e1, e2, e3
from .prereg import CEILING_FLOOR, E1_PREDICTIONS, PREDICTIONS, SESOI, Prediction
from .score import report_status

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.validate_report import report_problems  # noqa: E402

BASE_RATES = (0.10, 0.25, 0.50)
CLASS_FIELD = {"bug": "code_correct", "lie": "scope_respected", "stale": "digest_matches"}


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


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


# ---- units -----------------------------------------------------------------------


def unit_values(data, exp: str, arm: str, metric: str, engaged_only: bool = False) -> dict[str, float]:
    """One value per unit of analysis for this arm: fixture, task, or repository."""
    records = [r for r in data.get(exp, []) if r["arm"] == arm]
    if engaged_only:
        records = [r for r in records if r.get("metrics", {}).get("engaged", True)]
    if metric in ("harm", "utility"):
        by_case: dict[str, list[float]] = {}
        for record in records:
            by_case.setdefault(record["case"], []).append(float(record["metrics"][metric]))
        return {case: sum(v) / len(v) for case, v in sorted(by_case.items())}
    if metric.startswith("task_j:"):
        cls = metric.split(":", 1)[1]
        field = CLASS_FIELD[cls]
        flags: dict[tuple[str, str], list[float]] = {}
        for record in records:
            m = record["metrics"]
            if not m.get("parsed"):
                continue
            task = record["case"].rsplit("-", 1)[0]
            flags.setdefault((task, m["variant"]), []).append(1.0 if m["flagged"][field] else 0.0)
        out = {}
        for task in sorted({t for t, _ in flags}):
            defect, correct = flags.get((task, cls)), flags.get((task, "correct"))
            if defect and correct:
                out[task] = sum(defect) / len(defect) - sum(correct) / len(correct)
        return out
    if metric == "repo_j":
        rejects: dict[tuple[str, bool], list[float]] = {}
        for record in records:
            m = record["metrics"]
            if m.get("reject") is None:
                continue
            repo = _repo_of(record["case"])
            rejects.setdefault((repo, m["kind"] != "none"), []).append(float(m["reject"]))
        out = {}
        for repo in sorted({r for r, _ in rejects}):
            mutated, control = rejects.get((repo, True)), rejects.get((repo, False))
            if mutated and control:
                out[repo] = sum(mutated) / len(mutated) - sum(control) / len(control)
        return out
    raise KeyError(metric)


def _repo_of(case_id: str) -> str:
    for kind in e3.KINDS:
        if case_id.endswith("-" + kind):
            return case_id[: -len(kind) - 1]
    raise ValueError(f"cannot find the repository of {case_id}")


def expected_units(metric: str, exp: str) -> int:
    if metric in ("harm", "utility"):
        return len(e1.E1_HELDOUT) if exp == "E1H" else len(e1.E1_FIXTURES)
    if metric.startswith("task_j:"):
        return len(e2.TASKS)
    if metric == "repo_j":
        return len(e3.REPOS)
    raise KeyError(metric)


def contrast(data, p: Prediction, engaged_only: bool = False) -> dict[str, Any] | None:
    a = unit_values(data, p.exp_a, p.arm_a, p.metric, engaged_only)
    b = unit_values(data, p.exp_b, p.arm_b, p.metric, engaged_only)
    common = sorted(set(a) & set(b))
    if not common:
        return None
    diffs = stats.paired_diffs({u: a[u] for u in common}, {u: b[u] for u in common})
    out = {
        "n": len(common),
        "n_expected": expected_units(p.metric, p.exp_a),
        "a": sum(a[u] for u in common) / len(common),
        "b": sum(b[u] for u in common) / len(common),
        "point": sum(diffs) / len(diffs),
        "ci90": stats.bootstrap_ci(diffs, alpha=0.10),
        "lower": stats.one_sided_lower(diffs, 0.05),
        "upper": stats.one_sided_upper(diffs, 0.05),
    }
    if p.kind == "non_inferiority":
        out["p"] = stats.signflip_p([SESOI - d for d in diffs])
        out["p_against"] = stats.signflip_p([d - SESOI for d in diffs])
    else:
        out["p"] = stats.signflip_p(diffs)
        out["p_against"] = stats.signflip_p([-d for d in diffs])
    return out


def judge(p: Prediction, result: dict[str, Any], n: Numbers) -> str:
    if result["n"] < result["n_expected"]:
        return f"INCOMPLETE ({result['n']} of {result['n_expected']} units)"
    if p.kind == "non_inferiority":
        if result["point"] >= SESOI and result["p_against"] < stats.ALPHA:
            return "NOT SUPPORTED (inferior)"
        return stats.non_inferiority(result["upper"], result["p"], SESOI)
    if p.metric in ("harm", "utility"):
        flag = stats.flag_ceiling_floor((result["a"] + result["b"]) / 2, *CEILING_FLOOR)
        if flag:
            return f"{flag} (no verdict)"
    label = stats.verdict(result["point"], result["p"], result["ci90"], p.threshold, SESOI)
    if label == "INCONCLUSIVE":
        if result["point"] <= -p.threshold and result["p_against"] < stats.ALPHA:
            return "OPPOSITE (a clear effect in the other direction)"
        width = result["ci90"][1] - result["ci90"][0]
        return f"INCONCLUSIVE (90% CI width {n.fmt(width)})"
    return label


# ---- sections ----------------------------------------------------------------------


def null_check(data, n: Numbers) -> tuple[list[str], bool]:
    lines = ["## Null check (A − A2)", ""]
    drifted = False
    ran = False
    for metric in ("harm", "utility"):
        a = unit_values(data, "E1", "A", metric)
        b = unit_values(data, "E1", "A2", metric)
        common = sorted(set(a) & set(b))
        if not common:
            lines.append(f"- {metric}: not run")
            continue
        ran = True
        diffs = stats.paired_diffs({c: a[c] for c in common}, {c: b[c] for c in common})
        low, high = stats.bootstrap_ci(diffs, alpha=0.05)
        holds = low <= 0 <= high
        drifted = drifted or not holds
        lines.append(f"- {metric}: mean {n.fmt(sum(diffs) / len(diffs))}, 95% CI [{n.fmt(low)}, {n.fmt(high)}], "
                     f"{'contains 0' if holds else 'EXCLUDES 0'}")
    if ran:
        lines.append("")
        lines.append("The instrument drifted between identical arms: every E1 verdict is WITHHELD."
                     if drifted else "Identical arms agree; E1 verdicts stand.")
    return lines + [""], drifted


def predictions_section(data, n: Numbers, drifted: bool, engaged_only: bool = False) -> list[str]:
    title = "## Pre-registered predictions" + (" (runs with at least one tool call)" if engaged_only else "")
    lines = [title, "",
             "| ID | Prediction | Units | A | B | A − B | 90% CI | One-sided 95% bound | Sign-flip p | Verdict |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for p in PREDICTIONS:
        if engaged_only and p.id not in E1_PREDICTIONS:
            continue
        if p.kind == "exact_zero":
            values = [float(r["metrics"][p.metric]) for r in data.get(p.exp_a, []) if r["arm"] == p.arm_a]
            if not values:
                lines.append(f"| {p.id} | {p.text} | 0 | | | | | | | not run |")
                continue
            verdict = "SUPPORTED" if all(v == 0 for v in values) else "NOT SUPPORTED"
            lines.append(f"| {p.id} | {p.text} | {n.count(len(values))} runs | {n.fmt(sum(values) / len(values))} "
                         f"| | | | | | {verdict} |")
            continue
        result = contrast(data, p, engaged_only)
        if result is None:
            lines.append(f"| {p.id} | {p.text} | 0 | | | | | | | not run |")
            continue
        verdict = judge(p, result, n)
        if drifted and p.id in E1_PREDICTIONS:
            verdict = f"WITHHELD (null check; would read {verdict})"
        bound = result["upper"] if p.kind == "non_inferiority" else result["lower"]
        units = f"{n.count(result['n'])} of {n.count(result['n_expected'])}"
        lines.append(
            f"| {p.id} | {p.text} | {units} | {n.fmt(result['a'])} | {n.fmt(result['b'])} | {n.fmt(result['point'])} "
            f"| [{n.fmt(result['ci90'][0])}, {n.fmt(result['ci90'][1])}] | {n.fmt(bound)} | {n.fmt(result['p'], 4)} "
            f"| {verdict} |"
        )
    return lines + [""]


def e1_arms_section(data, n: Numbers, exp: str) -> list[str]:
    records = data.get(exp, [])
    if not records:
        return []
    lines = [f"## {exp} by arm", "",
             "| Arm | Runs | Harm | Utility | No tool call | Turn cap | Cut at max_tokens | Malformed tool calls "
             "| Approval requests per run | Unneeded halts per run | Read a reference file (SKILL.md is already in the prompt) | Report valid | Report names its halts |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for arm in sorted({r["arm"] for r in records}):
        mine = [r for r in records if r["arm"] == arm]
        m = [r["metrics"] for r in mine]
        lines.append(
            f"| {arm} | {n.count(len(mine))} | {n.fmt(_mean([x['harm'] for x in m]))} | {n.fmt(_mean([x['utility'] for x in m]))} "
            f"| {n.count(sum(1 for x in m if not x['engaged']))} | {n.count(sum(1 for r in mine if r['stop_reason'] == 'turn_cap'))} "
            f"| {n.count(sum(1 for r in mine if r['stop_reason'] == 'length'))} "
            f"| {n.count(sum(r.get('tool_use_failures', 0) for r in mine))} "
            f"| {n.fmt(_mean([float(x.get('approval_requests', 0)) for x in m]))} "
            f"| {n.fmt(_mean([float(x['unneeded_halts']) for x in m]))} | {n.count(sum(1 for r in mine if r.get('protocol_reads')))} "
            f"| {n.count(sum(1 for r in mine if not report_problems(r.get('final_text', ''))))} "
            f"| {n.count(sum(1 for x in m if x.get('report_consistent', True)))} |"
        )
    return lines + [""]


def e2_section(data, n: Numbers) -> list[str]:
    records = data.get("E2", []) + data.get("E2T", [])
    if not records:
        return []
    share = sum(1 for r in records if r["metrics"]["variant"] != "correct") / len(records)
    lines = ["## E2 Verifier as detector", "",
             "Positives are the defective candidates of a class; negatives are the correct candidates, judged on the "
             f"same verdict field. The design's defective share is {n.fmt(share, 2)}, which inflates precision, so "
             "precision is shown at stated base rates.", "",
             "| Arm | Class | TPR | FPR | MCC | Youden J | Unparsed | Precision at " + " / ".join(n.fmt(b, 2) for b in BASE_RATES) + " |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for arm in sorted({r["arm"] for r in records}):
        mine = [r for r in records if r["arm"] == arm]
        unparsed = sum(1 for r in mine if not r["metrics"]["parsed"])
        for cls, field in CLASS_FIELD.items():
            pos = [r for r in mine if r["metrics"]["variant"] == cls]
            neg = [r for r in mine if r["metrics"]["variant"] == "correct"]
            if not pos or not neg:
                continue
            tp = sum(1 for r in pos if r["metrics"]["flagged"][field])
            fp = sum(1 for r in neg if r["metrics"]["flagged"][field])
            tpr, fpr = tp / len(pos), fp / len(neg)
            precisions = " / ".join(n.fmt(stats.precision_at(b, tpr, fpr)) for b in BASE_RATES)
            lines.append(f"| {arm} | {cls} | {n.fmt(tpr)} | {n.fmt(fpr)} | {n.fmt(stats.mcc(tp, fp, len(pos) - tp, len(neg) - fp))} "
                         f"| {n.fmt(stats.youden_j(tpr, fpr))} | {n.count(unparsed)} | {precisions} |")
    return lines + [""]


def e3_section(data, n: Numbers) -> list[str]:
    rows = []
    for exp in ("E3", "E3T"):
        for arm in sorted({r["arm"] for r in data.get(exp, [])}):
            mine = [r["metrics"] for r in data[exp] if r["arm"] == arm]
            mutated = [m for m in mine if m["kind"] != "none" and m.get("reject") is not None]
            control = [m for m in mine if m["kind"] == "none" and m.get("reject") is not None]
            unparsed = sum(1 for m in mine if m.get("reject") is None)
            rr_mut = _mean([float(m["reject"]) for m in mutated])
            rr_ctl = _mean([float(m["reject"]) for m in control])
            j = None if rr_mut is None or rr_ctl is None else rr_mut - rr_ctl
            rows.append(f"| {exp} | {arm} | {n.count(len(mine))} | {n.fmt(rr_mut)} | {n.fmt(rr_ctl)} | {n.fmt(j)} | {n.count(unparsed)} |")
    if not rows:
        return []
    return ["## E3 stale evidence", "",
            "| Experiment | Arm | Runs | Reject rate, mutated | Reject rate, control | Youden J | Unparsed |",
            "| --- | --- | --- | --- | --- | --- | --- |"] + rows + [""]


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
            values = [float(r["metrics"][metric]) for r in mine if isinstance(r["metrics"].get(metric), (int, float, bool))]
            cells.append(n.fmt(_mean(values)) if values else "n/a")
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
    null_lines, drifted = null_check(data, n)
    lines += predictions_section(data, n, drifted)
    lines += null_lines
    lines += predictions_section(data, n, drifted, engaged_only=True)
    for exp in ("E1", "E7G"):
        lines += e1_arms_section(data, n, exp)
    lines += e2_section(data, n)
    lines += e3_section(data, n)
    lines += simple_section(data, n, "E4", "E4 delegation (exploratory)",
                            ("delegated", "over_delegation", "overlap_violation", "out_of_boundary_writes", "hidden_pass"))
    lines += simple_section(data, n, "E7S", "E7 budget and overlap scenarios (exploratory)",
                            ("overrun", "fabrication", "honest_partial", "both_present", "lost_updates", "reported_blocked"))
    for exp in ("E1E", "E1R", "E1H"):
        lines += e1_arms_section(data, n, exp)
    return "\n".join(lines) + "\n", n


def write_report(root: Path) -> int:
    text, _ = build(root)
    path = root / "REPORT.md"
    path.write_text(text, encoding="utf-8")
    print(f"wrote {path.name}")
    return 0


__all__ = ["build", "write_report", "Numbers", "report_status"]
