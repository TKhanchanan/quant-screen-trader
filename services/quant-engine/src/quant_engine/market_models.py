"""Canonical wire models. Payout is a ratio; timestamps are UTC epoch milliseconds."""

from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from quant_engine.configuration import Model, Platform

type SourceType = Literal["DOM", "VISUAL", "REPLAY", "SYNTHETIC"]
type QualityState = Literal["GOOD", "DEGRADED", "UNCERTAIN", "STALE", "INVALID"]
type Timeframe = Literal["S5", "M1", "M5", "M10"]
TIMEFRAMES: dict[Timeframe, int] = {"S5": 5, "M1": 60, "M5": 300, "M10": 600}


class DataQuality(Model):
    state: QualityState
    confidence: float = Field(ge=0, le=1, strict=True)
    freshness: float = Field(ge=0, le=1, strict=True)
    completeness: float = Field(ge=0, le=1, strict=True)
    sourceReliability: float = Field(ge=0, le=1, strict=True)
    latencyMs: float = Field(ge=0, strict=True)


class MarketObservation(Model):
    id: UUID
    platform: Platform
    slotId: int = Field(ge=1, le=9, strict=True)
    assetName: str = Field(min_length=1, max_length=120)
    contextId: UUID
    observedAt: AwareDatetime
    parsedAt: AwareDatetime
    sourceType: SourceType
    price: float | None = Field(gt=0, strict=True)
    payout: float | None = Field(ge=0, le=1, strict=True)
    timerSeconds: int | None = Field(ge=0, le=86399, strict=True)
    parserConfidence: float = Field(ge=0, le=1, strict=True)
    dataQuality: DataQuality
    captureLatencyMs: float = Field(ge=0, strict=True)
    parseLatencyMs: float = Field(ge=0, strict=True)
    calibrationProfileId: UUID | None
    parserVersion: str = Field(min_length=1, max_length=80)

    @model_validator(mode="after")
    def chronology(self) -> Self:
        if self.parsedAt < self.observedAt:
            raise ValueError("Parsing precedes observation")
        return self


class PriceSample(Model):
    platform: Platform
    slotId: int = Field(ge=1, le=9, strict=True)
    assetName: str = Field(min_length=1, max_length=120)
    contextId: UUID
    calibrationProfileId: UUID | None
    sourceType: SourceType
    timestamp: int = Field(strict=True)
    bucketTime: int | None = Field(default=None, strict=True)
    price: float = Field(gt=0, strict=True)
    quality: DataQuality


class Candle(Model):
    platform: Platform
    slotId: int = Field(ge=1, le=9, strict=True)
    assetName: str = Field(min_length=1, max_length=120)
    contextId: UUID
    calibrationProfileId: UUID | None
    sourceType: SourceType
    timeframe: Timeframe
    openTime: int = Field(strict=True)
    closeTime: int = Field(strict=True)
    open: float = Field(gt=0, strict=True)
    high: float = Field(gt=0, strict=True)
    low: float = Field(gt=0, strict=True)
    close: float = Field(gt=0, strict=True)
    sampleCount: int = Field(ge=0, strict=True)
    expectedSamples: int = Field(gt=0, strict=True)
    coverage: float = Field(ge=0, le=1, strict=True)
    gapDurationMs: int = Field(ge=0, strict=True)
    quality: QualityState
    state: Literal["FORMING", "CLOSED"]


def price_sample(observation: MarketObservation) -> PriceSample | None:
    q = observation.dataQuality
    if (
        observation.price is None
        or observation.parserConfidence < 0.8
        or q.state not in ("GOOD", "DEGRADED")
        or q.confidence < 0.8
        or q.freshness <= 0
        or q.sourceReliability < 0.8
        or (observation.parsedAt - observation.observedAt).total_seconds() > 3
    ):
        return None
    return PriceSample(
        platform=observation.platform,
        slotId=observation.slotId,
        assetName=observation.assetName,
        contextId=observation.contextId,
        calibrationProfileId=observation.calibrationProfileId,
        sourceType=observation.sourceType,
        timestamp=int(observation.observedAt.timestamp() * 1000),
        price=observation.price,
        quality=q,
    )
