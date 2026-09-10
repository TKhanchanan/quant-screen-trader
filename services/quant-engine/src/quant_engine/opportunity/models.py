"""Ranking-layer wire models.

Phase 6 produces facts, Phase 7 interprets one market, and Phase 8 compares several Phase 7
opinions against each other. Nothing here is an instruction: a ranked candidate is an
analytical ordering, not an order, and the package carries no stake, bankroll, payout or
broker control anywhere in it.

``rankScore`` is a relative utility for ordering the opportunities that exist right now. It
is deliberately not called a probability, a win rate or an expected return: no outcome has
ever been observed by this system, so nothing here could have been calibrated against one.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field

from quant_engine.configuration import Model, Platform
from quant_engine.market_models import Timeframe
from quant_engine.strategy.models import AnalysisStatus, Direction, Regime

RANKING_VERSION = "qst-ranking-v1"
"""Ranking weights, gates and cohort rules. Any change to how candidates are scored or
selected requires a new string, so rows produced by two definitions can never pool."""

SUPPORTED_FEATURE_VERSION = "qfe-v2"
SUPPORTED_REGIME_VERSION = "qst-regime-v1"
SUPPORTED_STRATEGY_VERSION = "qst-strategy-v1"
"""Deliberately literals, not imports of the Phase 6/7 constants.

Phase 8's gates are calibrated against what qfe-v2 features and qst-strategy-v1 confidence
actually mean. Tracking the upstream constants would let a later contract flow silently into
a ranking nobody re-checked against it; a literal makes that arrive as an explicit exclusion
instead of a silent re-interpretation."""

type CandidateStatus = Literal["ACTIONABLE", "WATCH", "NEUTRAL", "EXCLUDED"]
"""ACTIONABLE: Phase 7 named a direction and the candidate cleared the Phase 8 score gate,
so it is strong enough to stand as this board's leading analysis. WATCH: a valid directional
read that is not. NEUTRAL: Phase 7 itself reported NEUTRAL, preserved rather than converted.
EXCLUDED: Phase 7 skipped, or a hard ranking veto applies.

ACTIONABLE means analytically rankable. It does not mean execute anything."""

type BoardStatus = Literal["COLLECTING", "READY", "PARTIAL", "NO_OPPORTUNITY", "INVALID"]
"""COLLECTING: expected slots for this epoch have not all reported yet. READY: the cohort is
complete, ranked, and a leading analysis was resolved. PARTIAL: the epoch closed without
every expected slot, and what did arrive is reported honestly. NO_OPPORTUNITY: a complete
cohort in which nothing cleared the conservative selection gate. INVALID: every candidate
that arrived failed a version, identity or chronology check, so the cohort means nothing."""

EXCLUSION_CODES = (
    "UNSUPPORTED_VERSION",
    "INVALID_IDENTITY",
    "CHRONOLOGY_INVALID",
    "INVALID_ANALYSIS",
    "STRATEGY_SKIP",
    "STALE_FOR_EPOCH",
)
"""Every reason a slot can be kept out of a directional ranking. A candidate is never dropped
silently: an EXCLUDED candidate always names at least one of these."""

INTEGRITY_EXCLUSIONS = frozenset({"UNSUPPORTED_VERSION", "INVALID_IDENTITY", "CHRONOLOGY_INVALID"})
"""The subset that means the input itself could not be trusted, rather than that Phase 7
looked at a real market and declined to call it. A cohort made entirely of these is INVALID,
because there is nothing in it that was ever measured under a contract this layer knows."""

RANK_REASON_CODES = (
    "TOP_CANDIDATE",
    "BELOW_SELECTION_SCORE",
    "LOW_LEAD_MARGIN",
    "SOLE_CANDIDATE",
    "DEGRADED_INPUT",
    "HIGH_DISAGREEMENT",
    "HIGH_NOISE",
    "SHORT_HISTORY",
)

BOARD_REASON_CODES = (
    "COHORT_COMPLETE",
    "COHORT_INCOMPLETE",
    "MISSING_SLOTS",
    "STALE_SLOT_PRESENT",
    "UNSUPPORTED_VERSION_PRESENT",
    "NO_EXPECTED_SLOTS",
    "NO_DIRECTIONAL_CANDIDATE",
    "BELOW_SELECTION_SCORE",
    "LOW_LEAD_MARGIN",
)

MAX_CANDIDATES = 9
"""One per slot on one platform. Boards are per-platform, so nine is the whole cohort."""
MAX_WATCHLIST = 3
MAX_REASONS = 8

type Code = str


class OpportunityCandidate(Model):
    """One Phase 7 opinion, positioned against the other markets observed at the same close.

    Every diagnostic that moved the score is on the record, including the ones that are
    unflattering: a candidate ranked first on degraded input says so.
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
    rankingVersion: str = Field(min_length=1, max_length=40)

    direction: Direction
    analysisStatus: AnalysisStatus

    ensembleConfidence: float = Field(ge=0, le=1)
    """Phase 7's own reading, the dominant input. Not a probability of anything."""
    agreement: float = Field(ge=0, le=1)
    disagreement: float = Field(ge=0, le=1)

    primaryRegime: Regime
    regimeConfidence: float = Field(ge=0, le=1)
    noiseScore: float = Field(ge=0, le=1)
    qualityFit: float = Field(ge=0, le=1)

    eligibleStrategies: int = Field(ge=0)
    activeStrategies: int = Field(ge=0)

    directionPersistence3: float = Field(ge=0, le=1)
    directionPersistence5: float = Field(ge=0, le=1)
    confidenceMedian3: float = Field(ge=0, le=1)
    confidenceMedian5: float = Field(ge=0, le=1)

    supportScore: float = Field(ge=0, le=1)
    """The secondary evidence behind the multiplier, exposed so the score can be audited."""
    rankScore: float = Field(ge=0, le=1)
    """Relative ordering utility only. Never a win probability, edge or expected value."""

    candidateStatus: CandidateStatus
    rank: int | None = Field(default=None, ge=1, le=MAX_CANDIDATES)
    """Position among this board's directional candidates. ``None`` when there is no
    directional analysis to place."""
    rankReasonCodes: list[Code] = Field(default_factory=list, max_length=MAX_REASONS)
    exclusionReasons: list[Code] = Field(default_factory=list, max_length=MAX_REASONS)


class WatchlistEntry(Model):
    """A compact line for a diagnostics panel. Deliberately not a command."""

    rank: int = Field(ge=1, le=MAX_CANDIDATES)
    slotId: int = Field(ge=1, le=9, strict=True)
    assetName: str = Field(min_length=1, max_length=120)
    direction: Direction
    rankScore: float = Field(ge=0, le=1)
    ensembleConfidence: float = Field(ge=0, le=1)
    regime: Regime
    candidateStatus: CandidateStatus


class OpportunityBoard(Model):
    """One platform's ranking for one market decision time.

    Boards are per platform and never merged. CapitalBear settles five-second bars and IQ
    Option one-minute bars, so a single 1-18 ordering would claim two different horizons are
    the same decision. ``asOf`` is that platform's primary close, and only snapshots carrying
    exactly that time belong to the cohort.
    """

    platform: Platform
    asOf: int = Field(strict=True)
    primaryTimeframe: Timeframe

    featureVersion: str = Field(min_length=1, max_length=40)
    regimeVersion: str = Field(min_length=1, max_length=40)
    strategyVersion: str = Field(min_length=1, max_length=40)
    rankingVersion: str = Field(min_length=1, max_length=40)

    status: BoardStatus

    expectedSlots: int = Field(ge=0, le=9)
    receivedSlots: int = Field(ge=0, le=9)
    rankedSlots: int = Field(ge=0, le=9)
    excludedSlots: int = Field(ge=0, le=9)
    missingSlots: list[int] = Field(default_factory=list, max_length=9)
    """Expected slots that never produced a same-time ensemble. Named, never invented."""

    candidates: list[OpportunityCandidate] = Field(default_factory=list, max_length=MAX_CANDIDATES)

    selectedSlotId: int | None = Field(default=None, ge=1, le=9)
    selectedAssetName: str | None = Field(default=None, max_length=120)
    selectedDirection: Direction | None = None
    selectedScore: float | None = Field(default=None, ge=0, le=1)
    """The current top analytical candidate. Not a trade instruction, and absent whenever the
    conservative gates are not met."""

    runnerUpSlotId: int | None = Field(default=None, ge=1, le=9)
    leadMargin: float | None = Field(default=None, ge=0, le=1)

    watchlist: list[WatchlistEntry] = Field(default_factory=list, max_length=MAX_WATCHLIST)
    reasons: list[Code] = Field(default_factory=list, max_length=MAX_REASONS)


class PlatformOpportunityDiagnostics(Model):
    """Compact per-platform summary for the read API and the desktop diagnostics block."""

    platform: Platform
    board: OpportunityBoard | None
    boards: int = Field(ge=0)
    slotsTracked: int = Field(ge=0)
