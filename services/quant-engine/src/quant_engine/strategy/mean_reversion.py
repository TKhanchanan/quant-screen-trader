"""mean_reversion_v1 — fade a stretch, but only where a stretch means anything.

An oversold reading inside a strong downtrend is not a reversion setup; it is the trend
working. So this strategy is eligible only where the regime supports ranging behaviour, and
it carries a second, independent guard on raw trend pressure for the case where the label
says RANGE but the trend evidence says otherwise. The regime's own range score then scales
the result, so a barely-ranging market produces a barely-held opinion.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar, Final

from quant_engine.features.math import clamp
from quant_engine.strategy.base import Opinion, Strategy, abstain, direction_of
from quant_engine.strategy.common import (
    Analysis,
    Evidence,
    negate,
    oscillator,
    ramp,
    signed_ramp,
)
from quant_engine.strategy.models import Reason, Regime, RegimeSnapshot
from quant_engine.strategy.policy import MEAN_REVERSION, PlatformPolicy

PERCENT_B_HALF: Final = 0.70
"""Distance of %B from the band midpoint, doubled to -1..1, that scores ±0.5."""
Z_HALF: Final = 1.60
RSI_SPAN: Final = 30.0
RSI_DEADBAND: Final = 12.0
"""RSI inside 38-62 is not a stretch worth fading."""
STOCH_SPAN: Final = 35.0
STOCH_DEADBAND: Final = 15.0
LEVEL_FAR: Final = 2.00
LEVEL_NEAR: Final = 0.15
"""Distance to a confirmed pivot level, in typical bar movements, from irrelevant to touching."""

TREND_CEILING: Final = 0.45
"""Trend pressure above which a stretch is the trend working, not an excursion to fade.
Independent of the regime label, which may read RANGE while the trend score disagrees."""

RANGE_GAIN_FLOOR: Final = 0.35
"""What the evidence is worth when the range reading is at its weakest admissible level."""

MIN_EVIDENCE: Final = 0.28

REGIME_FIT: Mapping[Regime, float] = {
    "RANGE": 1.00,
    "VOLATILITY_COMPRESSION": 0.75,
    "UNCERTAIN": 0.35,
    "VOLATILITY_EXPANSION": 0.15,
    "NOISY": 0.15,
    "TREND_UP": 0.00,
    "TREND_DOWN": 0.00,
    "BREAKOUT_UP": 0.00,
    "BREAKOUT_DOWN": 0.00,
}


class MeanReversion(Strategy):
    id: ClassVar[str] = MEAN_REVERSION
    required: ClassVar[tuple[str, ...]] = (
        "primary.volatility.bbPercentB",
        "primary.momentum.rsi14",
        "primary.noise.efficiencyRatio10",
    )
    optional: ClassVar[tuple[str, ...]] = (
        "primary.volatility.bbZScore",
        "primary.momentum.stochK14",
        "primary.structure.nearestSupportDistanceBps",
        "primary.structure.nearestResistanceDistanceBps",
    )
    regime_fit: ClassVar[Mapping[Regime, float]] = REGIME_FIT
    min_evidence: ClassVar[float] = MIN_EVIDENCE

    def read(self, state: Analysis, regime: RegimeSnapshot, policy: PlatformPolicy) -> Opinion:
        if abs(regime.trendScore) >= TREND_CEILING:
            return abstain(
                "TREND_PRESSURE",
                "A stretch inside a trend this strong is the trend, not an excursion",
                regime.trendScore,
            )
        volatility, momentum, structure = (
            state.primary.volatility,
            state.primary.momentum,
            state.primary.structure,
        )
        evidence = Evidence()
        percent_b = volatility.bbPercentB
        # Every member is negated: this strategy fades the stretch, so evidence that price is
        # extended upward is evidence for a move down.
        band = None if percent_b is None else (percent_b - 0.5) * 2.0
        evidence.add(
            "BOLLINGER_PERCENT_B",
            "Close against the Bollinger band",
            1.2,
            negate(signed_ramp(band, PERCENT_B_HALF)),
            percent_b,
        )
        evidence.add(
            "BOLLINGER_Z",
            "Close against the Bollinger mean",
            1.0,
            negate(signed_ramp(volatility.bbZScore, Z_HALF)),
            volatility.bbZScore,
        )
        evidence.add(
            "RSI_STRETCH",
            "RSI14 stretched from fifty",
            1.0,
            negate(oscillator(momentum.rsi14, 50.0, RSI_SPAN, RSI_DEADBAND)),
            momentum.rsi14,
        )
        evidence.add(
            "STOCHASTIC_STRETCH",
            "Stochastic stretched from fifty",
            0.7,
            negate(oscillator(momentum.stochK14, 50.0, STOCH_SPAN, STOCH_DEADBAND)),
            momentum.stochK14,
        )
        support = ramp(state.norm(structure.nearestSupportDistanceBps), LEVEL_FAR, LEVEL_NEAR)
        resistance = ramp(state.norm(structure.nearestResistanceDistanceBps), LEVEL_FAR, LEVEL_NEAR)
        if support is not None or resistance is not None:
            evidence.add(
                "LEVEL_PROXIMITY",
                "Distance to the nearest confirmed pivot level",
                0.8,
                (support or 0.0) - (resistance or 0.0),
                structure.nearestSupportDistanceBps,
            )
        raw = evidence.score
        if raw is None:
            return abstain("NO_EVIDENCE", "No reversion evidence could be measured")
        gain = RANGE_GAIN_FLOOR + (1.0 - RANGE_GAIN_FLOOR) * regime.rangeScore
        score = clamp(raw * gain, -1.0, 1.0)
        direction = direction_of(score, self.min_evidence)
        reasons: list[Reason] = list(evidence.reasons(limit=4))
        reasons.append(
            Reason(code="RANGE_SUPPORT", message="Regime range score", value=regime.rangeScore)
        )
        return Opinion(
            direction=direction,
            rawScore=score,
            confidence=abs(score) * evidence.coverage if direction != "NEUTRAL" else 0.0,
            coverage=evidence.coverage,
            reasons=reasons,
        )
