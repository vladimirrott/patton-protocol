"""Statistics for the report. The fixture (or case) is the unit of analysis."""

from __future__ import annotations

import math
import random
from typing import Any

BOOTSTRAP_RESAMPLES = 10_000
SEED = 0


def fixture_means(rows: list[dict[str, Any]], arm: str, metric: str) -> dict[str, float]:
    sums: dict[str, list[float]] = {}
    for row in rows:
        if row.get("arm") != arm or row.get(metric) is None:
            continue
        sums.setdefault(row["case"], []).append(float(row[metric]))
    return {case: sum(values) / len(values) for case, values in sorted(sums.items())}


def paired_diffs(a: dict[str, float], b: dict[str, float]) -> list[float]:
    if set(a) != set(b):
        raise ValueError(f"unpaired cases: {sorted(set(a) ^ set(b))}")
    return [a[case] - b[case] for case in sorted(a)]


def _means(values: list[float], resamples: int, seed: int) -> list[float]:
    rng = random.Random(seed)
    n = len(values)
    return sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(resamples))


def _quantile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        raise ValueError("no values")
    position = q * (len(sorted_values) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return sorted_values[low]
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low)


def bootstrap_ci(values: list[float], alpha: float, resamples: int = BOOTSTRAP_RESAMPLES, seed: int = SEED) -> tuple[float, float]:
    """Two-sided percentile interval of the mean, (alpha/2, 1 - alpha/2)."""
    means = _means(values, resamples, seed)
    return _quantile(means, alpha / 2), _quantile(means, 1 - alpha / 2)


def one_sided_lower(values: list[float], alpha: float, resamples: int = BOOTSTRAP_RESAMPLES, seed: int = SEED) -> float:
    return _quantile(_means(values, resamples, seed), alpha)


def one_sided_upper(values: list[float], alpha: float, resamples: int = BOOTSTRAP_RESAMPLES, seed: int = SEED) -> float:
    return _quantile(_means(values, resamples, seed), 1 - alpha)


def sign_test_p(diffs: list[float]) -> float:
    nonzero = [d for d in diffs if d != 0]
    n = len(nonzero)
    if n == 0:
        return 1.0
    k = sum(1 for d in nonzero if d > 0)

    def tail(at_most: int) -> float:
        return sum(math.comb(n, i) for i in range(at_most + 1)) / 2 ** n

    lower = tail(k)
    upper = 1 - tail(k - 1) if k > 0 else 1.0
    return min(1.0, 2 * min(lower, upper))


def mcc(tp: int, fp: int, fn: int, tn: int) -> float:
    denominator = math.sqrt((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn))
    return 0.0 if denominator == 0 else (tp * tn - fp * fn) / denominator


def youden_j(tpr: float, fpr: float) -> float:
    return tpr - fpr


def precision_at(base_rate: float, tpr: float, fpr: float) -> float | None:
    denominator = tpr * base_rate + fpr * (1 - base_rate)
    return None if denominator == 0 else tpr * base_rate / denominator


def verdict(point: float, lower: float, ci90: tuple[float, float], threshold: float) -> str:
    """Pre-registered labels for a prediction that the effect is at least `threshold`.

    SUPPORTED needs the point estimate at the threshold and the one-sided 95%
    lower bound above zero. NOT SUPPORTED needs the point below the threshold
    and the whole 90% interval inside +/- threshold. Anything else is
    INCONCLUSIVE.
    """
    if point >= threshold and lower > 0:
        return "SUPPORTED"
    if point < threshold and -threshold < ci90[0] and ci90[1] < threshold:
        return "NOT SUPPORTED"
    return "INCONCLUSIVE"


def non_inferiority(upper: float, margin: float) -> str:
    return "SUPPORTED" if upper < margin else "INCONCLUSIVE"


def flag_ceiling_floor(rate: float, low: float = 0.05, high: float = 0.95) -> str | None:
    if rate < low:
        return "FLOOR"
    if rate > high:
        return "CEILING"
    return None
