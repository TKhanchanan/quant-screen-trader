"""Deterministic paper outcome simulation (Phase 9).

Consumes finished Phase 8 ``OpportunityBoard`` selections and canonical Phase 5 price samples,
and answers one question: if that selection had been entered when it actually became
available, what would the market have done over a fixed horizon? It recomputes no feature, no
regime, no strategy and no ranking, and it never asks Phase 8 to produce a selection.

It is not an execution layer. There is no broker control, no input event, no order, no wallet
and no real stake anywhere in this package — the only money it can report is an explicitly
configured simulation parameter — and a test asserts it binds no execution name and imports
nothing that could reach a broker.
"""

from quant_engine.paper.accounting import AccountingSnapshot, realized_pnl, snapshot_at_entry
from quant_engine.paper.engine import PaperEngine, PaperUpdate
from quant_engine.paper.models import (
    PAPER_VERSION,
    REASON_CODES,
    REJECTION_CODES,
    SUPPORTED_FEATURE_VERSION,
    SUPPORTED_RANKING_VERSION,
    SUPPORTED_REGIME_VERSION,
    SUPPORTED_STRATEGY_VERSION,
    PaperDirection,
    PaperEngineState,
    PaperEventType,
    PaperOutcome,
    PaperSettlement,
    PaperStats,
    PaperTrade,
    PaperTradeEvent,
    PaperTradeStatus,
    PlatformPaperDiagnostics,
)
from quant_engine.paper.policy import (
    MAX_ENTRY_DELAY_MS,
    MAX_OPEN_PER_PLATFORM,
    MAX_OPEN_PER_SLOT,
    MAX_RESOLUTION_LAG_MS,
    PAPER_DURATION_MS,
    TRADE_HISTORY_CAPACITY,
    PaperSettings,
    settings_from_environment,
)
from quant_engine.paper.resolver import (
    outcome_for,
    paper_trade_id,
    price_delta_bps,
    source_mode,
    usable,
)

__all__ = [
    "MAX_ENTRY_DELAY_MS",
    "MAX_OPEN_PER_PLATFORM",
    "MAX_OPEN_PER_SLOT",
    "MAX_RESOLUTION_LAG_MS",
    "PAPER_DURATION_MS",
    "PAPER_VERSION",
    "REASON_CODES",
    "REJECTION_CODES",
    "SUPPORTED_FEATURE_VERSION",
    "SUPPORTED_RANKING_VERSION",
    "SUPPORTED_REGIME_VERSION",
    "SUPPORTED_STRATEGY_VERSION",
    "TRADE_HISTORY_CAPACITY",
    "AccountingSnapshot",
    "PaperDirection",
    "PaperEngine",
    "PaperEngineState",
    "PaperEventType",
    "PaperOutcome",
    "PaperSettings",
    "PaperSettlement",
    "PaperStats",
    "PaperTrade",
    "PaperTradeEvent",
    "PaperTradeStatus",
    "PaperUpdate",
    "PlatformPaperDiagnostics",
    "outcome_for",
    "paper_trade_id",
    "price_delta_bps",
    "realized_pnl",
    "settings_from_environment",
    "snapshot_at_entry",
    "source_mode",
    "usable",
]
