"""Small deterministic statistics helpers with no third-party dependencies."""

from __future__ import annotations

import math
from typing import Iterable, List, Sequence, Tuple


Number = int | float


def _values(data: Iterable[Number]) -> List[float]:
    values = [float(value) for value in data]
    if not values:
        raise ValueError("statistics require at least one value")
    if not all(math.isfinite(value) for value in values):
        raise ValueError("statistics require finite values")
    return values


def mean(data: Iterable[Number]) -> float:
    values = _values(data)
    return math.fsum(values) / len(values)


def median(data: Iterable[Number]) -> float:
    values = sorted(_values(data))
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2.0


def p50(data: Iterable[Number]) -> float:
    return percentile(data, 50.0)


def percentile(data: Iterable[Number], percent: Number) -> float:
    """Return a linearly interpolated percentile on the inclusive [0, 100] scale."""

    percentile_value = float(percent)
    if not 0.0 <= percentile_value <= 100.0:
        raise ValueError("percent must be between 0 and 100")
    values = sorted(_values(data))
    if len(values) == 1:
        return values[0]
    rank = (len(values) - 1) * percentile_value / 100.0
    lower = int(math.floor(rank))
    upper = int(math.ceil(rank))
    if lower == upper:
        return values[lower]
    fraction = rank - lower
    return values[lower] + ((values[upper] - values[lower]) * fraction)


def standard_deviation(data: Iterable[Number]) -> float:
    """Return sample standard deviation; a single observation has deviation 0."""

    values = _values(data)
    if len(values) == 1:
        return 0.0
    average = math.fsum(values) / len(values)
    squared = math.fsum((value - average) ** 2 for value in values)
    return math.sqrt(squared / (len(values) - 1))


def ci95(data: Iterable[Number]) -> Tuple[float, float]:
    """Return the normal-approximation 95% confidence interval for the mean."""

    values = _values(data)
    average = math.fsum(values) / len(values)
    if len(values) == 1:
        return (average, average)
    margin = 1.96 * standard_deviation(values) / math.sqrt(len(values))
    return (average - margin, average + margin)


def minimum(data: Iterable[Number]) -> float:
    return min(_values(data))


def maximum(data: Iterable[Number]) -> float:
    return max(_values(data))


def summarize(data: Sequence[Number]) -> dict[str, float]:
    """Return the common benchmark summary in one pass-friendly API."""

    values = _values(data)
    lower, upper = ci95(values)
    return {
        "mean": mean(values),
        "median": median(values),
        "p50": p50(values),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
        "standard_deviation": standard_deviation(values),
        "ci95_lower": lower,
        "ci95_upper": upper,
        "min": minimum(values),
        "max": maximum(values),
    }
