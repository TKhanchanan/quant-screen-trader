"""Safe numeric primitives.

Every helper returns ``None`` rather than a sentinel number when the input cannot support
the calculation. Nothing here may return NaN or infinity: the feature models reject them,
and a zero would be indistinguishable from a real measurement.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

BPS = 10_000.0


def finite(value: float | None) -> float | None:
    """Drop NaN and infinity so they can never reach a model or the wire."""
    if value is None or not math.isfinite(value):
        return None
    return value


def safe_div(numerator: float, denominator: float) -> float | None:
    if denominator == 0:
        return None
    return finite(numerator / denominator)


def bps(value: float, reference: float) -> float | None:
    """Relative distance of ``value`` from ``reference`` in basis points."""
    if reference == 0:
        return None
    return finite((value / reference - 1.0) * BPS)


def log_return_bps(current: float, previous: float) -> float | None:
    if current <= 0 or previous <= 0:
        return None
    return finite(math.log(current / previous) * BPS)


def mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return finite(sum(values) / len(values))


def pstdev(values: Sequence[float]) -> float | None:
    """Population standard deviation; a single sample has no dispersion to measure."""
    if len(values) < 2:
        return None
    average = sum(values) / len(values)
    variance = sum((value - average) ** 2 for value in values) / len(values)
    if variance < 0:
        return None
    return finite(math.sqrt(variance))


def ols_slope(values: Sequence[float]) -> float | None:
    """Least-squares slope of ``values`` against x = 0..N-1, in units per step."""
    count = len(values)
    if count < 2:
        return None
    mean_x = (count - 1) / 2
    mean_y = sum(values) / count
    covariance = sum((index - mean_x) * (value - mean_y) for index, value in enumerate(values))
    variance = sum((index - mean_x) ** 2 for index in range(count))
    return safe_div(covariance, variance)


def log_price_slope_bps(closes: Sequence[float]) -> float | None:
    """OLS slope of log(close) over the window, expressed in basis points per bar."""
    if any(close <= 0 for close in closes):
        return None
    slope = ols_slope([math.log(close) for close in closes])
    if slope is None:
        return None
    return finite(slope * BPS)


def clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value
