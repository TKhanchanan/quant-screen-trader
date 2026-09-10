"""trend_follow_v1 — participate only when trend evidence is coherent.

The strategy asks one question: do the moving averages, the slopes, the momentum and the
wider structure all describe the same direction, reached by a path that actually went there?
A stacked set of EMAs arrived at by a series of reversals is not a trend, and the coherence
gain says so. A bar that has already exploded is not an entry, and the chasing veto says so.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar, Final

from quant_engine.features.math import clamp
from quant_engine.strategy.base import (
    Opinion,
    Strategy,
    abstain,
    direction_of,
)
from quant_engine.strategy.common import (
    MICRO_EVIDENCE_FLOOR,
    Analysis,
    Evidence,
    context_direction,
    mean_of,
    oscillator,
    ramp,
    signed_ramp,
)
from quant_engine.strategy.models import Reason, Regime, RegimeSnapshot
from quant_engine.strategy.policy import TREND_FOLLOW, PlatformPolicy

STACK_HALF: Final = 0.50
STRETCH_HALF: Final = 0.90
SLOPE_HALF: Final = 0.22
MICRO_HALF: Final = 0.25
RSI_SPAN: Final = 25.0
RSI_DEADBAND: Final = 5.0

COHERENCE_ZERO: Final = 0.18
COHERENCE_ONE: Final = 0.62
COHERENCE_FLOOR: Final = 0.35
"""How much of the directional evidence survives a completely incoherent path."""

MIN_EVIDENCE: Final = 0.30
"""Signed evidence needed before this strategy names a direction rather than NEUTRAL."""

RANGE_CEILING: Final = 0.72
"""A range this convincing overrides whatever label the regime settled on."""
NOISE_CEILING: Final = 0.70
CHASE_EXPANSION_CEILING: Final = 2.60
"""True range this many ATRs wide means the move has already happened. The strategy does not
model entry timing, so it declines to add evidence to a bar it cannot characterise."""

REGIME_FIT: Mapping[Regime, float] = {
    "TREND_UP": 1.00,
    "TREND_DOWN": 1.00,
    "BREAKOUT_UP": 0.80,
    "BREAKOUT_DOWN": 0.80,
    "VOLATILITY_EXPANSION": 0.70,
    "VOLATILITY_COMPRESSION": 0.40,
    "UNCERTAIN": 0.45,
    "RANGE": 0.10,
    "NOISY": 0.05,
}


def _stack(first: float | None, second: float | None, third: float | None) -> float | None:
    """+1 for a cleanly ascending set of averages, -1 descending, 0 when they interleave."""
    if first is None or second is None or third is None:
        return None
    if first > second > third:
        return 1.0
    if first < second < third:
        return -1.0
    return 0.0


class TrendFollow(Strategy):
    id: ClassVar[str] = TREND_FOLLOW
    required: ClassVar[tuple[str, ...]] = (
        "primary.trend.ema9",
        "primary.trend.ema20",
        "primary.trend.priceSlope10",
    )
    optional: ClassVar[tuple[str, ...]] = (
        "primary.trend.ema5",
        "primary.momentum.rsi14",
        "primary.noise.efficiencyRatio10",
        "micro.microVelocity5s",
    )
    regime_fit: ClassVar[Mapping[Regime, float]] = REGIME_FIT
    min_evidence: ClassVar[float] = MIN_EVIDENCE

    def read(self, state: Analysis, regime: RegimeSnapshot, policy: PlatformPolicy) -> Opinion:
        if regime.rangeScore >= RANGE_CEILING:
            return abstain("RANGE_MARKET", "Range evidence overwhelms trend", regime.rangeScore)
        if regime.noiseScore >= NOISE_CEILING:
            return abstain("EXTREME_NOISE", "Path too noisy to follow", regime.noiseScore)
        expansion = state.primary.volatility.rangeExpansion
        if expansion is not None and expansion >= CHASE_EXPANSION_CEILING:
            return abstain(
                "CHASING_RISK", "Late explosive bar; the move already happened", expansion
            )

        trend, price = state.primary.trend, state.primary.priceAction
        evidence = Evidence()
        evidence.add(
            "EMA_STACK",
            "EMA5 > EMA9 > EMA20",
            1.2,
            _stack(trend.ema5, trend.ema9, trend.ema20),
            trend.ema5To20Bps,
        )
        evidence.add(
            "EMA_SEPARATION",
            "EMA9 against EMA20",
            1.0,
            signed_ramp(state.norm(trend.ema9To20Bps), STACK_HALF),
            trend.ema9To20Bps,
        )
        evidence.add(
            "PRICE_TO_EMA9",
            "Price against EMA9",
            1.0,
            signed_ramp(state.norm(trend.priceToEma9Bps), STRETCH_HALF),
            trend.priceToEma9Bps,
        )
        for code, label, weight, value in (
            ("EMA9_SLOPE", "EMA9 slope", 0.9, trend.ema9Slope3),
            ("EMA20_SLOPE", "EMA20 slope", 1.0, trend.ema20Slope3),
            ("PRICE_SLOPE_10", "Ten-bar price slope", 1.0, trend.priceSlope10),
            ("PRICE_SLOPE_20", "Twenty-bar price slope", 0.9, trend.priceSlope20),
        ):
            evidence.add(code, label, weight, signed_ramp(state.norm(value), SLOPE_HALF), value)
        evidence.add(
            "RSI",
            "RSI14 away from fifty",
            0.5,
            oscillator(state.primary.momentum.rsi14, 50.0, RSI_SPAN, RSI_DEADBAND),
            state.primary.momentum.rsi14,
        )
        evidence.add(
            "CLOSE_LOCATION",
            "Close within the bar range",
            0.4,
            None if price.closeLocation is None else (price.closeLocation - 0.5) * 2.0,
            price.closeLocation,
        )
        if (state.micro.microCoverage10s or 0.0) >= MICRO_EVIDENCE_FLOOR:
            evidence.add(
                "MICRO_VELOCITY",
                "One-second velocity agrees",
                0.8 * policy.microWeight,
                signed_ramp(state.micro_norm(state.micro.microVelocity5s), MICRO_HALF),
                state.micro.microVelocity5s,
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

        raw = evidence.score
        if raw is None:
            return abstain("NO_EVIDENCE", "No trend evidence could be measured")
        noise = state.primary.noise
        coherence = mean_of(
            [
                ramp(noise.efficiencyRatio10, COHERENCE_ZERO, COHERENCE_ONE),
                ramp(noise.efficiencyRatio20, COHERENCE_ZERO, COHERENCE_ONE),
            ]
        )
        gain = 1.0 if coherence is None else COHERENCE_FLOOR + (1 - COHERENCE_FLOOR) * coherence
        score = clamp(raw * gain, -1.0, 1.0)
        direction = direction_of(score, self.min_evidence)
        reasons: list[Reason] = list(evidence.reasons(limit=4))
        if coherence is not None:
            reasons.append(
                Reason(code="PATH_COHERENCE", message="Efficiency of the path", value=coherence)
            )
        return Opinion(
            direction=direction,
            rawScore=score,
            confidence=abs(score) * evidence.coverage if direction != "NEUTRAL" else 0.0,
            coverage=evidence.coverage,
            reasons=reasons,
        )
