"""Per-platform policy. Data only: no strategy arithmetic lives here.

Each broker decides on a different horizon, so the same strategy is not worth the same on
both. CapitalBear settles five-second bars and the one-second stream is a first-class input;
IQ Option settles one-minute bars and the higher timeframes carry the structure. Keeping
that as a table means a policy change never edits a formula, and a formula change never
quietly re-tunes a platform.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from quant_engine.configuration import Platform
from quant_engine.market_models import Timeframe

TREND_FOLLOW: Final = "trend_follow_v1"
MOMENTUM_CONTINUATION: Final = "momentum_continuation_v1"
BREAKOUT: Final = "breakout_v1"
MEAN_REVERSION: Final = "mean_reversion_v1"
MICRO_IMPULSE: Final = "micro_impulse_v1"
TREND_PULLBACK: Final = "trend_pullback_v1"

STRATEGY_IDS: Final = (
    TREND_FOLLOW,
    MOMENTUM_CONTINUATION,
    BREAKOUT,
    MEAN_REVERSION,
    MICRO_IMPULSE,
    TREND_PULLBACK,
)

BASE_WEIGHTS: Final[Mapping[str, float]] = MappingProxyType(
    {
        TREND_FOLLOW: 1.00,
        MOMENTUM_CONTINUATION: 0.85,
        BREAKOUT: 1.00,
        MEAN_REVERSION: 0.85,
        MICRO_IMPULSE: 0.90,
        TREND_PULLBACK: 0.90,
    }
)
"""Initial heuristic priors, not optimized values.

Nothing here was fitted to data. They encode only how much independent information each
strategy is expected to add, and they are deliberately close together so no single member
can carry the panel. Calibration belongs to replay and shadow phases, not to this file."""


@dataclass(frozen=True, slots=True)
class PlatformPolicy:
    """How much each input family is worth on one broker."""

    platform: Platform
    primary: Timeframe
    microWeight: float
    """Multiplier on every one-second evidence member."""
    contextWeights: Mapping[Timeframe, float]
    """Multiplier on higher-timeframe agreement. An absent timeframe is worth nothing."""
    strategyFit: Mapping[str, float]
    """``platformFit`` in the ensemble vote: how well a strategy suits this broker at all."""

    def context_weight(self, timeframe: Timeframe) -> float:
        return self.contextWeights.get(timeframe, 0.0)

    def fit(self, strategy_id: str) -> float:
        return self.strategyFit.get(strategy_id, 0.0)


CAPITALBEAR: Final = PlatformPolicy(
    platform="capitalbear",
    primary="S5",
    microWeight=1.0,
    contextWeights=MappingProxyType({"M1": 0.60, "M5": 0.20}),
    strategyFit=MappingProxyType(
        {
            TREND_FOLLOW: 0.85,
            MOMENTUM_CONTINUATION: 0.90,
            BREAKOUT: 0.90,
            MEAN_REVERSION: 0.85,
            MICRO_IMPULSE: 1.00,
            TREND_PULLBACK: 0.80,
        }
    ),
)
"""Five-second decisions. The one-second stream is at full weight and M1 is the structure a
five-second bar sits inside; M5 is background only. Bar-count-hungry strategies are worth
slightly less here because fifty S5 bars span four minutes of a fast, thin series."""

IQOPTION: Final = PlatformPolicy(
    platform="iqoption",
    primary="M1",
    microWeight=0.35,
    contextWeights=MappingProxyType({"M5": 0.70, "M10": 0.45}),
    strategyFit=MappingProxyType(
        {
            TREND_FOLLOW: 1.00,
            MOMENTUM_CONTINUATION: 1.00,
            BREAKOUT: 1.00,
            MEAN_REVERSION: 1.00,
            MICRO_IMPULSE: 0.35,
            TREND_PULLBACK: 1.00,
        }
    ),
)
"""One-minute decisions. M5 and M10 carry real structural weight, and the last few seconds
of a sixty-second bar are secondary confirmation rather than the decision itself — so the
micro impulse strategy still runs, as a diagnostic, at a much lower platform fit."""

POLICIES: Final[Mapping[Platform, PlatformPolicy]] = MappingProxyType(
    {"capitalbear": CAPITALBEAR, "iqoption": IQOPTION}
)


def policy_for(platform: Platform) -> PlatformPolicy:
    return POLICIES[platform]
