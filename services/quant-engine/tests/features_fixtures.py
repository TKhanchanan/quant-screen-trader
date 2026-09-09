"""Deterministic candle and sample fixtures for the feature engine tests."""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from quant_engine.configuration import Platform
from quant_engine.market_models import (
    TIMEFRAMES,
    Candle,
    DataQuality,
    PriceSample,
    QualityState,
    SourceType,
    Timeframe,
)

BASE_MS = int(datetime(2026, 9, 8, 12, 0, tzinfo=UTC).timestamp() * 1000)
CONTEXT_A = UUID("11111111-1111-4111-8111-111111111111")
CONTEXT_B = UUID("22222222-2222-4222-8222-222222222222")

GOOD = DataQuality(
    state="GOOD", confidence=1, freshness=1, completeness=1, sourceReliability=1, latencyMs=0
)


def candle(
    index: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    *,
    timeframe: Timeframe = "M1",
    platform: Platform = "capitalbear",
    slot: int = 1,
    asset: str = "EUR/USD OTC",
    context: UUID = CONTEXT_A,
    source: SourceType = "REPLAY",
    quality: QualityState = "GOOD",
    coverage: float = 1.0,
    gap_ms: int = 0,
    base: int = BASE_MS,
) -> Candle:
    duration = TIMEFRAMES[timeframe] * 1000
    open_time = base + index * duration
    return Candle(
        platform=platform,
        slotId=slot,
        assetName=asset,
        contextId=context,
        calibrationProfileId=None,
        sourceType=source,
        timeframe=timeframe,
        openTime=open_time,
        closeTime=open_time + duration,
        open=open_,
        high=high,
        low=low,
        close=close,
        sampleCount=TIMEFRAMES[timeframe],
        expectedSamples=TIMEFRAMES[timeframe],
        coverage=coverage,
        gapDurationMs=gap_ms,
        quality=quality,
        state="CLOSED",
    )


def flat_candles(closes: Sequence[float], **changes: object) -> list[Candle]:
    """One candle per close, each with a small symmetric range around it."""
    return [
        candle(
            index,
            close,
            close * 1.001,
            close * 0.999,
            close,
            **changes,  # type: ignore[arg-type]
        )
        for index, close in enumerate(closes)
    ]


def second(
    epoch_second: int,
    price: float,
    *,
    platform: Platform = "capitalbear",
    slot: int = 1,
    asset: str = "EUR/USD OTC",
    context: UUID = CONTEXT_A,
) -> PriceSample:
    stamp = BASE_MS + epoch_second * 1000
    return PriceSample(
        platform=platform,
        slotId=slot,
        assetName=asset,
        contextId=context,
        calibrationProfileId=None,
        sourceType="REPLAY",
        timestamp=stamp,
        bucketTime=stamp,
        price=price,
        quality=GOOD,
    )


def walk_or_flat(count: int, **changes: object) -> list[Candle]:
    """A deterministic path with a real range on every bar, for storage and engine tests."""
    return [
        candle(
            index,
            close,
            close * 1.002,
            close * 0.998,
            close,
            **changes,  # type: ignore[arg-type]
        )
        for index, close in ((step, 100.0 + (step % 7) - (step % 3)) for step in range(count))
    ]
