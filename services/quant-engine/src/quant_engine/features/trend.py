"""Exponential moving averages and slope measures over CLOSED candles."""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence

from quant_engine.features.math import finite, log_price_slope_bps, log_return_bps

SLOPE_BARS = 3


class EMAState:
    """Standard EMA seeded with SMA(period). No value exists before the seed completes."""

    def __init__(self, period: int) -> None:
        if period < 1:
            raise ValueError("Positive EMA period required")
        self.period = period
        self.alpha = 2.0 / (period + 1)
        self.value: float | None = None
        self._seed: list[float] = []
        self._history: deque[float] = deque(maxlen=SLOPE_BARS + 1)

    def update(self, value: float) -> float | None:
        if self.value is None:
            self._seed.append(value)
            if len(self._seed) < self.period:
                return None
            self.value = sum(self._seed) / self.period
            self._seed.clear()
        else:
            self.value = self.alpha * value + (1.0 - self.alpha) * self.value
        self._history.append(self.value)
        return self.value

    def slope_bps(self) -> float | None:
        """Log change of the EMA over the last three bars, in basis points per bar."""
        if len(self._history) < SLOPE_BARS + 1:
            return None
        change = log_return_bps(self._history[-1], self._history[-(SLOPE_BARS + 1)])
        return None if change is None else finite(change / SLOPE_BARS)


def price_slope_bps(closes: Sequence[float], window: int) -> float | None:
    """OLS slope of log(close) over the trailing ``window`` closes, in bps per bar."""
    if len(closes) < window:
        return None
    return log_price_slope_bps(list(closes)[-window:])
