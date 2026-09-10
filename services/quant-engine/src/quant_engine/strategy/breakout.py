"""breakout_v1 — a level was taken out, and the bar that took it out meant it.

Two things have to be true. A close must actually sit beyond a prior extreme; touching a
level, or wicking through it and closing back inside, is not a break. And the bar that did it
must carry conviction — an expanded range, a real body, a close near its own extreme and a
path that went somewhere. Confirmation multiplies the direction, so a break with none of it
scores near zero and the strategy reports NEUTRAL rather than a breakout that is only a fact
about a price crossing a number.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar, Final

from quant_engine.features.math import clamp
from quant_engine.strategy.base import Opinion, Strategy, abstain, direction_of, undecided
from quant_engine.strategy.common import (
    EPSILON,
    MICRO_EVIDENCE_FLOOR,
    Analysis,
    BreakoutConfirmation,
    Evidence,
    breakout_sign,
    mean_of,
    signed_ramp,
)
from quant_engine.strategy.models import Reason, Regime, RegimeSnapshot
from quant_engine.strategy.policy import BREAKOUT, PlatformPolicy

WINDOW_WEIGHTS: Final = ((20, 1.4), (10, 1.0), (5, 0.6))
BB_Z_HALF: Final = 1.60
MICRO_HALF: Final = 2.00

CONFIRMATION: Final = BreakoutConfirmation(
    body_zero=0.35,
    body_one=0.75,
    close_zero=0.55,
    close_one=0.90,
    expansion_zero=0.95,
    expansion_one=1.90,
    efficiency_zero=0.25,
    efficiency_one=0.65,
    context_floor=0.40,
)
"""Close location is measured in the break's own direction, so a down break wants a low
close; true range is measured against ATR, ordinary at parity and decisive near two."""

MIN_CONFIRMATION: Final = 0.35
"""Below this the level cross has no bar behind it and the strategy reports NEUTRAL."""
MIN_EVIDENCE: Final = 0.25
NOISE_CEILING: Final = 0.68

REGIME_FIT: Mapping[Regime, float] = {
    "BREAKOUT_UP": 1.00,
    "BREAKOUT_DOWN": 1.00,
    "VOLATILITY_EXPANSION": 0.90,
    "TREND_UP": 0.70,
    "TREND_DOWN": 0.70,
    "VOLATILITY_COMPRESSION": 0.50,
    "UNCERTAIN": 0.40,
    "RANGE": 0.30,
    "NOISY": 0.05,
}


class Breakout(Strategy):
    id: ClassVar[str] = BREAKOUT
    required: ClassVar[tuple[str, ...]] = (
        "primary.structure.priorHigh10",
        "primary.structure.priorLow10",
        "primary.priceAction.bodyToRange",
        "primary.priceAction.closeLocation",
    )
    optional: ClassVar[tuple[str, ...]] = (
        "primary.volatility.rangeExpansion",
        "primary.volatility.bbZScore",
        "primary.noise.efficiencyRatio10",
        "micro.microReturn5sBps",
    )
    regime_fit: ClassVar[Mapping[Regime, float]] = REGIME_FIT
    min_evidence: ClassVar[float] = MIN_EVIDENCE

    def read(self, state: Analysis, regime: RegimeSnapshot, policy: PlatformPolicy) -> Opinion:
        if regime.noiseScore >= NOISE_CEILING:
            return abstain(
                "EXTREME_NOISE", "Levels are meaningless in this path", regime.noiseScore
            )

        structure, price = state.primary.structure, state.primary.priceAction
        volatility, noise = state.primary.volatility, state.primary.noise
        evidence = Evidence()
        thrusts: list[float | None] = []
        for window, weight in WINDOW_WEIGHTS:
            sign = breakout_sign(
                getattr(structure, f"abovePriorHigh{window}"),
                getattr(structure, f"belowPriorLow{window}"),
            )
            thrusts.append(sign)
            evidence.add(
                f"THRUST_{window}",
                f"Close beyond the prior {window}-bar extreme",
                weight,
                sign,
                sign,
            )
        thrust = mean_of(thrusts)
        if thrust is None:
            return abstain("NO_PRIOR_RANGE", "No prior extreme available to break")
        if abs(thrust) < EPSILON:
            return undecided(
                evidence.reasons(limit=2),
                [Reason(code="NO_BREAK", message="Price is still inside its prior range")],
                evidence.coverage,
            )
        evidence.add(
            "CLOSE_LOCATION",
            "Close within the bar range",
            0.7,
            None if price.closeLocation is None else (price.closeLocation - 0.5) * 2.0,
            price.closeLocation,
        )
        evidence.add(
            "BOLLINGER_Z",
            "Close against the Bollinger mean",
            0.7,
            signed_ramp(volatility.bbZScore, BB_Z_HALF),
            volatility.bbZScore,
        )
        if (state.micro.microCoverage10s or 0.0) >= MICRO_EVIDENCE_FLOOR:
            evidence.add(
                "MICRO_PUSH",
                "One-second push into the break",
                0.6 * policy.microWeight,
                signed_ramp(state.micro_norm(state.micro.microReturn5sBps), MICRO_HALF),
                state.micro.microReturn5sBps,
            )
        direction_score = evidence.score
        if direction_score is None:
            return abstain("NO_EVIDENCE", "No breakout evidence could be measured")
        if direction_score * thrust <= 0:
            return undecided(
                evidence.reasons(limit=3),
                [
                    Reason(
                        code="BREAK_REJECTED",
                        message="Closed beyond a level but the bar points the other way",
                        value=direction_score,
                    )
                ],
                evidence.coverage,
            )
        confirmation = CONFIRMATION.score(
            body_to_range=price.bodyToRange,
            close_location=price.closeLocation,
            thrust=thrust,
            range_expansion=volatility.rangeExpansion,
            efficiency=noise.efficiencyRatio10,
        )
        reasons: list[Reason] = list(evidence.reasons(limit=3))
        if confirmation is None or confirmation < MIN_CONFIRMATION:
            return undecided(
                reasons,
                [
                    Reason(
                        code="WEAK_CONFIRMATION",
                        message="The bar that broke the level carried no conviction",
                        value=confirmation,
                    )
                ],
                evidence.coverage,
            )
        reasons.append(
            Reason(
                code="CONFIRMATION", message="Expansion, body and close agree", value=confirmation
            )
        )
        score = clamp(direction_score * confirmation, -1.0, 1.0)
        direction = direction_of(score, self.min_evidence)
        return Opinion(
            direction=direction,
            rawScore=score,
            confidence=abs(score) * evidence.coverage if direction != "NEUTRAL" else 0.0,
            coverage=evidence.coverage,
            reasons=reasons,
        )
