"""Feature wire models.

Every numeric feature is optional: an indicator that has not warmed up, or whose inputs
cannot support it, reports ``None``. Zero is a measurement, never a placeholder.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field

from quant_engine.configuration import Model, Platform
from quant_engine.market_models import QualityState, Timeframe

FEATURE_VERSION = "qfe-v2"
"""Schema and formula version. Changing any formula requires changing this string so that
snapshots computed under different definitions can never be silently mixed.

qfe-v2 corrected trueRangeBps and atr14Bps, which reported a bare ratio under a name that
promises basis points. qfe-v1 records stay on disk and stay readable; they are simply a
different formula contract and must never be pooled with qfe-v2 for research or backtests."""

type FeatureStatus = Literal["WARMING", "READY", "DEGRADED", "INVALID"]

Unit = Field(default=None, ge=0, le=1)


class PriceActionFeatures(Model):
    return1Bps: float | None = None
    return3Bps: float | None = None
    return5Bps: float | None = None
    logReturn1Bps: float | None = None
    bodyBps: float | None = None
    rangeBps: float | None = None
    bodyToRange: float | None = Unit
    upperWickToRange: float | None = Unit
    lowerWickToRange: float | None = Unit
    closeLocation: float | None = Unit
    trueRangeBps: float | None = None
    gapFromPreviousCloseBps: float | None = None


class TrendFeatures(Model):
    ema5: float | None = None
    ema9: float | None = None
    ema20: float | None = None
    ema50: float | None = None
    priceToEma5Bps: float | None = None
    priceToEma9Bps: float | None = None
    priceToEma20Bps: float | None = None
    priceToEma50Bps: float | None = None
    ema5To9Bps: float | None = None
    ema5To20Bps: float | None = None
    ema9To20Bps: float | None = None
    ema20To50Bps: float | None = None
    ema5Slope3: float | None = None
    ema9Slope3: float | None = None
    ema20Slope3: float | None = None
    ema50Slope3: float | None = None
    priceSlope5: float | None = None
    priceSlope10: float | None = None
    priceSlope20: float | None = None


class MomentumFeatures(Model):
    rsi14: float | None = Field(default=None, ge=0, le=100)
    roc5Bps: float | None = None
    roc10Bps: float | None = None
    stochK14: float | None = Field(default=None, ge=0, le=100)
    stochD3: float | None = Field(default=None, ge=0, le=100)
    macd: float | None = None
    macdSignal: float | None = None
    macdHistogram: float | None = None
    macdBps: float | None = None
    macdSignalBps: float | None = None
    macdHistogramBps: float | None = None


class VolatilityFeatures(Model):
    atr14: float | None = Field(default=None, ge=0)
    atr14Bps: float | None = Field(default=None, ge=0)
    realizedVol10Bps: float | None = Field(default=None, ge=0)
    realizedVol20Bps: float | None = Field(default=None, ge=0)
    bbMiddle: float | None = None
    bbUpper: float | None = None
    bbLower: float | None = None
    bbWidthBps: float | None = None
    bbPercentB: float | None = None
    bbZScore: float | None = None
    rangeExpansion: float | None = Field(default=None, ge=0)


class StructureFeatures(Model):
    priorHigh5: float | None = None
    priorLow5: float | None = None
    priorHigh10: float | None = None
    priorLow10: float | None = None
    priorHigh20: float | None = None
    priorLow20: float | None = None
    distanceToPriorHigh5Bps: float | None = None
    distanceToPriorLow5Bps: float | None = None
    distanceToPriorHigh10Bps: float | None = None
    distanceToPriorLow10Bps: float | None = None
    distanceToPriorHigh20Bps: float | None = None
    distanceToPriorLow20Bps: float | None = None
    abovePriorHigh5: bool | None = None
    belowPriorLow5: bool | None = None
    abovePriorHigh10: bool | None = None
    belowPriorLow10: bool | None = None
    abovePriorHigh20: bool | None = None
    belowPriorLow20: bool | None = None
    confirmedPivots: int = Field(default=0, ge=0)
    nearestSupportDistanceBps: float | None = None
    nearestResistanceDistanceBps: float | None = None
    supportTouches: int | None = Field(default=None, ge=1)
    resistanceTouches: int | None = Field(default=None, ge=1)


class NoiseFeatures(Model):
    efficiencyRatio10: float | None = Unit
    efficiencyRatio20: float | None = Unit
    choppiness14: float | None = Field(default=None, ge=0, le=100)
    signFlipRate10: float | None = Unit
    rangeOverlap5: float | None = Field(default=None, ge=0)


class TimeContextFeatures(Model):
    timeframeSeconds: int = Field(gt=0)
    isOTC: bool
    hourUtcSin: float
    hourUtcCos: float
    minuteUtcSin: float
    minuteUtcCos: float
    minutePhase: float = Field(ge=0, lt=1)


class MicroFeatures(Model):
    """Derived from the canonical one-second stream, never from raw capture frames."""

    samples: int = Field(ge=0)
    lastSecond: int | None = None
    microReturn1sBps: float | None = None
    microReturn3sBps: float | None = None
    microReturn5sBps: float | None = None
    microVelocity3s: float | None = None
    microVelocity5s: float | None = None
    microAcceleration1s: float | None = None
    microVol5sBps: float | None = Field(default=None, ge=0)
    microVol10sBps: float | None = Field(default=None, ge=0)
    microVol30sBps: float | None = Field(default=None, ge=0)
    microRange5sBps: float | None = Field(default=None, ge=0)
    microRange10sBps: float | None = Field(default=None, ge=0)
    microRange30sBps: float | None = Field(default=None, ge=0)
    microEfficiency5s: float | None = Unit
    microEfficiency10s: float | None = Unit
    microSignFlipRate10s: float | None = Unit
    microCoverage10s: float | None = Unit
    microCoverage30s: float | None = Unit


class FeatureQuality(Model):
    """Metadata about the inputs. Never a trading score."""

    goodRatio10: float | None = Unit
    goodRatio20: float | None = Unit
    meanCoverage10: float | None = Unit
    meanCoverage20: float | None = Unit
    gapBars10: int | None = Field(default=None, ge=0)
    gapBars20: int | None = Field(default=None, ge=0)
    missingSecondsRecent: int | None = Field(default=None, ge=0)
    currentCandleQuality: QualityState
    historyBars: int = Field(ge=0)
    hydratedBars: int = Field(default=0, ge=0)


class FeatureSnapshot(Model):
    platform: Platform
    slotId: int = Field(ge=1, le=9, strict=True)
    assetName: str = Field(min_length=1, max_length=120)
    contextId: UUID
    timeframe: Timeframe
    featureTime: int = Field(strict=True)
    featureVersion: str = Field(min_length=1, max_length=40)
    status: FeatureStatus
    barCount: int = Field(ge=0, strict=True)
    sourceType: str = Field(min_length=1, max_length=20)
    quality: FeatureQuality
    priceAction: PriceActionFeatures
    trend: TrendFeatures
    momentum: MomentumFeatures
    volatility: VolatilityFeatures
    structure: StructureFeatures
    noise: NoiseFeatures
    timeContext: TimeContextFeatures
    micro: MicroFeatures


class FeatureBundle(Model):
    """Read model. Context snapshots are joined as-of the primary feature time."""

    platform: Platform
    slotId: int = Field(ge=1, le=9, strict=True)
    assetName: str = Field(min_length=1, max_length=120)
    contextId: UUID
    asOf: int = Field(strict=True)
    primaryTimeframe: Timeframe
    featureVersion: str = Field(min_length=1, max_length=40)
    primary: FeatureSnapshot | None
    micro: MicroFeatures
    contexts: dict[str, FeatureSnapshot]


class TimeframeDiagnostics(Model):
    timeframe: Timeframe
    barCount: int = Field(ge=0)
    hydratedBars: int = Field(ge=0)
    status: FeatureStatus | None
    featureTime: int | None


class SlotFeatureDiagnostics(Model):
    platform: Platform
    slotId: int = Field(ge=1, le=9)
    assetName: str
    contextId: UUID
    primaryTimeframe: Timeframe
    featureVersion: str
    microSamples: int = Field(ge=0)
    timeframes: list[TimeframeDiagnostics]
    quality: FeatureQuality | None
