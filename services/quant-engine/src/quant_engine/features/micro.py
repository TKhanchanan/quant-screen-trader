"""One-second micro state built from the canonical Phase 5 second stream.

Seconds are addressed by their absolute epoch second, so a missing second stays missing:
a gap is never closed by treating the previous sample as if it had arrived on time.
"""

from __future__ import annotations

import math
from collections import deque

from quant_engine.features.math import BPS, bps, finite, log_return_bps, pstdev, safe_div
from quant_engine.features.models import MicroFeatures
from quant_engine.features.noise import sign_flip_rate

MICRO_CAPACITY = 120
MICRO_WINDOWS = (5, 10, 30)


class MicroState:
    """Bounded one-second history for a single live series."""

    def __init__(self, capacity: int = MICRO_CAPACITY) -> None:
        self.history: deque[tuple[int, float]] = deque(maxlen=capacity)
        self.samples = 0

    def reset(self) -> None:
        self.history.clear()
        self.samples = 0

    def ingest(self, second: int, price: float) -> None:
        if price <= 0:
            return
        if self.history and second <= self.history[-1][0]:
            return  # event time only moves forward; late seconds never rewrite history
        self.history.append((second, price))
        self.samples += 1

    def price_at(self, second: int) -> float | None:
        for stamp, price in reversed(self.history):
            if stamp == second:
                return price
            if stamp < second:
                return None
        return None

    def _window(self, seconds: int) -> list[tuple[int, float]]:
        if not self.history:
            return []
        newest = self.history[-1][0]
        return [item for item in self.history if item[0] > newest - seconds]

    def _consecutive_log_returns(self, seconds: int) -> list[float]:
        window = self._window(seconds)
        returns: list[float] = []
        for index in range(1, len(window)):
            if window[index][0] - window[index - 1][0] != 1:
                continue
            change = log_return_bps(window[index][1], window[index - 1][1])
            if change is not None:
                returns.append(change)
        return returns

    def _return_over(self, seconds: int) -> float | None:
        if not self.history:
            return None
        newest, price = self.history[-1]
        earlier = self.price_at(newest - seconds)
        return None if earlier is None else bps(price, earlier)

    def _velocity(self, seconds: int) -> float | None:
        if not self.history:
            return None
        newest, price = self.history[-1]
        earlier = self.price_at(newest - seconds)
        if earlier is None:
            return None
        change = log_return_bps(price, earlier)
        return None if change is None else finite(change / seconds)

    def _acceleration(self) -> float | None:
        """Change in the one-second return: r(t) - r(t-1), both in basis points."""
        if not self.history:
            return None
        newest = self.history[-1][0]
        current = self._return_between(newest - 1, newest)
        previous = self._return_between(newest - 2, newest - 1)
        if current is None or previous is None:
            return None
        return finite(current - previous)

    def _return_between(self, earlier_second: int, later_second: int) -> float | None:
        earlier, later = self.price_at(earlier_second), self.price_at(later_second)
        if earlier is None or later is None:
            return None
        return bps(later, earlier)

    def _range_bps(self, seconds: int) -> float | None:
        window = self._window(seconds)
        if len(window) < 2:
            return None
        prices = [price for _, price in window]
        value = bps(max(prices), min(prices))
        return None if value is None or value < 0 else value

    def _efficiency(self, seconds: int) -> float | None:
        window = self._window(seconds)
        if len(window) < 2:
            return None
        prices = [price for _, price in window]
        travelled = sum(abs(prices[i] - prices[i - 1]) for i in range(1, len(prices)))
        ratio = safe_div(abs(prices[-1] - prices[0]), travelled)
        return None if ratio is None else min(1.0, max(0.0, ratio))

    def coverage(self, seconds: int) -> float | None:
        """Share of the trailing interval for which a real second actually exists."""
        if not self.history:
            return None
        newest = self.history[-1][0]
        present = {stamp for stamp, _ in self.history if newest - seconds < stamp <= newest}
        return safe_div(len(present), seconds)

    def _volatility(self, seconds: int) -> float | None:
        return pstdev(self._consecutive_log_returns(seconds))

    def features(self) -> MicroFeatures:
        newest = self.history[-1][0] if self.history else None
        flips = sign_flip_rate(self._consecutive_log_returns(11))
        return MicroFeatures(
            samples=self.samples,
            lastSecond=newest,
            microReturn1sBps=self._return_over(1),
            microReturn3sBps=self._return_over(3),
            microReturn5sBps=self._return_over(5),
            microVelocity3s=self._velocity(3),
            microVelocity5s=self._velocity(5),
            microAcceleration1s=self._acceleration(),
            microVol5sBps=self._volatility(5),
            microVol10sBps=self._volatility(10),
            microVol30sBps=self._volatility(30),
            microRange5sBps=self._range_bps(5),
            microRange10sBps=self._range_bps(10),
            microRange30sBps=self._range_bps(30),
            microEfficiency5s=self._efficiency(5),
            microEfficiency10s=self._efficiency(10),
            microSignFlipRate10s=flips,
            microCoverage10s=self.coverage(10),
            microCoverage30s=self.coverage(30),
        )


def seconds_between(earlier_ms: int, later_ms: int) -> int:
    return int(math.floor((later_ms - earlier_ms) / 1000))


__all__ = ["BPS", "MICRO_CAPACITY", "MICRO_WINDOWS", "MicroState", "seconds_between"]
