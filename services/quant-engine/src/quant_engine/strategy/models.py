"""Strategy-layer wire models.

Phase 6 produces facts. Phase 7 interprets them and says nothing else: there is no order,
no stake, no bankroll and no execution surface anywhere in this package. Every opinion it
emits carries the version contract it was produced under, the evidence behind it, and the
vetoes that were considered, so a later replay can reconstruct exactly why it was reached.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field

from quant_engine.configuration import Model, Platform
from quant_engine.market_models import Timeframe

STRATEGY_VERSION = "qst-strategy-v1"
"""Strategy catalog, thresholds and ensemble arithmetic. Any change to a strategy's evidence,
weight or threshold requires a new string, so opinions from two definitions can never pool."""

REGIME_VERSION = "qst-regime-v1"
"""Regime classification rules. Versioned separately from the strategies because a regime
change invalidates a different, smaller set of stored rows."""

SUPPORTED_FEATURE_VERSION = "qfe-v2"
"""Deliberately a literal, not an import of ``FEATURE_VERSION``.

Phase 7's thresholds are calibrated against the qfe-v2 formulas. Tracking the Phase 6
constant would let a later formula change flow silently into strategies that were never
re-checked against it; a literal makes that change fail loudly here instead."""

type Direction = Literal["UP", "DOWN", "NEUTRAL", "SKIP"]
"""UP/DOWN carry directional evidence. NEUTRAL is a valid read with no edge. SKIP means the
strategy was not eligible or the data could not support an opinion. They are not the same
verdict and are never collapsed into one another."""

type Regime = Literal[
    "TREND_UP",
    "TREND_DOWN",
    "RANGE",
    "BREAKOUT_UP",
    "BREAKOUT_DOWN",
    "VOLATILITY_EXPANSION",
    "VOLATILITY_COMPRESSION",
    "NOISY",
    "UNCERTAIN",
]

type AnalysisStatus = Literal["OK", "WARMING", "DEGRADED", "INVALID"]
"""Whether the inputs could support analysis at all, mirroring the Phase 6 vocabulary."""

VETO_CODES = (
    "UNSUPPORTED_FEATURE_VERSION",
    "INVALID_PRIMARY_FEATURES",
    "MISSING_PRIMARY_FEATURES",
    "PRIMARY_TIMEFRAME_MISMATCH",
    "CONTEXT_CHANGED",
    "CHRONOLOGY_INVALID",
    "INSUFFICIENT_DATA",
    "LOW_COVERAGE",
    "EXTREME_NOISE",
    "CONFLICT_TOO_HIGH",
    "NO_ELIGIBLE_STRATEGY",
    "INSUFFICIENT_STRATEGY_WEIGHT",
)
"""Every ensemble-level veto that can force SKIP. Output is never suppressed silently: a
SKIP always names at least one of these."""


class Reason(Model):
    """One structured piece of explanation.

    ``code`` is stable and machine-readable, ``message`` is display text, and ``value`` is
    the measurement behind it when there is one. Reasons are a curated audit trail, never a
    dump of the whole feature set.
    """

    code: str = Field(min_length=1, max_length=48)
    message: str = Field(min_length=1, max_length=160)
    value: float | None = None


class RegimeSnapshot(Model):
    """Deterministic reading of what kind of market this is, with no strategy attached."""

    platform: Platform
    slotId: int = Field(ge=1, le=9, strict=True)
    assetName: str = Field(min_length=1, max_length=120)
    contextId: UUID
    asOf: int = Field(strict=True)
    primaryTimeframe: Timeframe
    featureVersion: str = Field(min_length=1, max_length=40)
    regimeVersion: str = Field(min_length=1, max_length=40)

    primaryRegime: Regime

    trendScore: float = Field(ge=-1, le=1)
    """Signed: positive is trend-up evidence, negative trend-down. Magnitude is strength."""
    rangeScore: float = Field(ge=0, le=1)
    breakoutScore: float = Field(ge=-1, le=1)
    """Signed, and already multiplied by its confirmation: a break with no follow-through
    scores near zero rather than reporting the raw fact that a level was crossed."""
    noiseScore: float = Field(ge=0, le=1)
    volatilityExpansionScore: float = Field(ge=0, le=1)
    volatilityCompressionScore: float = Field(ge=0, le=1)

    directionBias: Direction
    confidence: float = Field(ge=0, le=1)
    qualityFit: float = Field(ge=0, le=1)

    reasons: list[Reason] = Field(default_factory=list, max_length=24)
    vetoes: list[Reason] = Field(default_factory=list, max_length=12)
    status: AnalysisStatus


class StrategyEvaluation(Model):
    """One strategy's opinion about one bundle. Never an instruction to do anything."""

    strategyId: str = Field(min_length=1, max_length=48)
    strategyVersion: str = Field(min_length=1, max_length=40)
    platform: Platform
    slotId: int = Field(ge=1, le=9, strict=True)
    assetName: str = Field(min_length=1, max_length=120)
    contextId: UUID
    asOf: int = Field(strict=True)

    eligible: bool
    direction: Direction
    confidence: float = Field(ge=0, le=1)
    """The strategy's own conviction only. Regime, quality and platform fit are applied once,
    by the ensemble, so that multiplying them in here would double-count them."""

    rawScore: float = Field(ge=-1, le=1)
    """Signed directional evidence before any fit multiplier."""

    regimeFit: float = Field(ge=0, le=1)
    qualityFit: float = Field(ge=0, le=1)
    platformFit: float = Field(ge=0, le=1)
    evidenceCoverage: float = Field(ge=0, le=1)
    """Share of the strategy's weighted evidence that was actually available."""

    reasons: list[Reason] = Field(default_factory=list, max_length=24)
    vetoes: list[Reason] = Field(default_factory=list, max_length=12)

    featureVersion: str = Field(min_length=1, max_length=40)
    regimeVersion: str = Field(min_length=1, max_length=40)


class EnsembleWeights(Model):
    """Weighted bookkeeping behind the ensemble direction, exposed so it can be audited."""

    up: float = Field(ge=0)
    down: float = Field(ge=0)
    neutral: float = Field(ge=0)
    active: float = Field(ge=0)
    net: float


class EnsembleSnapshot(Model):
    """The whole panel's reading for one slot at one primary close.

    Carries no stake, no order, no payout and no expected value. ``confidence`` is a measure
    of how much usable, agreeing evidence exists — it is not a probability of anything.
    """

    platform: Platform
    slotId: int = Field(ge=1, le=9, strict=True)
    assetName: str = Field(min_length=1, max_length=120)
    contextId: UUID
    asOf: int = Field(strict=True)
    primaryTimeframe: Timeframe

    featureVersion: str = Field(min_length=1, max_length=40)
    regimeVersion: str = Field(min_length=1, max_length=40)
    strategyVersion: str = Field(min_length=1, max_length=40)

    regime: RegimeSnapshot
    strategies: list[StrategyEvaluation] = Field(default_factory=list, max_length=16)

    direction: Direction
    confidence: float = Field(ge=0, le=1)

    agreement: float = Field(ge=0, le=1)
    """Absolute net directional vote over the total active weight, per R3: how strongly the
    whole eligible panel points one way, not merely whether its voters happen to concur."""
    disagreement: float = Field(ge=0, le=1)
    """Share of the directional weight that voted against the net direction."""

    weights: EnsembleWeights
    eligibleStrategies: int = Field(ge=0)
    activeStrategies: int = Field(ge=0)
    """Eligible strategies that actually expressed UP or DOWN, rather than NEUTRAL."""

    reasons: list[Reason] = Field(default_factory=list, max_length=24)
    vetoes: list[Reason] = Field(default_factory=list, max_length=12)
    status: AnalysisStatus


class StrategyVote(Model):
    """One strategy's contribution, compact enough for a diagnostics line."""

    strategyId: str = Field(min_length=1, max_length=48)
    direction: Direction
    confidence: float = Field(ge=0, le=1)


class SlotStrategyDiagnostics(Model):
    platform: Platform
    slotId: int = Field(ge=1, le=9)
    assetName: str
    contextId: UUID
    asOf: int
    primaryRegime: Regime
    regimeConfidence: float = Field(ge=0, le=1)
    direction: Direction
    confidence: float = Field(ge=0, le=1)
    eligibleStrategies: int = Field(ge=0)
    activeStrategies: int = Field(ge=0)
    evaluations: int = Field(ge=0)
    votes: list[StrategyVote] = Field(default_factory=list, max_length=16)
    vetoes: list[str] = Field(default_factory=list, max_length=12)
