"""Market structure facts: prior ranges, breakout state and confirmed swing pivots.

Nothing here looks ahead. Prior ranges exclude the current candle, and a pivot only exists
once the bars to its right have already CLOSED.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence

from quant_engine.features.math import bps

PIVOT_LEFT = 2
PIVOT_RIGHT = 2
CLUSTER_ATR_FRACTION = 0.25


class PriorRange:
    __slots__ = ("high", "low")

    def __init__(self, high: float | None, low: float | None) -> None:
        self.high = high
        self.low = low


def prior_range(highs: Sequence[float], lows: Sequence[float], window: int) -> PriorRange:
    """Highest high and lowest low of the ``window`` candles BEFORE the current one."""
    if len(highs) < window + 1 or len(lows) < window + 1:
        return PriorRange(None, None)
    return PriorRange(max(highs[-(window + 1) : -1]), min(lows[-(window + 1) : -1]))


def resistance_distance_bps(level: float | None, close: float) -> float | None:
    """Positive while the level is still above price."""
    return None if level is None else bps(level, close)


def support_distance_bps(level: float | None, close: float) -> float | None:
    """Positive while the level is still below price."""
    return None if level is None else bps(close, level)


class PivotTracker:
    """Swing pivots confirmed strictly against ``left`` past and ``right`` already-closed bars."""

    def __init__(
        self, left: int = PIVOT_LEFT, right: int = PIVOT_RIGHT, capacity: int = 64
    ) -> None:
        self.left = left
        self.right = right
        self._window: deque[tuple[float, float]] = deque(maxlen=left + right + 1)
        self.highs: deque[float] = deque(maxlen=capacity)
        self.lows: deque[float] = deque(maxlen=capacity)

    def update(self, high: float, low: float) -> None:
        self._window.append((high, low))
        if len(self._window) < self.left + self.right + 1:
            return
        candidate_high, candidate_low = self._window[self.left]
        others = [value for index, value in enumerate(self._window) if index != self.left]
        if all(candidate_high > other_high for other_high, _ in others):
            self.highs.append(candidate_high)
        if all(candidate_low < other_low for _, other_low in others):
            self.lows.append(candidate_low)

    def count(self) -> int:
        return len(self.highs) + len(self.lows)


class Level:
    __slots__ = ("price", "touches")

    def __init__(self, price: float, touches: int) -> None:
        self.price = price
        self.touches = touches


def _cluster(levels: Sequence[float], tolerance: float) -> list[Level]:
    """Group levels that sit within ``tolerance`` of each other. Zero tolerance groups only
    exactly equal prices, which is what happens before ATR exists: no level is invented."""
    clusters: list[Level] = []
    for price in sorted(levels):
        last = clusters[-1] if clusters else None
        if last is not None and price - last.price <= tolerance:
            last.touches += 1
            continue
        clusters.append(Level(price, 1))
    return clusters


def nearest_support(pivot_lows: Sequence[float], close: float, atr: float | None) -> Level | None:
    tolerance = 0.0 if atr is None else atr * CLUSTER_ATR_FRACTION
    below = [price for price in pivot_lows if price < close]
    clusters = _cluster(below, tolerance)
    return clusters[-1] if clusters else None


def nearest_resistance(
    pivot_highs: Sequence[float], close: float, atr: float | None
) -> Level | None:
    tolerance = 0.0 if atr is None else atr * CLUSTER_ATR_FRACTION
    above = [price for price in pivot_highs if price > close]
    clusters = _cluster(above, tolerance)
    return clusters[0] if clusters else None
