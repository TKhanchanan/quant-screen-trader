"""Momentum indicators computed from CLOSED candles only."""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence

from quant_engine.features.math import bps, finite, mean
from quant_engine.features.trend import EMAState

FLAT_RSI = 50.0
"""A window with neither gains nor losses has no directional pressure at all; Wilder's ratio
is undefined there, so the neutral midpoint is reported rather than an arbitrary extreme."""


def rsi_from_averages(average_gain: float, average_loss: float) -> float:
    if average_gain == 0 and average_loss == 0:
        return FLAT_RSI
    if average_loss == 0:
        return 100.0
    if average_gain == 0:
        return 0.0
    return 100.0 - 100.0 / (1.0 + average_gain / average_loss)


class RSIState:
    """Wilder RSI: SMA seed over the first ``period`` changes, then Wilder smoothing."""

    def __init__(self, period: int = 14) -> None:
        if period < 1:
            raise ValueError("Positive RSI period required")
        self.period = period
        self.value: float | None = None
        self.average_gain: float | None = None
        self.average_loss: float | None = None
        self._previous: float | None = None
        self._gains: list[float] = []
        self._losses: list[float] = []

    def update(self, close: float) -> float | None:
        if self._previous is None:
            self._previous = close
            return None
        change = close - self._previous
        self._previous = close
        gain, loss = max(change, 0.0), max(-change, 0.0)
        if self.average_gain is None or self.average_loss is None:
            self._gains.append(gain)
            self._losses.append(loss)
            if len(self._gains) < self.period:
                return None
            self.average_gain = sum(self._gains) / self.period
            self.average_loss = sum(self._losses) / self.period
            self._gains.clear()
            self._losses.clear()
        else:
            weight = self.period - 1
            self.average_gain = (self.average_gain * weight + gain) / self.period
            self.average_loss = (self.average_loss * weight + loss) / self.period
        self.value = rsi_from_averages(self.average_gain, self.average_loss)
        return self.value


class StochasticState:
    """%K over the trailing period, %D as the simple mean of the last ``smoothing`` %K values."""

    def __init__(self, period: int = 14, smoothing: int = 3) -> None:
        self.period = period
        self.smoothing = smoothing
        self._k: deque[float | None] = deque(maxlen=smoothing)

    def update(
        self, highs: Sequence[float], lows: Sequence[float], close: float
    ) -> tuple[float | None, float | None]:
        percent_k: float | None = None
        if len(highs) >= self.period and len(lows) >= self.period:
            highest = max(highs[-self.period :])
            lowest = min(lows[-self.period :])
            span = highest - lowest
            if span > 0:
                percent_k = finite(100.0 * (close - lowest) / span)
        self._k.append(percent_k)
        recent = list(self._k)
        percent_d = (
            mean([value for value in recent if value is not None])
            if len(recent) == self.smoothing and all(value is not None for value in recent)
            else None
        )
        return percent_k, percent_d


class MACDState:
    """MACD line, signal line and histogram, all seeded through their own EMA warm-up."""

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9) -> None:
        self.fast = EMAState(fast)
        self.slow = EMAState(slow)
        self.signal = EMAState(signal)
        self.macd: float | None = None
        self.signal_value: float | None = None
        self.histogram: float | None = None

    def update(self, close: float) -> None:
        fast, slow = self.fast.update(close), self.slow.update(close)
        if fast is None or slow is None:
            return
        self.macd = finite(fast - slow)
        if self.macd is None:
            return
        self.signal_value = self.signal.update(self.macd)
        self.histogram = (
            None if self.signal_value is None else finite(self.macd - self.signal_value)
        )


def roc_bps(closes: Sequence[float], window: int) -> float | None:
    """Rate of change against the close ``window`` bars ago, in basis points."""
    if len(closes) < window + 1:
        return None
    return bps(closes[-1], closes[-(window + 1)])
