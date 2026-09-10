"""Cross-asset opportunity ranking (Phase 8).

Consumes canonical Phase 7 ``EnsembleSnapshot`` objects and orders them against each other,
one board per platform. It reads no broker price, no ``MarketObservation`` and no raw
feature; it recomputes no candle, indicator or regime; and it contains no copy of a Phase 7
strategy. Phase 6 states facts, Phase 7 interprets one market, and this layer only compares
opinions that have already been formed.

It places no orders, sizes no stakes, touches no broker control and manages no bankroll —
there is no execution surface anywhere in this package, and a test asserts it.
"""

from quant_engine.opportunity.engine import (
    BOARD_HISTORY_CAPACITY,
    PRIMARY_HORIZON,
    RECENT_CAPACITY,
    Ingestion,
    OpportunityEngine,
    build_board,
)
from quant_engine.opportunity.models import (
    BOARD_REASON_CODES,
    EXCLUSION_CODES,
    INTEGRITY_EXCLUSIONS,
    RANK_REASON_CODES,
    RANKING_VERSION,
    SUPPORTED_FEATURE_VERSION,
    SUPPORTED_REGIME_VERSION,
    SUPPORTED_STRATEGY_VERSION,
    BoardStatus,
    CandidateStatus,
    OpportunityBoard,
    OpportunityCandidate,
    PlatformOpportunityDiagnostics,
    WatchlistEntry,
)
from quant_engine.opportunity.scoring import (
    MIN_LEAD_MARGIN,
    MIN_SELECTION_SCORE,
    median,
    persistence,
    rank_score,
    sort_key,
    support_for,
)

__all__ = [
    "BOARD_HISTORY_CAPACITY",
    "BOARD_REASON_CODES",
    "EXCLUSION_CODES",
    "INTEGRITY_EXCLUSIONS",
    "MIN_LEAD_MARGIN",
    "MIN_SELECTION_SCORE",
    "PRIMARY_HORIZON",
    "RANKING_VERSION",
    "RANK_REASON_CODES",
    "RECENT_CAPACITY",
    "SUPPORTED_FEATURE_VERSION",
    "SUPPORTED_REGIME_VERSION",
    "SUPPORTED_STRATEGY_VERSION",
    "BoardStatus",
    "CandidateStatus",
    "Ingestion",
    "OpportunityBoard",
    "OpportunityCandidate",
    "OpportunityEngine",
    "PlatformOpportunityDiagnostics",
    "WatchlistEntry",
    "build_board",
    "median",
    "persistence",
    "rank_score",
    "sort_key",
    "support_for",
]
