"""The replay clock, and every duration derived from the frozen contracts (Phase 11).

Time in a replay comes from the record and from nowhere else. ``datetime.now`` cannot appear in
this package's analytical path: a backtest whose result depended on when it was run would not be
a backtest. The one wall clock a run is allowed is the stopwatch that measures how long the
replay took, and it is kept on the run metadata where nothing can read it into a number.

The durations here are derived rather than chosen. A warm-up long enough for the indicators is a
property of ``qfe-v2`` and the platform policies, and a settlement tail long enough for an open
trade is a property of ``qst-paper-v1``; both are computed from those modules so a change
upstream moves them instead of quietly invalidating them.
"""

from __future__ import annotations

from typing import Final

from quant_engine.configuration import Platform
from quant_engine.features.engine import PRIMARY_TIMEFRAME, READY_BARS
from quant_engine.market_models import TIMEFRAMES, Timeframe
from quant_engine.paper.policy import PaperSettings
from quant_engine.strategy.policy import policy_for

AVAILABILITY_LAG_MS: Final = 3_000
"""The market-time lag the live engine's background maintenance applies to the availability
watermark, restated here because ``market_api.advance_live`` holds it as a literal.

It is what keeps a bar from being declared closed before the batches covering its final seconds
could have arrived. Replay applies the same lag from replayed market time so that a quiet stretch
closes a bar at the same point in the series it would have closed at live, rather than whenever
the next sample happens to turn up. A test asserts the live value still agrees with this one."""

WARMUP_BARS: Final = READY_BARS
"""Fifty closed bars: the count below which ``qfe-v2`` reports WARMING and nothing in the
catalog may claim READY. Imported rather than restated, so a change to the feature contract
lengthens the warm-up instead of silently invalidating it."""


def slowest_timeframe(platform: Platform) -> Timeframe:
    """The slowest bar this platform's decisions actually read.

    A primary close is only half of a decision: the ensemble reads higher-timeframe context
    snapshots as-of that close, and those carry the same fifty-bar maturity rule. CapitalBear
    decides on S5 while reading M1 and M5; IQ Option decides on M1 while reading M5 and M10. The
    warm-up has to mature the slowest of them or the first evaluated decisions would be made on
    context that had not formed yet.
    """
    policy = policy_for(platform)
    timeframes = [PRIMARY_TIMEFRAME[platform], policy.primary, *policy.contextWeights]
    return max(timeframes, key=lambda frame: TIMEFRAMES[frame])


def warmup_duration_ms(platforms: tuple[Platform, ...]) -> int:
    """Fifty bars of the slowest timeframe each selected platform reads.

    Deliberately conservative, and deliberately derived. CapitalBear reads M5, so its warm-up is
    fifty five-minute bars — four hours and ten minutes. IQ Option reads M10, so its warm-up is
    fifty ten-minute bars — eight hours and twenty minutes. A run covering both takes the longer
    of the two, because the evaluation window is one window and the weaker platform would
    otherwise start evaluating on immature context.
    """
    if not platforms:
        return 0
    return max(
        TIMEFRAMES[slowest_timeframe(platform)] * 1_000 * WARMUP_BARS for platform in platforms
    )


def settlement_tail_ms(platforms: tuple[Platform, ...], settings: PaperSettings) -> int:
    """How long after the evaluation window a trade opened inside it can still be running.

    A selection made one millisecond before the window closes may wait its full entry bound for
    a price, hold its whole horizon, and settle at the outer edge of its resolution bound. Cut
    the feed at the window and that trade is thrown away — not because the market failed to
    answer, but because the replay stopped listening. So the tail is the sum of all three, per
    platform, and the widest one wins.
    """
    if not platforms:
        return 0
    return max(
        settings.max_entry_delay_ms(platform)
        + settings.duration_ms(platform)
        + settings.max_resolution_lag_ms(platform)
        for platform in platforms
    )


class ReplayClock:
    """A market-time clock that only ever moves because the record said so.

    Monotone by construction: a later event with an earlier timestamp does not rewind it, so a
    durable record whose rows are an unordered set cannot make the clock go backwards even
    before the canonical sort has been applied. It exposes no way to set a time that did not
    come from an event, and it cannot read the host's clock at all.
    """

    __slots__ = ("_first", "_now", "advances", "regressions")

    def __init__(self) -> None:
        self._now: int | None = None
        self._first: int | None = None
        self.advances = 0
        self.regressions = 0

    def advance(self, market_time: int) -> int:
        """Move to a market instant the record has reached, and report where the clock now is."""
        if self._first is None:
            self._first = market_time
        if self._now is None or market_time > self._now:
            self._now = market_time
            self.advances += 1
        elif market_time < self._now:
            self.regressions += 1
        return self._now

    @property
    def now(self) -> int | None:
        """The latest market instant the record has reached, or ``None`` before the first one."""
        return self._now

    @property
    def started(self) -> int | None:
        return self._first

    @property
    def elapsed(self) -> int:
        """Market time covered so far, in milliseconds. Never a wall-clock duration."""
        if self._now is None or self._first is None:
            return 0
        return self._now - self._first

    @property
    def watermark(self) -> int | None:
        """The availability watermark: market time, less the lag the live engine applies.

        Knowing that market time has reached *T* is knowledge available at *T*, so deriving
        another slot's watermark from it grants no future information — it is exactly what the
        live engine does with its own clock, and it is what makes a gap close a bar in replay at
        the same point in the series it closes one live.
        """
        return None if self._now is None else self._now - AVAILABILITY_LAG_MS
