"""Deterministic weighted ensemble over the strategy catalog.

Not a majority vote. Each eligible strategy is given a capacity — its prior weight, scaled by
how well it fits this regime, how good the inputs are, and how well it suits this broker —
and casts a fraction of that capacity equal to its own conviction. A strategy that skipped
casts nothing at all; one that saw no edge still consumes capacity, because a member of the
panel finding nothing is information about the panel's conclusion.

``confidence`` measures how much usable, agreeing evidence the panel produced. It is not a
probability of anything, and it is deliberately not called one: Phase 7 has no calibration
and no outcome history behind it.

The arithmetic is split into three pure steps — tally, veto, confidence — so each can be
exercised on its own rather than only through a market that happens to produce the right
combination of votes.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

from quant_engine.features.math import clamp
from quant_engine.features.models import FeatureBundle
from quant_engine.strategy.base import Strategy
from quant_engine.strategy.breakout import Breakout
from quant_engine.strategy.common import Gate, analysis, market_gate, ramp
from quant_engine.strategy.mean_reversion import MeanReversion
from quant_engine.strategy.micro import MicroImpulse
from quant_engine.strategy.models import (
    REGIME_VERSION,
    STRATEGY_VERSION,
    Direction,
    EnsembleSnapshot,
    EnsembleWeights,
    Reason,
    RegimeSnapshot,
    StrategyEvaluation,
)
from quant_engine.strategy.momentum import MomentumContinuation
from quant_engine.strategy.policy import BASE_WEIGHTS, policy_for
from quant_engine.strategy.pullback import TrendPullback
from quant_engine.strategy.regime import classify
from quant_engine.strategy.trend_follow import TrendFollow

CATALOG: Final[tuple[Strategy, ...]] = (
    TrendFollow(),
    MomentumContinuation(),
    Breakout(),
    MeanReversion(),
    MicroImpulse(),
    TrendPullback(),
)
"""Evaluation order is fixed so that a replay of the same bundle produces the same rows."""

NOISE_DAMPING: Final = 0.60
"""Most of the capacity that noise can take away before the outright veto takes over."""
NOISE_TOLERANCE: Final = 0.45
"""Noise below this costs nothing.

Range and noise share their inputs — low efficiency, high overlap, frequent sign changes —
so an ordinary, perfectly tradeable range already scores around a half. Damping from zero
would punish every range-appropriate strategy for the conditions that make it applicable, on
top of the regime fit that has already accounted for them. Damping starts where noise exceeds
what a legitimate range looks like."""
NOISE_CEILING: Final = 0.75
"""Above this the ensemble abstains no matter how loudly one member votes."""

MIN_ACTIVE_WEIGHT: Final = 0.60
"""Total capacity needed before the panel is a panel rather than one damped opinion."""

MIN_AGREEMENT: Final = 0.18
"""Net directional vote, as a share of total capacity, before a direction is named."""

MAX_DISAGREEMENT: Final = 0.45
"""An almost even split is not a weak signal, it is an unresolved one, and it is skipped."""

FULL_PANEL: Final = 4.0
"""Eligible strategies at which breadth stops adding confidence. Six exist, but several are
regime-exclusive, so requiring all of them would mean never reaching full confidence."""

REGIME_CONFIDENCE_FLOOR: Final = 0.40
"""Regime confidence modulates the ensemble but may not annihilate it: strategies carry their
own regime fit already, so an uncertain regime should discount, not erase."""

MAX_ENSEMBLE_REASONS: Final = 8
MAX_ENSEMBLE_VETOES: Final = 12


def noise_damping(noise_score: float) -> float:
    return clamp(
        1.0 - NOISE_DAMPING * (ramp(noise_score, NOISE_TOLERANCE, NOISE_CEILING) or 0.0), 0.0, 1.0
    )


@dataclass(frozen=True, slots=True)
class Tally:
    """The weighted arithmetic behind one ensemble decision."""

    weights: EnsembleWeights
    agreement: float
    disagreement: float
    eligible: int
    active: int
    reasons: list[Reason] = field(default_factory=list)


EMPTY_WEIGHTS: Final = EnsembleWeights(up=0.0, down=0.0, neutral=0.0, active=0.0, net=0.0)
EMPTY_TALLY: Final = Tally(
    weights=EMPTY_WEIGHTS, agreement=0.0, disagreement=0.0, eligible=0, active=0
)


def capacity(evaluation: StrategyEvaluation, damping: float) -> float:
    """What this strategy is allowed to contribute, before its own conviction is applied."""
    return (
        BASE_WEIGHTS.get(evaluation.strategyId, 0.0)
        * evaluation.regimeFit
        * evaluation.qualityFit
        * evaluation.platformFit
        * damping
    )


def tally(evaluations: Sequence[StrategyEvaluation], damping: float) -> Tally:
    """Sum the panel's votes. Pure, and independent of how the evaluations were produced."""
    up = down = neutral = 0.0
    reasons: list[Reason] = []
    for evaluation in evaluations:
        if not evaluation.eligible:
            continue
        allowance = capacity(evaluation, damping)
        if evaluation.direction == "NEUTRAL":
            neutral += allowance
            continue
        vote = evaluation.confidence * allowance
        if evaluation.direction == "UP":
            up += vote
        else:
            down += vote
        reasons.append(
            Reason(
                code=f"VOTE_{evaluation.strategyId.upper()}",
                message=f"{evaluation.strategyId} {evaluation.direction}",
                value=round(vote, 6),
            )
        )
    active = up + down + neutral
    directional = up + down
    net = up - down
    return Tally(
        weights=EnsembleWeights(up=up, down=down, neutral=neutral, active=active, net=net),
        # R3: the absolute net vote over the total active weight, so a panel that mostly
        # found nothing reports low agreement even when its one voter was certain.
        agreement=clamp(abs(net) / active, 0.0, 1.0) if active > 0 else 0.0,
        # The share of the directional weight that voted against the net direction.
        disagreement=clamp(min(up, down) / directional, 0.0, 1.0) if directional > 0 else 0.0,
        eligible=sum(1 for item in evaluations if item.eligible),
        active=sum(1 for item in evaluations if item.direction in ("UP", "DOWN")),
        reasons=reasons,
    )


def vetoes_for(regime: RegimeSnapshot, counted: Tally) -> list[Reason]:
    """Ensemble-level objections, ordered so the reported one names the real cause.

    Returning an empty list means nothing objected; a SKIP always carries at least one of
    these, so output is never suppressed without an explanation.
    """
    if regime.noiseScore >= NOISE_CEILING:
        return [
            Reason(
                code="EXTREME_NOISE",
                message="Market is too noisy to act on",
                value=regime.noiseScore,
            )
        ]
    if counted.eligible == 0:
        return [Reason(code="NO_ELIGIBLE_STRATEGY", message="Every strategy abstained", value=0.0)]
    if counted.weights.active < MIN_ACTIVE_WEIGHT:
        return [
            Reason(
                code="INSUFFICIENT_STRATEGY_WEIGHT",
                message="Too little usable strategy weight to form a panel",
                value=counted.weights.active,
            )
        ]
    if counted.disagreement >= MAX_DISAGREEMENT:
        return [
            Reason(
                code="CONFLICT_TOO_HIGH",
                message="Strategies are split too evenly to resolve",
                value=counted.disagreement,
            )
        ]
    return []


def direction_for(counted: Tally, vetoes: Sequence[Reason]) -> Direction:
    if vetoes:
        return "SKIP"
    if counted.agreement < MIN_AGREEMENT:
        return "NEUTRAL"
    return "UP" if counted.weights.net > 0 else "DOWN"


def confidence_for(
    direction: Direction, counted: Tally, regime: RegimeSnapshot, quality_fit: float
) -> float:
    """How much usable, agreeing evidence the panel produced. Never a win probability."""
    if direction == "SKIP":
        return 0.0
    breadth = clamp(counted.eligible / FULL_PANEL, 0.0, 1.0)
    regime_factor = REGIME_CONFIDENCE_FLOOR + (1 - REGIME_CONFIDENCE_FLOOR) * regime.confidence
    return clamp(
        counted.agreement * (1.0 - counted.disagreement) * regime_factor * quality_fit * breadth,
        0.0,
        1.0,
    )


def evaluate_strategies(
    bundle: FeatureBundle, regime: RegimeSnapshot, gate: Gate | None = None
) -> list[StrategyEvaluation]:
    """Every strategy's opinion, in catalog order. Pure."""
    resolved = market_gate(bundle) if gate is None else gate
    primary = bundle.primary
    if not resolved.usable or primary is None:
        return []
    state = analysis(bundle, primary, resolved.qualityFit)
    policy = policy_for(bundle.platform)
    return [strategy.evaluate(state, regime, policy) for strategy in CATALOG]


def _snapshot(
    bundle: FeatureBundle,
    regime: RegimeSnapshot,
    gate: Gate,
    evaluations: list[StrategyEvaluation],
    counted: Tally,
    direction: Direction,
    confidence: float,
    reasons: list[Reason],
    vetoes: list[Reason],
) -> EnsembleSnapshot:
    return EnsembleSnapshot(
        platform=bundle.platform,
        slotId=bundle.slotId,
        assetName=bundle.assetName,
        contextId=bundle.contextId,
        asOf=bundle.asOf,
        primaryTimeframe=bundle.primaryTimeframe,
        featureVersion=bundle.featureVersion,
        regimeVersion=REGIME_VERSION,
        strategyVersion=STRATEGY_VERSION,
        regime=regime,
        strategies=evaluations,
        direction=direction,
        confidence=confidence,
        agreement=counted.agreement,
        disagreement=counted.disagreement,
        weights=counted.weights,
        eligibleStrategies=counted.eligible,
        activeStrategies=counted.active,
        reasons=reasons[:MAX_ENSEMBLE_REASONS],
        vetoes=vetoes[:MAX_ENSEMBLE_VETOES],
        status=gate.status,
    )


def evaluate_bundle(bundle: FeatureBundle) -> EnsembleSnapshot:
    """Regime, every strategy, and the weighted ensemble over one FeatureBundle.

    Pure and total: the same bundle always produces the same snapshot, and an unusable one
    produces an explicit SKIP carrying the vetoes that caused it, never silence.
    """
    gate = market_gate(bundle)
    regime = classify(bundle, gate)
    evaluations = evaluate_strategies(bundle, regime, gate)
    if not gate.usable:
        return _snapshot(
            bundle, regime, gate, evaluations, EMPTY_TALLY, "SKIP", 0.0, [], list(gate.vetoes)
        )

    counted = tally(evaluations, noise_damping(regime.noiseScore))
    # A gate veto that was not fatal — coverage too low to act on — still forces SKIP, and
    # is reported instead of the panel's own objections rather than alongside them.
    vetoes = list(gate.vetoes) or vetoes_for(regime, counted)
    direction = direction_for(counted, vetoes)
    reasons = [
        Reason(
            code=f"REGIME_{regime.primaryRegime}",
            message=f"Regime {regime.primaryRegime}",
            value=regime.confidence,
        ),
        *counted.reasons,
    ]
    return _snapshot(
        bundle,
        regime,
        gate,
        evaluations,
        counted,
        direction,
        confidence_for(direction, counted, regime, gate.qualityFit),
        reasons,
        vetoes,
    )
