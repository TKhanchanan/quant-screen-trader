"""Streaming feature engine.

State is keyed by (platform, slot) and owned by an identity of (assetName, contextId): any
change to that identity discards every rolling indicator, pivot and micro sample, so nothing
from a previous asset or context can survive into the next one.

Only CLOSED candles drive the persistent indicators. A FORMING candle would otherwise repaint
every rolling value at whatever rate the capture loop happens to run.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from uuid import UUID

from quant_engine.configuration import Platform
from quant_engine.features.math import bps, finite, safe_div
from quant_engine.features.micro import MicroState
from quant_engine.features.models import (
    FEATURE_VERSION,
    FeatureBundle,
    FeatureQuality,
    FeatureSnapshot,
    FeatureStatus,
    MicroFeatures,
    MomentumFeatures,
    NoiseFeatures,
    PriceActionFeatures,
    SlotFeatureDiagnostics,
    StructureFeatures,
    TimeContextFeatures,
    TimeframeDiagnostics,
    TrendFeatures,
    VolatilityFeatures,
)
from quant_engine.features.momentum import MACDState, RSIState, StochasticState, roc_bps
from quant_engine.features.noise import (
    choppiness,
    efficiency_ratio,
    range_overlap,
    sign_flip_rate,
)
from quant_engine.features.structure import (
    PivotTracker,
    nearest_resistance,
    nearest_support,
    prior_range,
    resistance_distance_bps,
    support_distance_bps,
)
from quant_engine.features.trend import EMAState, price_slope_bps
from quant_engine.features.volatility import (
    ATRState,
    bollinger,
    range_expansion,
    realized_volatility_bps,
    true_range,
)
from quant_engine.market_models import TIMEFRAMES, Candle, PriceSample, QualityState, Timeframe

HISTORY_CAPACITY = 256
SNAPSHOT_HISTORY = 8
READY_BARS = 50
"""EMA50 is the slowest member of the catalog; nothing claims READY before it can exist."""
GOOD_RATIO_FLOOR = 0.8
COVERAGE_FLOOR = 0.8
EMA_PERIODS = (5, 9, 20, 50)
PRIMARY_TIMEFRAME: dict[Platform, Timeframe] = {"capitalbear": "S5", "iqoption": "M1"}
LIVE_SOURCES = ("DOM", "VISUAL")

type Hydrator = Callable[[Platform, str, Timeframe, int], list[Candle]]
type SlotKey = tuple[Platform, int]


def is_otc(asset_name: str) -> bool:
    return asset_name.strip().upper().endswith(" OTC")


def time_context(feature_time: int, timeframe: Timeframe, asset_name: str) -> TimeContextFeatures:
    stamp = datetime.fromtimestamp(feature_time / 1000, UTC)
    hour_angle = 2 * math.pi * stamp.hour / 24
    minute_angle = 2 * math.pi * stamp.minute / 60
    return TimeContextFeatures(
        timeframeSeconds=TIMEFRAMES[timeframe],
        isOTC=is_otc(asset_name),
        hourUtcSin=math.sin(hour_angle),
        hourUtcCos=math.cos(hour_angle),
        minuteUtcSin=math.sin(minute_angle),
        minuteUtcCos=math.cos(minute_angle),
        minutePhase=(feature_time % 60_000) / 60_000,
    )


def _ratio_over(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


class TimeframeState:
    """Rolling indicator state for one series at one timeframe."""

    def __init__(self, timeframe: Timeframe, capacity: int = HISTORY_CAPACITY) -> None:
        self.timeframe = timeframe
        self.opens: deque[float] = deque(maxlen=capacity)
        self.highs: deque[float] = deque(maxlen=capacity)
        self.lows: deque[float] = deque(maxlen=capacity)
        self.closes: deque[float] = deque(maxlen=capacity)
        self.true_ranges: deque[float] = deque(maxlen=capacity)
        self.qualities: deque[QualityState] = deque(maxlen=capacity)
        self.coverages: deque[float] = deque(maxlen=capacity)
        self.gaps: deque[int] = deque(maxlen=capacity)
        self.ema = {period: EMAState(period) for period in EMA_PERIODS}
        self.rsi = RSIState(14)
        self.atr = ATRState(14)
        self.macd = MACDState()
        self.stochastic = StochasticState()
        self.pivots = PivotTracker()
        self.snapshots: deque[FeatureSnapshot] = deque(maxlen=SNAPSHOT_HISTORY)
        self.bar_count = 0
        self.hydrated_bars = 0
        self.hydrated = False
        self.last_close_time: int | None = None
        self._stoch_k: float | None = None
        self._stoch_d: float | None = None
        self._highs: list[float] = []
        self._lows: list[float] = []
        self._closes: list[float] = []
        self._ranges: list[float] = []

    def apply(self, candle: Candle, *, hydrated: bool = False) -> None:
        previous_close = self.closes[-1] if self.closes else None
        current_range = true_range(candle.high, candle.low, previous_close)
        self.opens.append(candle.open)
        self.highs.append(candle.high)
        self.lows.append(candle.low)
        self.closes.append(candle.close)
        self.true_ranges.append(current_range)
        self.qualities.append(candle.quality)
        self.coverages.append(candle.coverage)
        self.gaps.append(candle.gapDurationMs)
        self._highs = list(self.highs)
        self._lows = list(self.lows)
        self._closes = list(self.closes)
        self._ranges = list(self.true_ranges)
        for state in self.ema.values():
            state.update(candle.close)
        self.rsi.update(candle.close)
        self.atr.update(current_range)
        self.macd.update(candle.close)
        self._stoch_k, self._stoch_d = self.stochastic.update(self._highs, self._lows, candle.close)
        self.pivots.update(candle.high, candle.low)
        self.bar_count += 1
        if hydrated:
            self.hydrated_bars += 1
        self.last_close_time = candle.closeTime

    def latest(self) -> FeatureSnapshot | None:
        return self.snapshots[-1] if self.snapshots else None

    def as_of(self, feature_time: int) -> FeatureSnapshot | None:
        """Newest snapshot that was already complete at ``feature_time``."""
        for snapshot in reversed(self.snapshots):
            if snapshot.featureTime <= feature_time:
                return snapshot
            continue
        return None

    def price_action(self, candle: Candle) -> PriceActionFeatures:
        closes = self._closes
        previous_close = closes[-2] if len(closes) >= 2 else None
        span = candle.high - candle.low
        body = candle.close - candle.open
        upper_wick = candle.high - max(candle.open, candle.close)
        lower_wick = min(candle.open, candle.close) - candle.low
        return PriceActionFeatures(
            return1Bps=bps(closes[-1], closes[-2]) if len(closes) >= 2 else None,
            return3Bps=bps(closes[-1], closes[-4]) if len(closes) >= 4 else None,
            return5Bps=bps(closes[-1], closes[-6]) if len(closes) >= 6 else None,
            logReturn1Bps=(
                None if previous_close is None else _log_bps(candle.close, previous_close)
            ),
            bodyBps=bps(candle.close, candle.open),
            rangeBps=None if candle.low <= 0 else bps(candle.high, candle.low),
            bodyToRange=safe_div(abs(body), span),
            upperWickToRange=safe_div(upper_wick, span),
            lowerWickToRange=safe_div(lower_wick, span),
            closeLocation=safe_div(candle.close - candle.low, span),
            trueRangeBps=safe_div(self._ranges[-1], candle.close),
            gapFromPreviousCloseBps=(
                None if previous_close is None else bps(candle.open, previous_close)
            ),
        )

    def trend(self, close: float) -> TrendFeatures:
        value = {period: self.ema[period].value for period in EMA_PERIODS}
        slope = {period: self.ema[period].slope_bps() for period in EMA_PERIODS}
        return TrendFeatures(
            ema5=value[5],
            ema9=value[9],
            ema20=value[20],
            ema50=value[50],
            priceToEma5Bps=_pair_bps(close, value[5]),
            priceToEma9Bps=_pair_bps(close, value[9]),
            priceToEma20Bps=_pair_bps(close, value[20]),
            priceToEma50Bps=_pair_bps(close, value[50]),
            ema5To9Bps=_both_bps(value[5], value[9]),
            ema5To20Bps=_both_bps(value[5], value[20]),
            ema9To20Bps=_both_bps(value[9], value[20]),
            ema20To50Bps=_both_bps(value[20], value[50]),
            ema5Slope3=slope[5],
            ema9Slope3=slope[9],
            ema20Slope3=slope[20],
            ema50Slope3=slope[50],
            priceSlope5=price_slope_bps(self._closes, 5),
            priceSlope10=price_slope_bps(self._closes, 10),
            priceSlope20=price_slope_bps(self._closes, 20),
        )

    def momentum(self, close: float) -> MomentumFeatures:
        macd, signal, histogram = self.macd.macd, self.macd.signal_value, self.macd.histogram
        return MomentumFeatures(
            rsi14=self.rsi.value,
            roc5Bps=roc_bps(self._closes, 5),
            roc10Bps=roc_bps(self._closes, 10),
            stochK14=self._stoch_k,
            stochD3=self._stoch_d,
            macd=macd,
            macdSignal=signal,
            macdHistogram=histogram,
            macdBps=_scale_bps(macd, close),
            macdSignalBps=_scale_bps(signal, close),
            macdHistogramBps=_scale_bps(histogram, close),
        )

    def volatility(self, close: float) -> VolatilityFeatures:
        atr = self.atr.value
        bands = bollinger(self._closes)
        return VolatilityFeatures(
            atr14=atr,
            atr14Bps=None if atr is None else safe_div(atr, close),
            realizedVol10Bps=realized_volatility_bps(self._closes, 10),
            realizedVol20Bps=realized_volatility_bps(self._closes, 20),
            bbMiddle=bands.middle,
            bbUpper=bands.upper,
            bbLower=bands.lower,
            bbWidthBps=bands.width_bps,
            bbPercentB=bands.percent_b,
            bbZScore=bands.z_score,
            rangeExpansion=range_expansion(self._ranges[-1], atr),
        )

    def structure(self, close: float) -> StructureFeatures:
        five = prior_range(self._highs, self._lows, 5)
        ten = prior_range(self._highs, self._lows, 10)
        twenty = prior_range(self._highs, self._lows, 20)
        support = nearest_support(list(self.pivots.lows), close, self.atr.value)
        resistance = nearest_resistance(list(self.pivots.highs), close, self.atr.value)
        return StructureFeatures(
            priorHigh5=five.high,
            priorLow5=five.low,
            priorHigh10=ten.high,
            priorLow10=ten.low,
            priorHigh20=twenty.high,
            priorLow20=twenty.low,
            distanceToPriorHigh5Bps=resistance_distance_bps(five.high, close),
            distanceToPriorLow5Bps=support_distance_bps(five.low, close),
            distanceToPriorHigh10Bps=resistance_distance_bps(ten.high, close),
            distanceToPriorLow10Bps=support_distance_bps(ten.low, close),
            distanceToPriorHigh20Bps=resistance_distance_bps(twenty.high, close),
            distanceToPriorLow20Bps=support_distance_bps(twenty.low, close),
            abovePriorHigh5=None if five.high is None else close > five.high,
            belowPriorLow5=None if five.low is None else close < five.low,
            abovePriorHigh10=None if ten.high is None else close > ten.high,
            belowPriorLow10=None if ten.low is None else close < ten.low,
            abovePriorHigh20=None if twenty.high is None else close > twenty.high,
            belowPriorLow20=None if twenty.low is None else close < twenty.low,
            confirmedPivots=self.pivots.count(),
            nearestSupportDistanceBps=(
                None if support is None else support_distance_bps(support.price, close)
            ),
            nearestResistanceDistanceBps=(
                None if resistance is None else resistance_distance_bps(resistance.price, close)
            ),
            supportTouches=None if support is None else support.touches,
            resistanceTouches=None if resistance is None else resistance.touches,
        )

    def noise(self) -> NoiseFeatures:
        closes = self._closes
        changes = [closes[index] - closes[index - 1] for index in range(1, len(closes))][-11:]
        return NoiseFeatures(
            efficiencyRatio10=efficiency_ratio(closes, 10),
            efficiencyRatio20=efficiency_ratio(closes, 20),
            choppiness14=choppiness(self._ranges, self._highs, self._lows),
            signFlipRate10=sign_flip_rate(changes),
            rangeOverlap5=range_overlap(self._highs, self._lows),
        )

    def quality(self, candle: Candle) -> FeatureQuality:
        good = [1.0 if state == "GOOD" else 0.0 for state in self.qualities]
        coverage = list(self.coverages)
        gaps = list(self.gaps)
        return FeatureQuality(
            goodRatio10=_ratio_over(good, 10),
            goodRatio20=_ratio_over(good, 20),
            meanCoverage10=_ratio_over(coverage, 10),
            meanCoverage20=_ratio_over(coverage, 20),
            gapBars10=(sum(1 for value in gaps[-10:] if value > 0) if len(gaps) >= 10 else None),
            gapBars20=(sum(1 for value in gaps[-20:] if value > 0) if len(gaps) >= 20 else None),
            missingSecondsRecent=(sum(gaps[-10:]) // 1000 if len(gaps) >= 10 else None),
            currentCandleQuality=candle.quality,
            historyBars=len(self.closes),
            hydratedBars=self.hydrated_bars,
        )

    def status(self, quality: FeatureQuality) -> FeatureStatus:
        if self.bar_count < READY_BARS:
            return "WARMING"
        if (
            quality.goodRatio10 is None
            or quality.goodRatio10 < GOOD_RATIO_FLOOR
            or quality.meanCoverage10 is None
            or quality.meanCoverage10 < COVERAGE_FLOOR
        ):
            return "DEGRADED"
        return "READY"


def _log_bps(current: float, previous: float) -> float | None:
    if current <= 0 or previous <= 0:
        return None
    return finite(math.log(current / previous) * 10_000.0)


def _pair_bps(close: float, level: float | None) -> float | None:
    return None if level is None else bps(close, level)


def _both_bps(first: float | None, second: float | None) -> float | None:
    return None if first is None or second is None else bps(first, second)


def _scale_bps(value: float | None, close: float) -> float | None:
    if value is None:
        return None
    return None if close == 0 else finite(value / close * 10_000.0)


def _timeframe_diagnostics(timeframe: Timeframe, state: TimeframeState) -> TimeframeDiagnostics:
    latest = state.latest()
    return TimeframeDiagnostics(
        timeframe=timeframe,
        barCount=state.bar_count,
        hydratedBars=state.hydrated_bars,
        status=None if latest is None else latest.status,
        featureTime=None if latest is None else latest.featureTime,
    )


class SlotFeatureState:
    """All feature state for one (platform, slot) under one (asset, context) identity."""

    def __init__(self, platform: Platform, slot_id: int, asset_name: str, context_id: UUID) -> None:
        self.platform = platform
        self.slotId = slot_id
        self.assetName = asset_name
        self.contextId = context_id
        self.micro = MicroState()
        self.timeframes: dict[Timeframe, TimeframeState] = {
            timeframe: TimeframeState(timeframe) for timeframe in TIMEFRAMES
        }

    @property
    def identity(self) -> tuple[str, UUID]:
        return self.assetName, self.contextId

    @property
    def primary_timeframe(self) -> Timeframe:
        return PRIMARY_TIMEFRAME[self.platform]

    def bundle(self) -> FeatureBundle:
        primary_timeframe = self.primary_timeframe
        primary = self.timeframes[primary_timeframe].latest()
        as_of = primary.featureTime if primary is not None else self._micro_time()
        contexts: dict[str, FeatureSnapshot] = {}
        for timeframe, state in self.timeframes.items():
            if timeframe == primary_timeframe:
                continue
            snapshot = state.as_of(as_of)
            if snapshot is not None:
                contexts[timeframe] = snapshot
        return FeatureBundle(
            platform=self.platform,
            slotId=self.slotId,
            assetName=self.assetName,
            contextId=self.contextId,
            asOf=as_of,
            primaryTimeframe=primary_timeframe,
            featureVersion=FEATURE_VERSION,
            primary=primary,
            micro=self.micro.features(),
            contexts=contexts,
        )

    def _micro_time(self) -> int:
        return self.micro.history[-1][0] * 1000 if self.micro.history else 0

    def diagnostics(self) -> SlotFeatureDiagnostics:
        primary = self.timeframes[self.primary_timeframe].latest()
        return SlotFeatureDiagnostics(
            platform=self.platform,
            slotId=self.slotId,
            assetName=self.assetName,
            contextId=self.contextId,
            primaryTimeframe=self.primary_timeframe,
            featureVersion=FEATURE_VERSION,
            microSamples=self.micro.samples,
            timeframes=[
                _timeframe_diagnostics(timeframe, state)
                for timeframe, state in self.timeframes.items()
            ],
            quality=primary.quality if primary is not None else None,
        )


class FeatureEngine:
    """Event-driven entry point. One instance owns every live slot's feature state."""

    def __init__(self, hydrator: Hydrator | None = None) -> None:
        self.slots: dict[SlotKey, SlotFeatureState] = {}
        self.hydrator = hydrator
        self.rejected = 0

    def _state(
        self, platform: Platform, slot_id: int, asset_name: str, context_id: UUID
    ) -> SlotFeatureState:
        key: SlotKey = (platform, slot_id)
        state = self.slots.get(key)
        if state is not None and state.identity == (asset_name, context_id):
            return state
        fresh = SlotFeatureState(platform, slot_id, asset_name, context_id)
        self.slots[key] = fresh
        return fresh

    def ingest_second(self, sample: PriceSample) -> None:
        state = self._state(sample.platform, sample.slotId, sample.assetName, sample.contextId)
        stamp = sample.bucketTime if sample.bucketTime is not None else sample.timestamp
        state.micro.ingest(stamp // 1000, sample.price)

    def ingest_candle(self, candle: Candle) -> FeatureSnapshot | None:
        if candle.state != "CLOSED":
            return None
        state = self._state(candle.platform, candle.slotId, candle.assetName, candle.contextId)
        timeframe = state.timeframes[candle.timeframe]
        if candle.high < candle.low or not candle.low <= candle.close <= candle.high:
            self.rejected += 1
            return self._invalid(candle, timeframe)
        if timeframe.last_close_time is not None and candle.closeTime <= timeframe.last_close_time:
            self.rejected += 1
            return None
        self._hydrate(state, candle, timeframe)
        timeframe.apply(candle)
        snapshot = self._snapshot(state, candle, timeframe)
        timeframe.snapshots.append(snapshot)
        return snapshot

    def _hydrate(self, state: SlotFeatureState, candle: Candle, timeframe: TimeframeState) -> None:
        """Seed indicators from trusted earlier history the first time a series is seen."""
        if timeframe.hydrated:
            return
        timeframe.hydrated = True
        if self.hydrator is None or timeframe.bar_count:
            return
        for historical in self.hydrator(
            state.platform, state.assetName, candle.timeframe, candle.openTime
        ):
            timeframe.apply(historical, hydrated=True)

    def _snapshot(
        self, state: SlotFeatureState, candle: Candle, timeframe: TimeframeState
    ) -> FeatureSnapshot:
        quality = timeframe.quality(candle)
        return FeatureSnapshot(
            platform=state.platform,
            slotId=state.slotId,
            assetName=state.assetName,
            contextId=state.contextId,
            timeframe=candle.timeframe,
            featureTime=candle.closeTime,
            featureVersion=FEATURE_VERSION,
            status=timeframe.status(quality),
            barCount=timeframe.bar_count,
            sourceType=candle.sourceType,
            quality=quality,
            priceAction=timeframe.price_action(candle),
            trend=timeframe.trend(candle.close),
            momentum=timeframe.momentum(candle.close),
            volatility=timeframe.volatility(candle.close),
            structure=timeframe.structure(candle.close),
            noise=timeframe.noise(),
            timeContext=time_context(candle.closeTime, candle.timeframe, state.assetName),
            micro=state.micro.features(),
        )

    def _invalid(self, candle: Candle, timeframe: TimeframeState) -> FeatureSnapshot:
        """Report an impossible candle without letting it touch any rolling state."""
        return FeatureSnapshot(
            platform=candle.platform,
            slotId=candle.slotId,
            assetName=candle.assetName,
            contextId=candle.contextId,
            timeframe=candle.timeframe,
            featureTime=candle.closeTime,
            featureVersion=FEATURE_VERSION,
            status="INVALID",
            barCount=timeframe.bar_count,
            sourceType=candle.sourceType,
            quality=FeatureQuality(
                currentCandleQuality="INVALID",
                historyBars=len(timeframe.closes),
                hydratedBars=timeframe.hydrated_bars,
            ),
            priceAction=PriceActionFeatures(),
            trend=TrendFeatures(),
            momentum=MomentumFeatures(),
            volatility=VolatilityFeatures(),
            structure=StructureFeatures(),
            noise=NoiseFeatures(),
            timeContext=time_context(candle.closeTime, candle.timeframe, candle.assetName),
            micro=MicroFeatures(samples=0),
        )

    def reset_slot(self, platform: Platform, slot_ids: Iterable[int]) -> int:
        return sum(self.slots.pop((platform, slot_id), None) is not None for slot_id in slot_ids)

    def latest_snapshot(
        self, platform: Platform, slot_id: int, timeframe: Timeframe
    ) -> FeatureSnapshot | None:
        state = self.slots.get((platform, slot_id))
        return None if state is None else state.timeframes[timeframe].latest()

    def latest_bundle(self, platform: Platform, slot_id: int) -> FeatureBundle | None:
        state = self.slots.get((platform, slot_id))
        return None if state is None else state.bundle()

    def diagnostics(self) -> list[SlotFeatureDiagnostics]:
        return [state.diagnostics() for state in self.slots.values()]
