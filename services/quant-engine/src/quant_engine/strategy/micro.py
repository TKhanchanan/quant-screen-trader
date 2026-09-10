"""micro_impulse_v1 — the one-second stream, where it is dense enough to read.

Primary target is CapitalBear's five-second horizon, where a clean two-second push is a
material share of the decision window. It also runs on IQ Option, at a much lower platform
fit, as secondary confirmation rather than a decision of its own.

Coverage comes first. A ten-second window with four real seconds in it describes a guess,
not a market, so the strategy abstains outright rather than reading a sparse series. Sign
flips and inefficiency then reduce what a measured impulse is worth: a fast series that keeps
changing its mind is volatility, not direction.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar, Final

from quant_engine.features.math import clamp
from quant_engine.strategy.base import Opinion, Strategy, abstain, direction_of
from quant_engine.strategy.common import Analysis, Evidence, ramp, signed_ramp
from quant_engine.strategy.models import Reason, Regime, RegimeSnapshot
from quant_engine.strategy.policy import MICRO_IMPULSE, PlatformPolicy

COVERAGE_FLOOR: Final = 0.60
"""Share of the last ten seconds that must actually exist before anything is read."""
FLIP_CEILING: Final = 0.65
"""One-second returns that change sign this often are noise at this resolution."""

RETURN_HALF: Final = 2.00
"""A multi-second return, in one-second volatilities, that scores ±0.5."""
VELOCITY_HALF: Final = 0.50
ACCELERATION_HALF: Final = 1.50
BAR_HALF: Final = 1.00

EFFICIENCY_ZERO: Final = 0.20
EFFICIENCY_ONE: Final = 0.70
EFFICIENCY_FLOOR: Final = 0.30
"""How much of a measured impulse survives a completely inefficient one-second path."""

MIN_EVIDENCE: Final = 0.32

REGIME_FIT: Mapping[Regime, float] = {
    "BREAKOUT_UP": 1.00,
    "BREAKOUT_DOWN": 1.00,
    "TREND_UP": 0.90,
    "TREND_DOWN": 0.90,
    "VOLATILITY_EXPANSION": 0.80,
    "UNCERTAIN": 0.55,
    "VOLATILITY_COMPRESSION": 0.45,
    "RANGE": 0.35,
    "NOISY": 0.05,
}


class MicroImpulse(Strategy):
    id: ClassVar[str] = MICRO_IMPULSE
    required: ClassVar[tuple[str, ...]] = (
        "micro.microVelocity5s",
        "micro.microEfficiency5s",
        "micro.microCoverage10s",
    )
    optional: ClassVar[tuple[str, ...]] = (
        "micro.microReturn3sBps",
        "micro.microAcceleration1s",
        "micro.microSignFlipRate10s",
        "primary.priceAction.return1Bps",
    )
    regime_fit: ClassVar[Mapping[Regime, float]] = REGIME_FIT
    min_evidence: ClassVar[float] = MIN_EVIDENCE

    def read(self, state: Analysis, regime: RegimeSnapshot, policy: PlatformPolicy) -> Opinion:
        micro = state.micro
        coverage_10s = micro.microCoverage10s
        if coverage_10s is None or coverage_10s < COVERAGE_FLOOR:
            return abstain(
                "LOW_COVERAGE",
                "Too few of the last ten seconds actually exist",
                coverage_10s,
            )
        flips = micro.microSignFlipRate10s
        if flips is not None and flips >= FLIP_CEILING:
            return abstain("MICRO_SIGN_FLIPS", "One-second returns keep changing sign", flips)

        evidence = Evidence()
        evidence.add(
            "MICRO_RETURN_3S",
            "Three-second return",
            0.9,
            signed_ramp(state.micro_norm(micro.microReturn3sBps), RETURN_HALF),
            micro.microReturn3sBps,
        )
        evidence.add(
            "MICRO_RETURN_5S",
            "Five-second return",
            1.0,
            signed_ramp(state.micro_norm(micro.microReturn5sBps), RETURN_HALF),
            micro.microReturn5sBps,
        )
        evidence.add(
            "MICRO_VELOCITY_3S",
            "Three-second velocity",
            1.0,
            signed_ramp(state.micro_norm(micro.microVelocity3s), VELOCITY_HALF),
            micro.microVelocity3s,
        )
        evidence.add(
            "MICRO_VELOCITY_5S",
            "Five-second velocity",
            1.0,
            signed_ramp(state.micro_norm(micro.microVelocity5s), VELOCITY_HALF),
            micro.microVelocity5s,
        )
        evidence.add(
            "MICRO_ACCELERATION",
            "One-second acceleration",
            0.6,
            signed_ramp(state.micro_norm(micro.microAcceleration1s), ACCELERATION_HALF),
            micro.microAcceleration1s,
        )
        evidence.add(
            "BAR_AGREEMENT",
            "The closed bar agrees",
            0.5,
            signed_ramp(state.norm(state.primary.priceAction.return1Bps), BAR_HALF),
            state.primary.priceAction.return1Bps,
        )
        raw = evidence.score
        if raw is None:
            return abstain("NO_EVIDENCE", "No one-second evidence could be measured")
        efficiency = ramp(micro.microEfficiency5s, EFFICIENCY_ZERO, EFFICIENCY_ONE)
        gain = 1.0 if efficiency is None else EFFICIENCY_FLOOR + (1 - EFFICIENCY_FLOOR) * efficiency
        score = clamp(raw * gain, -1.0, 1.0)
        direction = direction_of(score, self.min_evidence)
        reasons: list[Reason] = list(evidence.reasons(limit=4))
        reasons.append(
            Reason(code="MICRO_COVERAGE", message="Ten-second coverage", value=coverage_10s)
        )
        # Coverage multiplies confidence a second time on purpose: a barely-admissible stream
        # may still be read, but never with the conviction of a complete one.
        return Opinion(
            direction=direction,
            rawScore=score,
            confidence=abs(score) * evidence.coverage * coverage_10s
            if direction != "NEUTRAL"
            else 0.0,
            coverage=evidence.coverage,
            reasons=reasons,
        )
