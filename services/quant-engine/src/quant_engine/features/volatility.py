"""Volatility measures: Wilder ATR, realized volatility and Bollinger bands."""

from __future__ import annotations

from collections.abc import Sequence

from quant_engine.features.math import bps, finite, log_return_bps, pstdev, safe_div


def true_range(high: float, low: float, previous_close: float | None) -> float:
    if previous_close is None:
        return high - low
    return max(high - low, abs(high - previous_close), abs(low - previous_close))


class ATRState:
    """Wilder ATR: SMA seed over the first ``period`` true ranges, then Wilder smoothing."""

    def __init__(self, period: int = 14) -> None:
        if period < 1:
            raise ValueError("Positive ATR period required")
        self.period = period
        self.value: float | None = None
        self._seed: list[float] = []

    def update(self, range_value: float) -> float | None:
        if self.value is None:
            self._seed.append(range_value)
            if len(self._seed) < self.period:
                return None
            self.value = sum(self._seed) / self.period
            self._seed.clear()
        else:
            self.value = (self.value * (self.period - 1) + range_value) / self.period
        return self.value


def realized_volatility_bps(closes: Sequence[float], window: int) -> float | None:
    """Population standard deviation of the last ``window`` log returns, in basis points.

    Deliberately not annualized: an annualization factor would encode a timeframe assumption
    that does not hold across S5, M1, M5 and M10 alike.
    """
    if len(closes) < window + 1:
        return None
    returns: list[float] = []
    for index in range(len(closes) - window, len(closes)):
        change = log_return_bps(closes[index], closes[index - 1])
        if change is None:
            return None
        returns.append(change)
    return pstdev(returns)


class BollingerBands:
    __slots__ = ("middle", "upper", "lower", "width_bps", "percent_b", "z_score")

    def __init__(
        self,
        middle: float | None,
        upper: float | None,
        lower: float | None,
        width_bps: float | None,
        percent_b: float | None,
        z_score: float | None,
    ) -> None:
        self.middle = middle
        self.upper = upper
        self.lower = lower
        self.width_bps = width_bps
        self.percent_b = percent_b
        self.z_score = z_score


EMPTY_BANDS = BollingerBands(None, None, None, None, None, None)


def bollinger(closes: Sequence[float], period: int = 20, deviations: float = 2.0) -> BollingerBands:
    if len(closes) < period:
        return EMPTY_BANDS
    window = list(closes)[-period:]
    middle = sum(window) / period
    spread = pstdev(window)
    if spread is None or middle == 0:
        return EMPTY_BANDS
    upper, lower = middle + deviations * spread, middle - deviations * spread
    width = bps(upper, middle)
    lower_width = bps(lower, middle)
    return BollingerBands(
        middle=finite(middle),
        upper=finite(upper),
        lower=finite(lower),
        width_bps=None if width is None or lower_width is None else finite(width - lower_width),
        percent_b=safe_div(closes[-1] - lower, upper - lower),
        z_score=safe_div(closes[-1] - middle, spread),
    )


def range_expansion(current_true_range: float, atr: float | None) -> float | None:
    if atr is None:
        return None
    return safe_div(current_true_range, atr)
