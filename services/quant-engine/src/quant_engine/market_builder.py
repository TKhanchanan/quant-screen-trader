"""Event-time builder: first equal timestamp wins; late data never rewrites history."""

from collections import deque
from dataclasses import dataclass

from quant_engine.market_models import (
    TIMEFRAMES,
    Candle,
    MarketObservation,
    PriceSample,
    Timeframe,
    price_sample,
)


@dataclass(frozen=True, slots=True)
class Emission:
    """One canonical record together with the market time at which it became available.

    A closed bar is *about* the window it covers, but it only exists once the watermark has
    passed its close: the ninth slot of a cohort is assembled after the bar it describes ended.
    Downstream layers that must not pretend they could have acted at ``closeTime`` — Phase 9
    above all — need that availability time, and it can only be stated by the builder that
    applied the watermark.

    ``availableAt`` is a canonical market event time in every replayable path: while a series
    is being rebuilt from stored data the watermark is always a sample's own timestamp, so the
    same recorded events reproduce the same availability times exactly.
    """

    record: PriceSample | Candle
    availableAt: int


class TimeSeriesBuilder:
    def __init__(self, capacity: int = 3600) -> None:
        if capacity < 1:
            raise ValueError("Positive capacity required")
        self.samples: deque[PriceSample] = deque(maxlen=capacity)
        self.seconds: deque[PriceSample] = deque(maxlen=capacity)
        self.candles: deque[Candle] = deque(maxlen=capacity)
        self.forming: dict[Timeframe, Candle] = {}
        self._degraded: dict[Timeframe, bool] = {}
        self._seconds_seen: dict[Timeframe, set[int]] = {}
        self._pending: PriceSample | None = None
        self._identity: tuple[object, ...] | None = None
        self._watermark = -1
        self._last_sample = -1
        self.rejected = 0
        self.missingSeconds = 0
        self.emitted: deque[Emission] = deque(maxlen=capacity * 5)

    def advance(self, timestamp: int) -> None:
        """Explicit availability watermark. Only close boundaries already reached."""
        if timestamp < self._watermark:
            return
        self._watermark = timestamp
        if self._pending and (self._pending.timestamp // 1000 + 1) * 1000 <= timestamp:
            second = self._pending.model_copy(
                update={"bucketTime": self._pending.timestamp // 1000 * 1000}
            )
            self.seconds.append(second)
            self.emitted.append(Emission(second, timestamp))
            self._pending = None
        for tf, candle in list(self.forming.items()):
            if candle.closeTime <= timestamp:
                closed = candle.model_copy(update={"state": "CLOSED"})
                self.candles.append(closed)
                self.emitted.append(Emission(closed, timestamp))
                del self.forming[tf]
                del self._seconds_seen[tf]
                del self._degraded[tf]

    def ingest(self, observation: MarketObservation) -> PriceSample | None:
        sample = price_sample(observation)
        if (
            sample is None
            or sample.timestamp < self._watermark
            or sample.timestamp <= self._last_sample
        ):
            self.rejected += 1
            return None
        identity = (
            sample.platform,
            sample.slotId,
            sample.assetName,
            sample.contextId,
            sample.calibrationProfileId,
            sample.sourceType,
        )
        if self._identity is not None and identity != self._identity:
            # End the old context without manufacturing closed partial candles.
            self.advance(sample.timestamp)
            self.forming.clear()
            self._seconds_seen.clear()
            self._degraded.clear()
            self._pending = None
            self.samples.clear()
            self.seconds.clear()
            self.candles.clear()
            self._last_sample = -1
        self._identity = identity
        self.advance(sample.timestamp)
        if self._last_sample >= 0:
            self.missingSeconds += max(0, sample.timestamp // 1000 - self._last_sample // 1000 - 1)
        self._last_sample = sample.timestamp
        self.samples.append(sample)
        self._pending = sample  # latest valid observation in this second
        for tf, duration in TIMEFRAMES.items():
            start = sample.timestamp // (duration * 1000) * duration * 1000
            candle = self.forming.get(tf)
            if candle is None:
                candle = Candle(
                    platform=sample.platform,
                    slotId=sample.slotId,
                    assetName=sample.assetName,
                    contextId=sample.contextId,
                    calibrationProfileId=sample.calibrationProfileId,
                    sourceType=sample.sourceType,
                    timeframe=tf,
                    openTime=start,
                    closeTime=start + duration * 1000,
                    open=sample.price,
                    high=sample.price,
                    low=sample.price,
                    close=sample.price,
                    sampleCount=0,
                    expectedSamples=duration,
                    coverage=0,
                    gapDurationMs=duration * 1000,
                    quality="DEGRADED",
                    state="FORMING",
                )
                self._seconds_seen[tf] = set()
                self._degraded[tf] = False
            self._seconds_seen[tf].add(sample.timestamp // 1000)
            self._degraded[tf] |= sample.quality.state != "GOOD"
            coverage = len(self._seconds_seen[tf]) / duration
            # Confidence is gated before entry; partial coverage remains degraded.
            quality = "GOOD" if coverage == 1 and not self._degraded[tf] else "DEGRADED"
            candle = candle.model_copy(
                update={
                    "high": max(candle.high, sample.price),
                    "low": min(candle.low, sample.price),
                    "close": sample.price,
                    "sampleCount": candle.sampleCount + 1,
                    "coverage": coverage,
                    "gapDurationMs": (duration - len(self._seconds_seen[tf])) * 1000,
                    "quality": quality,
                }
            )
            self.forming[tf] = candle
        return sample

    def drain(self) -> list[PriceSample | Candle]:
        result = [emission.record for emission in self.emitted]
        self.emitted.clear()
        return result
