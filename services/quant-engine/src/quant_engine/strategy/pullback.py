"""trend_pullback_v1 — a temporary retracement inside a trend that is still intact.

The strategy separates two horizons that Phase 6 measures independently. The host trend is
read from the slow structure — EMA20 against EMA50, the twenty-bar slope, and whatever the
higher timeframes say. The pullback is read from the fast structure — where price sits
relative to EMA5, whether the oscillators have reset, and whether the one-second stream has
started pushing back the way the trend was going.

Direction always resumes the host trend. Retracing too far is not a pullback but a broken
trend, and the strategy says so rather than buying deeper into a reversal. It never fires in
a range, where "the trend" it would be resuming does not exist.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar, Final

from quant_engine.features.math import clamp
from quant_engine.strategy.base import Opinion, Strategy, abstain, direction_of, undecided
from quant_engine.strategy.common import (
    MICRO_EVIDENCE_FLOOR,
    Analysis,
    Evidence,
    aligned,
    context_direction,
    max_of,
    mean_of,
    ramp,
    signed_ramp,
)
from quant_engine.strategy.models import Reason, Regime, RegimeSnapshot
from quant_engine.strategy.policy import TREND_PULLBACK, PlatformPolicy

HOST_STACK_HALF: Final = 0.55
HOST_SLOPE_HALF: Final = 0.25
MIN_HOST_TREND: Final = 0.28
"""Host trend evidence below this leaves nothing for a pullback to be a pullback within."""
STRONG_HOST_TREND: Final = 0.75
HOST_GAIN_FLOOR: Final = 0.55
"""What the setup is worth on a host trend that only just clears the minimum."""

MIN_RETRACE: Final = 0.12
"""Retracement against the trend, in typical bar movements, before there is any pullback."""
IDEAL_RETRACE: Final = 0.60
"""A healthy retracement sits a fraction of a bar's movement below the fast average, not a
full one. Price a whole typical bar below EMA5 is already an unusual excursion, so treating
that as the ideal would score every ordinary pullback as barely present."""
MAX_RETRACE: Final = 2.20
"""Beyond this the move against the trend is larger than the trend's own bars; that is a
reversal candidate, not a continuation setup, and the strategy declines to call it either."""

RSI_HOT: Final = 80.0
RSI_RESET: Final = 55.0
"""A reset inside a trend is relative, not absolute. A sustained uptrend holds RSI in the
seventies and eighties, and demanding it fall to the classic oversold region before calling
a dip a pullback would mean only recognising pullbacks in trends that had already ended.
Mirrored around fifty for downtrends."""
STOCH_HOT: Final = 85.0
STOCH_RESET: Final = 40.0
"""Stochastic is measured against the last fourteen bars' own range, so it registers a
retracement faster than RSI does and carries the reset evidence when RSI barely moves."""

LEVEL_FAR: Final = 2.00
LEVEL_NEAR: Final = 0.20
MICRO_HALF: Final = 0.40
SLOPE_HALF: Final = 0.30

MIN_EVIDENCE: Final = 0.30

REGIME_FIT: Mapping[Regime, float] = {
    "TREND_UP": 1.00,
    "TREND_DOWN": 1.00,
    "BREAKOUT_UP": 0.60,
    "BREAKOUT_DOWN": 0.60,
    "VOLATILITY_EXPANSION": 0.50,
    "VOLATILITY_COMPRESSION": 0.45,
    "UNCERTAIN": 0.35,
    "RANGE": 0.00,
    "NOISY": 0.00,
}


class TrendPullback(Strategy):
    id: ClassVar[str] = TREND_PULLBACK
    required: ClassVar[tuple[str, ...]] = (
        "primary.trend.ema20",
        "primary.trend.priceSlope20",
        "primary.trend.priceToEma5Bps",
        "primary.momentum.rsi14",
    )
    optional: ClassVar[tuple[str, ...]] = (
        "primary.trend.ema20To50Bps",
        "primary.momentum.stochK14",
        "primary.structure.nearestSupportDistanceBps",
        "micro.microVelocity3s",
    )
    regime_fit: ClassVar[Mapping[Regime, float]] = REGIME_FIT
    min_evidence: ClassVar[float] = MIN_EVIDENCE

    def _host(self, state: Analysis, policy: PlatformPolicy) -> tuple[float | None, Evidence]:
        trend = state.primary.trend
        evidence = Evidence()
        evidence.add(
            "EMA20_TO_50",
            "EMA20 against EMA50",
            1.2,
            signed_ramp(state.norm(trend.ema20To50Bps), HOST_STACK_HALF),
            trend.ema20To50Bps,
        )
        evidence.add(
            "PRICE_TO_EMA20",
            "Price against EMA20",
            1.0,
            signed_ramp(state.norm(trend.priceToEma20Bps), HOST_STACK_HALF),
            trend.priceToEma20Bps,
        )
        evidence.add(
            "PRICE_SLOPE_20",
            "Twenty-bar price slope",
            1.2,
            signed_ramp(state.norm(trend.priceSlope20), HOST_SLOPE_HALF),
            trend.priceSlope20,
        )
        evidence.add(
            "EMA20_SLOPE",
            "EMA20 slope",
            1.0,
            signed_ramp(state.norm(trend.ema20Slope3), HOST_SLOPE_HALF),
            trend.ema20Slope3,
        )
        for timeframe, weight in policy.contextWeights.items():
            snapshot = state.context(timeframe)
            if snapshot is None or weight <= 0:
                continue
            evidence.add(
                f"CONTEXT_{timeframe}",
                f"{timeframe} structure agrees",
                weight,
                context_direction(snapshot),
                None,
            )
        return evidence.score, evidence

    def read(self, state: Analysis, regime: RegimeSnapshot, policy: PlatformPolicy) -> Opinion:
        host, host_evidence = self._host(state, policy)
        if host is None:
            return abstain("NO_EVIDENCE", "No host trend evidence could be measured")
        reasons: list[Reason] = list(host_evidence.reasons(limit=3))
        if abs(host) < MIN_HOST_TREND:
            return undecided(
                reasons,
                [Reason(code="NO_HOST_TREND", message="No trend to pull back within", value=host)],
                host_evidence.coverage,
            )
        sign = 1.0 if host > 0 else -1.0
        stretch = state.norm(state.primary.trend.priceToEma5Bps)
        if stretch is None:
            return abstain("NO_EVIDENCE", "Price distance from EMA5 is not measurable")
        retrace = -sign * stretch
        if retrace < MIN_RETRACE:
            return undecided(
                reasons,
                [
                    Reason(
                        code="NO_PULLBACK",
                        message="Price has not retraced against the trend",
                        value=retrace,
                    )
                ],
                host_evidence.coverage,
            )
        if retrace > MAX_RETRACE:
            return abstain(
                "TREND_BROKEN", "Retracement is larger than the trend it would resume", retrace
            )

        momentum, structure, price = (
            state.primary.momentum,
            state.primary.structure,
            state.primary.priceAction,
        )
        depth = ramp(retrace, MIN_RETRACE, IDEAL_RETRACE)
        rsi_reset = (
            ramp(momentum.rsi14, RSI_HOT, RSI_RESET)
            if sign > 0
            else ramp(momentum.rsi14, 100.0 - RSI_HOT, 100.0 - RSI_RESET)
        )
        stoch_reset = (
            ramp(momentum.stochK14, STOCH_HOT, STOCH_RESET)
            if sign > 0
            else ramp(momentum.stochK14, 100.0 - STOCH_HOT, 100.0 - STOCH_RESET)
        )
        level = ramp(
            state.norm(
                structure.nearestSupportDistanceBps
                if sign > 0
                else structure.nearestResistanceDistanceBps
            ),
            LEVEL_FAR,
            LEVEL_NEAR,
        )
        micro_push = (
            signed_ramp(state.micro_norm(state.micro.microVelocity3s), MICRO_HALF)
            if (state.micro.microCoverage10s or 0.0) >= MICRO_EVIDENCE_FLOOR
            else None
        )
        # Is price turning back into the trend? Each reading is graded on how much it agrees
        # with the host direction; one that points the other way withholds support rather
        # than cancelling out the readings that do.
        close_push = None if price.closeLocation is None else (price.closeLocation - 0.5) * 2.0
        resume = mean_of(
            [
                aligned(sign, micro_push),
                aligned(sign, signed_ramp(state.norm(state.primary.trend.priceSlope5), SLOPE_HALF)),
                aligned(sign, close_push),
            ]
        )
        # RSI and stochastic describe the same reset at different sensitivities, so the
        # clearer of the two carries it rather than the slower one halving the evidence.
        momentum_reset = max_of([rsi_reset, stoch_reset])
        setup = mean_of([depth, momentum_reset, level, resume])
        if setup is None:
            return abstain("NO_EVIDENCE", "The pullback setup could not be measured")
        gain = HOST_GAIN_FLOOR + (1 - HOST_GAIN_FLOOR) * (
            ramp(abs(host), MIN_HOST_TREND, STRONG_HOST_TREND) or 0.0
        )
        score = clamp(sign * setup * gain, -1.0, 1.0)
        direction = direction_of(score, self.min_evidence)
        reasons.append(
            Reason(code="RETRACEMENT", message="Pullback depth in bar moves", value=retrace)
        )
        if rsi_reset is not None:
            reasons.append(
                Reason(code="MOMENTUM_RESET", message="RSI14 has reset", value=momentum.rsi14)
            )
        if resume is not None:
            reasons.append(
                Reason(
                    code="RESUMPTION", message="Price is turning back into the trend", value=resume
                )
            )
        return Opinion(
            direction=direction,
            rawScore=score,
            confidence=abs(score) * host_evidence.coverage if direction != "NEUTRAL" else 0.0,
            coverage=host_evidence.coverage,
            reasons=reasons,
        )
