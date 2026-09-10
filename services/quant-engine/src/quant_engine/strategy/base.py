"""The contract every strategy shares.

A strategy declares the features it needs, how well it suits each regime, and how much
signed evidence it demands before it will name a direction. Everything else — the version
stamps, the identity fields, the eligibility checks and the None handling — is done once,
here, so that a strategy file contains only its own reasoning.

Eligibility is checked before any arithmetic runs. A declared feature that is ``None`` is
missing, not zero, and the strategy abstains rather than voting on a value it never saw.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import ClassVar, Final

from quant_engine.features.math import clamp
from quant_engine.strategy.common import Analysis, resolve
from quant_engine.strategy.models import (
    REGIME_VERSION,
    STRATEGY_VERSION,
    SUPPORTED_FEATURE_VERSION,
    Direction,
    Reason,
    Regime,
    RegimeSnapshot,
    StrategyEvaluation,
)
from quant_engine.strategy.policy import PlatformPolicy

MIN_REGIME_FIT: Final = 0.20
"""Below this a strategy is simply the wrong tool for the market it is looking at, and it
abstains instead of contributing a weak vote that the ensemble would still have to carry."""

MIN_QUALITY_FIT: Final = 0.15
"""Below this the inputs are too thin or too dirty for any strategy to reason about."""

MAX_STRATEGY_REASONS: Final = 5


@dataclass(frozen=True, slots=True)
class Opinion:
    """What a strategy concluded, before the shared boilerplate is applied."""

    direction: Direction
    rawScore: float
    confidence: float
    coverage: float
    reasons: list[Reason] = field(default_factory=list)
    vetoes: list[Reason] = field(default_factory=list)


def abstain(code: str, message: str, value: float | None = None) -> Opinion:
    """A SKIP that always says why."""
    return Opinion(
        direction="SKIP",
        rawScore=0.0,
        confidence=0.0,
        coverage=0.0,
        vetoes=[Reason(code=code, message=message, value=value)],
    )


def undecided(reasons: list[Reason], vetoes: list[Reason], coverage: float) -> Opinion:
    """A valid read with no directional edge. Distinct from SKIP, and never merged with it."""
    return Opinion(
        direction="NEUTRAL",
        rawScore=0.0,
        confidence=0.0,
        coverage=coverage,
        reasons=reasons,
        vetoes=vetoes,
    )


def direction_of(raw: float, minimum: float) -> Direction:
    """UP, DOWN, or NEUTRAL against the strategy's own documented evidence floor."""
    if raw >= minimum:
        return "UP"
    if raw <= -minimum:
        return "DOWN"
    return "NEUTRAL"


class Strategy:
    """Base class. Subclasses implement :meth:`read` and declare the four class attributes."""

    id: ClassVar[str]
    required: ClassVar[tuple[str, ...]]
    optional: ClassVar[tuple[str, ...]] = ()
    regime_fit: ClassVar[Mapping[Regime, float]]
    default_fit: ClassVar[float] = 0.5
    min_evidence: ClassVar[float]
    """Signed evidence this strategy demands before it names a direction rather than NEUTRAL.
    Each subclass sets it from its own documented constant, and :meth:`read` uses it through
    the attribute so the declared threshold is the one actually applied."""

    def fit(self, regime: Regime) -> float:
        return self.regime_fit.get(regime, self.default_fit)

    def read(
        self, state: Analysis, regime: RegimeSnapshot, policy: PlatformPolicy
    ) -> Opinion:  # pragma: no cover - abstract
        raise NotImplementedError

    def missing(self, state: Analysis) -> list[str]:
        return [path for path in self.required if resolve(state.bundle, path) is None]

    def evaluate(
        self, state: Analysis, regime: RegimeSnapshot, policy: PlatformPolicy
    ) -> StrategyEvaluation:
        regime_fit = self.fit(regime.primaryRegime)
        platform_fit = policy.fit(self.id)
        absent = self.missing(state)
        if absent:
            opinion = abstain(
                "MISSING_REQUIRED_FEATURE",
                "Required features unavailable: " + ", ".join(absent[:4]),
                float(len(absent)),
            )
        elif regime_fit < MIN_REGIME_FIT:
            opinion = abstain(
                "REGIME_UNSUITABLE",
                f"{self.id} does not operate in {regime.primaryRegime}",
                regime_fit,
            )
        elif state.qualityFit < MIN_QUALITY_FIT:
            opinion = abstain(
                "QUALITY_TOO_LOW", "Inputs too thin or too dirty to reason about", state.qualityFit
            )
        elif platform_fit <= 0:
            opinion = abstain(
                "PLATFORM_UNSUITABLE", f"{self.id} does not run on {policy.platform}", platform_fit
            )
        else:
            opinion = self.read(state, regime, policy)
        return StrategyEvaluation(
            strategyId=self.id,
            strategyVersion=STRATEGY_VERSION,
            platform=state.bundle.platform,
            slotId=state.bundle.slotId,
            assetName=state.bundle.assetName,
            contextId=state.bundle.contextId,
            asOf=state.bundle.asOf,
            eligible=opinion.direction != "SKIP",
            direction=opinion.direction,
            confidence=clamp(opinion.confidence, 0.0, 1.0),
            rawScore=clamp(opinion.rawScore, -1.0, 1.0),
            regimeFit=clamp(regime_fit, 0.0, 1.0),
            qualityFit=clamp(state.qualityFit, 0.0, 1.0),
            platformFit=clamp(platform_fit, 0.0, 1.0),
            evidenceCoverage=clamp(opinion.coverage, 0.0, 1.0),
            reasons=opinion.reasons[:MAX_STRATEGY_REASONS],
            vetoes=opinion.vetoes[:MAX_STRATEGY_REASONS],
            featureVersion=SUPPORTED_FEATURE_VERSION,
            regimeVersion=REGIME_VERSION,
        )
