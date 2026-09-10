"""momentum_continuation_v1 — continuation only when independent families concur.

RSI alone is not momentum. The strategy groups its evidence into families that measure
different things — oscillators, rate of change, MACD, slope, one-second push — scores each
family on its own, and names a direction only when several of them point the same way. A
single loud family cannot carry the vote, and families that disagree produce NEUTRAL rather
than an average that hides the conflict.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar, Final

from quant_engine.features.math import clamp
from quant_engine.strategy.base import Opinion, Strategy, abstain, direction_of, undecided
from quant_engine.strategy.common import (
    MICRO_EVIDENCE_FLOOR,
    Analysis,
    mean_of,
    oscillator,
    signed_ramp,
)
from quant_engine.strategy.models import Reason, Regime, RegimeSnapshot
from quant_engine.strategy.policy import MOMENTUM_CONTINUATION, PlatformPolicy

RSI_SPAN: Final = 28.0
RSI_DEADBAND: Final = 6.0
STOCH_SPAN: Final = 35.0
STOCH_DEADBAND: Final = 10.0
ROC_HALF: Final = 1.10
"""Rate of change, in typical bar movements, that scores ±0.5."""
MACD_HALF: Final = 0.45
SLOPE_HALF: Final = 0.25
MICRO_HALF: Final = 0.30

FAMILY_DEADBAND: Final = 0.12
"""A family below this is present but says nothing; it neither agrees nor disagrees."""
MIN_FAMILIES: Final = 3
"""Families that must be measurable at all before a direction is possible."""
MIN_AGREEING_FAMILIES: Final = 3
"""Families that must point the same way. Two agreeing families is a coincidence."""

MIN_EVIDENCE: Final = 0.28

FLIP_CEILING: Final = 0.75
CHOP_CEILING: Final = 68.0
OVERLAP_CEILING: Final = 0.85
"""Above any of these the recent path reverses too often for continuation to mean anything."""

REGIME_FIT: Mapping[Regime, float] = {
    "TREND_UP": 1.00,
    "TREND_DOWN": 1.00,
    "BREAKOUT_UP": 0.90,
    "BREAKOUT_DOWN": 0.90,
    "VOLATILITY_EXPANSION": 0.75,
    "UNCERTAIN": 0.45,
    "VOLATILITY_COMPRESSION": 0.35,
    "RANGE": 0.25,
    "NOISY": 0.05,
}


@dataclass(frozen=True, slots=True)
class Family:
    code: str
    label: str
    weight: float
    score: float | None
    value: float | None = None


class MomentumContinuation(Strategy):
    id: ClassVar[str] = MOMENTUM_CONTINUATION
    required: ClassVar[tuple[str, ...]] = (
        "primary.momentum.rsi14",
        "primary.momentum.roc5Bps",
        "primary.momentum.macdHistogramBps",
    )
    optional: ClassVar[tuple[str, ...]] = (
        "primary.momentum.stochK14",
        "primary.momentum.roc10Bps",
        "primary.trend.priceSlope5",
        "micro.microVelocity3s",
    )
    regime_fit: ClassVar[Mapping[Regime, float]] = REGIME_FIT
    min_evidence: ClassVar[float] = MIN_EVIDENCE

    def _families(self, state: Analysis, policy: PlatformPolicy) -> list[Family]:
        momentum, trend = state.primary.momentum, state.primary.trend
        families = [
            Family(
                "OSCILLATORS",
                "RSI and stochastic away from the midpoint",
                0.9,
                mean_of(
                    [
                        oscillator(momentum.rsi14, 50.0, RSI_SPAN, RSI_DEADBAND),
                        oscillator(momentum.stochK14, 50.0, STOCH_SPAN, STOCH_DEADBAND),
                    ]
                ),
                momentum.rsi14,
            ),
            Family(
                "RATE_OF_CHANGE",
                "Five- and ten-bar rate of change",
                1.0,
                mean_of(
                    [
                        signed_ramp(state.norm(momentum.roc5Bps), ROC_HALF),
                        signed_ramp(state.norm(momentum.roc10Bps), ROC_HALF),
                    ]
                ),
                momentum.roc5Bps,
            ),
            Family(
                "MACD",
                "MACD histogram and line",
                1.0,
                mean_of(
                    [
                        signed_ramp(state.norm(momentum.macdHistogramBps), MACD_HALF),
                        signed_ramp(state.norm(momentum.macdBps), MACD_HALF),
                    ]
                ),
                momentum.macdHistogramBps,
            ),
            Family(
                "SLOPE",
                "Five-bar price slope",
                0.8,
                signed_ramp(state.norm(trend.priceSlope5), SLOPE_HALF),
                trend.priceSlope5,
            ),
        ]
        if (state.micro.microCoverage10s or 0.0) >= MICRO_EVIDENCE_FLOOR:
            families.append(
                Family(
                    "MICRO_PUSH",
                    "One-second velocity",
                    0.7 * policy.microWeight,
                    mean_of(
                        [
                            signed_ramp(state.micro_norm(state.micro.microVelocity3s), MICRO_HALF),
                            signed_ramp(state.micro_norm(state.micro.microVelocity5s), MICRO_HALF),
                        ]
                    ),
                    state.micro.microVelocity5s,
                )
            )
        return families

    def read(self, state: Analysis, regime: RegimeSnapshot, policy: PlatformPolicy) -> Opinion:
        noise = state.primary.noise
        for value, ceiling, code, message in (
            (noise.signFlipRate10, FLIP_CEILING, "SIGN_FLIPS", "Returns change sign too often"),
            (noise.choppiness14, CHOP_CEILING, "CHOPPINESS", "Choppiness leaves no continuation"),
            (
                noise.rangeOverlap5,
                OVERLAP_CEILING,
                "RANGE_OVERLAP",
                "Bar ranges almost fully overlap",
            ),
        ):
            if value is not None and value >= ceiling:
                return abstain(code, message, value)

        families = self._families(state, policy)
        present = [family for family in families if family.score is not None]
        coverage = clamp(
            sum(family.weight for family in present)
            / max(sum(family.weight for family in families), 1e-9),
            0.0,
            1.0,
        )
        reasons = [
            Reason(code=family.code, message=family.label, value=family.value)
            for family in sorted(present, key=lambda item: -abs(item.score or 0.0))[:4]
        ]
        if len(present) < MIN_FAMILIES:
            return abstain(
                "INSUFFICIENT_FAMILIES",
                f"Only {len(present)} momentum families measurable",
                float(len(present)),
            )
        weighted = sum((family.score or 0.0) * family.weight for family in present)
        total = sum(family.weight for family in present)
        raw = clamp(weighted / total, -1.0, 1.0)
        directional = [family for family in present if abs(family.score or 0.0) >= FAMILY_DEADBAND]
        sign = 1.0 if raw >= 0 else -1.0
        agreeing = [family for family in directional if (family.score or 0.0) * sign > 0]
        if len(agreeing) < MIN_AGREEING_FAMILIES:
            return undecided(
                reasons,
                [
                    Reason(
                        code="FAMILIES_DISAGREE",
                        message=f"Only {len(agreeing)} of {len(directional)} families concur",
                        value=float(len(agreeing)),
                    )
                ],
                coverage,
            )
        concord = len(agreeing) / max(len(directional), 1)
        score = clamp(raw * concord, -1.0, 1.0)
        direction = direction_of(score, self.min_evidence)
        return Opinion(
            direction=direction,
            rawScore=score,
            confidence=abs(score) * coverage if direction != "NEUTRAL" else 0.0,
            coverage=coverage,
            reasons=reasons,
        )
