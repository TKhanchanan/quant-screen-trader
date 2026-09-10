"""Deterministic ranking arithmetic.

Phase 7 confidence already contains agreement, regime, quality, breadth and each strategy's
own conviction. Rebuilding that here with different weights would be Phase 7 again, so this
layer does not: ``ensemble.confidence`` is the dominant term, and everything else is a
bounded multiplier that can only move a candidate within a conservative band.

Nothing in this module reads a clock. A score is a function of the snapshot and the slot's
own recent snapshots, so replaying the same inputs a year later produces the same number.
The desktop may show "age 1.2 s" next to a board; that age never reaches a stored score.

Every weight here is an initial heuristic, not an empirically calibrated value. No outcome
has been observed by this system, so nothing could have been fitted to one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

from quant_engine.opportunity.models import OpportunityCandidate
from quant_engine.strategy.models import AnalysisStatus, Direction

# --- support weights (initial heuristic; not empirically calibrated) --------------------

AGREEMENT_WEIGHT: Final = 0.35
"""How hard the whole eligible panel leaned one way, rather than how loud one member was."""
REGIME_CONFIDENCE_WEIGHT: Final = 0.30
"""How sure Phase 7 was about what kind of market this is at all."""
BREADTH_WEIGHT: Final = 0.20
"""How many strategies actually expressed a direction, rather than one carrying the vote."""
PERSISTENCE_WEIGHT: Final = 0.15
"""Deliberately the smallest. A breakout is legitimately new, and M3 forbids treating "not
present three bars ago" as a reason to reject today's setup."""

SUPPORT_WEIGHT_TOTAL: Final = (
    AGREEMENT_WEIGHT + REGIME_CONFIDENCE_WEIGHT + BREADTH_WEIGHT + PERSISTENCE_WEIGHT
)

BREADTH_FULL: Final = 3.0
"""Active strategies at which breadth stops adding support. Six exist but several are
regime-exclusive, so requiring all of them would mean never reaching full support."""

SUPPORT_FLOOR: Final = 0.70
"""The multiplier at zero support. The band is therefore 0.70..1.00 by construction: a
candidate with poor Phase 7 confidence can never reach the top of a board because one
secondary diagnostic looked good, and a strong one is never erased by a weak diagnostic."""

# --- penalties (subtracted from support, so the band above still holds) ----------------

DISAGREEMENT_TOLERANCE: Final = 0.20
DISAGREEMENT_CEILING: Final = 0.45
"""Phase 7 refuses to name a direction at all at this split, so nothing beyond it arrives
here. The penalty ramps across the range Phase 7 still considers resolvable."""
DISAGREEMENT_PENALTY: Final = 0.20

NOISE_TOLERANCE: Final = 0.55
NOISE_CEILING: Final = 0.75
"""Phase 7's outright noise veto. An ordinary tradeable range already scores near a half, so
the penalty starts above what a legitimate range looks like."""
NOISE_PENALTY: Final = 0.15

DEGRADED_PENALTY: Final = 0.10
"""DEGRADED is the honest steady state of live broker capture and Phase 7 has already
discounted it once. This is a tie-breaker against clean input, not a second rejection."""

SHORT_HISTORY: Final = 0.60
"""Persistence below this is reported as a reason. It is a label on the diagnostics, not an
extra penalty: the persistence weight has already priced it."""

# --- selection gates -------------------------------------------------------------------

MIN_SELECTION_SCORE: Final = 0.35
"""The bar a candidate must clear on its own before it can lead a board.

Scaled against what Phase 7 actually produces: a textbook, noiseless, fully agreeing trend
on synthetic input reaches roughly 0.58 confidence, so the practical rankScore ceiling is
near 0.6 rather than 1.0. This is a heuristic constant, not a calibrated one, and it is
another reason rankScore must never be read as a probability."""

MIN_LEAD_MARGIN: Final = 0.05
"""How far the top candidate must stand clear of the runner-up before this layer will call
it the leading analysis. Below it the two are not distinguishable by this heuristic, and
saying otherwise would be false certainty."""

PERSISTENCE_WINDOW_SHORT: Final = 3
PERSISTENCE_WINDOW_LONG: Final = 5


def clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


def ramp(value: float, zero_at: float, one_at: float) -> float:
    """Linear 0..1 ramp between two named points, flat outside them."""
    if one_at == zero_at:
        return 0.0
    return clamp((value - zero_at) / (one_at - zero_at), 0.0, 1.0)


def median(values: Sequence[float]) -> float:
    """Median, not mean, so one extreme reading cannot move a stability diagnostic."""
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def persistence(directions: Sequence[Direction], current: Direction) -> float:
    """Share of the recent directional readings that agree with the current one.

    Only UP and DOWN are counted. A NEUTRAL or SKIP in the window occupies a place — so a
    window that mostly abstained holds less directional evidence — but it is never counted as
    a vote against: a NEUTRAL is not a DOWN merely because the current reading is UP.

    The current snapshot is part of its own window, so a signal appearing for the first time
    starts at 1.0 rather than being punished for having no past.
    """
    usable = [item for item in directions if item in ("UP", "DOWN")]
    if not usable:
        return 0.0
    return sum(1 for item in usable if item == current) / len(usable)


def breadth(active_strategies: int) -> float:
    return clamp(active_strategies / BREADTH_FULL, 0.0, 1.0)


@dataclass(frozen=True, slots=True)
class Support:
    """Secondary evidence behind one candidate's multiplier, with what shaped it."""

    score: float
    codes: list[str] = field(default_factory=list)


def support_for(
    *,
    agreement: float,
    regime_confidence: float,
    active_strategies: int,
    direction_persistence3: float,
    disagreement: float,
    noise_score: float,
    analysis_status: AnalysisStatus,
) -> Support:
    """Weighted mean of the secondary diagnostics, less modest, named penalties.

    Penalties are subtracted here rather than from the multiplier so the multiplier band
    stays exactly 0.70..1.00. Phase 7 already accounted for disagreement, noise and degraded
    input once; these are a nudge in the ordering, never a second veto.
    """
    base = (
        AGREEMENT_WEIGHT * agreement
        + REGIME_CONFIDENCE_WEIGHT * regime_confidence
        + BREADTH_WEIGHT * breadth(active_strategies)
        + PERSISTENCE_WEIGHT * direction_persistence3
    ) / SUPPORT_WEIGHT_TOTAL
    codes: list[str] = []
    penalty = 0.0
    conflict = ramp(disagreement, DISAGREEMENT_TOLERANCE, DISAGREEMENT_CEILING)
    if conflict > 0:
        penalty += DISAGREEMENT_PENALTY * conflict
        codes.append("HIGH_DISAGREEMENT")
    turbulence = ramp(noise_score, NOISE_TOLERANCE, NOISE_CEILING)
    if turbulence > 0:
        penalty += NOISE_PENALTY * turbulence
        codes.append("HIGH_NOISE")
    if analysis_status == "DEGRADED":
        penalty += DEGRADED_PENALTY
        codes.append("DEGRADED_INPUT")
    if direction_persistence3 < SHORT_HISTORY:
        codes.append("SHORT_HISTORY")
    return Support(score=clamp(base - penalty, 0.0, 1.0), codes=codes)


def support_multiplier(support: float) -> float:
    return SUPPORT_FLOOR + (1.0 - SUPPORT_FLOOR) * clamp(support, 0.0, 1.0)


def rank_score(ensemble_confidence: float, support: float) -> float:
    """Relative ordering utility for one candidate. Never a probability of anything.

    ``coreStrength`` is Phase 7's own confidence; the support multiplier can only move it
    within 0.70..1.00. Two candidates whose only difference is confidence therefore always
    rank in confidence order, and the result is bounded, finite and reproducible.
    """
    return clamp(clamp(ensemble_confidence, 0.0, 1.0) * support_multiplier(support), 0.0, 1.0)


def sort_key(candidate: OpportunityCandidate) -> tuple[float, float, float, int, int]:
    """Total order over directional candidates. Never depends on insertion order.

    Score first, then Phase 7's own confidence, then how hard the panel leaned, then how many
    strategies spoke, and finally the slot number — which is unique within a board, so the
    order is total and a replay always produces identical ranks.
    """
    return (
        -candidate.rankScore,
        -candidate.ensembleConfidence,
        -candidate.agreement,
        -candidate.activeStrategies,
        candidate.slotId,
    )
