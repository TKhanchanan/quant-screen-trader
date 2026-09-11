"""Paper-simulation wire models (Phase 9).

Phase 6 produces facts, Phase 7 interprets one market, Phase 8 compares those opinions, and
this layer asks the only question none of them can: *if that selection had been entered, what
would the market have done next?*

It is a measurement layer, not a strategy and not an execution layer. Nothing here presses a
broker control, sizes a real position or reads a real wallet. ``paperStake`` and
``paperPayoutRate`` are explicit simulation parameters supplied by the operator; when they are
absent the layer still resolves WIN / LOSS / DRAW and reports ``realizedPaperPnl`` as ``None``
rather than inventing money.

The word "paper" is overloaded in this project and the two meanings are unrelated. The desktop
``ExecutionManager`` has a PAPER mode, which decides whether it *would* have pressed a broker
control and never presses it. This layer never looks at a broker control at all: it takes a
Phase 8 selection, finds the canonical market price after the decision was available, waits a
fixed horizon in market time, and records what happened. An execution ticket saying CONFIRMED
means a broker panel reacted; a paper trade saying WIN means the market moved the way the
analysis said it would. They are never the same statement.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field

from quant_engine.configuration import Model, Platform
from quant_engine.market_models import QualityState, SourceType
from quant_engine.opportunity.models import BoardStatus
from quant_engine.strategy.models import Regime

PAPER_VERSION = "qst-paper-v1"
"""Entry policy, expiry horizons, timeout bounds, outcome rules and payout semantics. Any
change to how an outcome is reached requires a new string, so trades resolved under two
definitions can never pool into one statistic."""

SUPPORTED_FEATURE_VERSION = "qfe-v2"
SUPPORTED_REGIME_VERSION = "qst-regime-v1"
SUPPORTED_STRATEGY_VERSION = "qst-strategy-v1"
SUPPORTED_RANKING_VERSION = "qst-ranking-v1"
"""Deliberately literals, not imports of the Phase 6/7/8 constants.

A paper trade is evidence about what a *particular* set of upstream contracts produced.
Tracking the upstream constants would let a later contract silently pool new decisions with
old outcomes; a literal makes that arrive as an explicit UNSUPPORTED_VERSION refusal."""

type PaperTradeStatus = Literal["PENDING_ENTRY", "OPEN", "RESOLVED", "CANCELLED", "INVALID"]
"""PENDING_ENTRY: a Phase 8 selection is waiting for its first post-decision price. OPEN: an
entry price was taken and the horizon is running. RESOLVED: an expiry price was taken and the
direction was scored. CANCELLED: the identity the trade belonged to stopped existing before it
could finish. INVALID: the canonical data needed to enter or resolve never arrived in time."""

type PaperOutcome = Literal["UNRESOLVED", "WIN", "LOSS", "DRAW", "INVALID"]
"""UNRESOLVED covers PENDING_ENTRY, OPEN and CANCELLED: nothing was measured, and a cancelled
trade is deliberately not counted as a loss."""

type PaperDirection = Literal["UP", "DOWN"]
"""What Phase 8 selected. A direction, never an instruction and never a broker control."""

type PaperEventType = Literal["PENDING_CREATED", "OPENED", "RESOLVED", "CANCELLED", "INVALIDATED"]

REASON_CODES = (
    "BOARD_SELECTION",
    "ENTRY_FILLED",
    "EXPIRY_FILLED",
    "CONTEXT_CHANGED",
    "ASSET_CHANGED",
    "ENTRY_TIMEOUT",
    "RESOLUTION_TIMEOUT",
    "DATA_UNAVAILABLE",
    "VERSION_UNSUPPORTED",
    "BOARD_INELIGIBLE",
    "DUPLICATE",
    "SLOT_RESET",
    "RESTART_POLICY",
    "RESTART_UNRESOLVABLE",
)
"""Every code a trade can carry. A trade never ends silently: a CANCELLED or INVALID trade
always names at least one of these, and the two timeouts are also the only ``invalidReasons``
this version can produce."""

REJECTION_CODES = (
    "PAPER_DISABLED",
    "UNSUPPORTED_VERSION",
    "BOARD_INELIGIBLE",
    "NO_SELECTION",
    "CHRONOLOGY_INVALID",
    "DUPLICATE",
    "SKIPPED_ALREADY_OPEN",
    "SKIPPED_PLATFORM_LIMIT",
)
"""Why an arriving board produced no paper intent at all. A refusal is always named; the
engine never declines to simulate without saying which rule declined."""

MAX_REASONS = 8

type Code = str


class PaperTrade(Model):
    """One hypothetical entry taken from one Phase 8 selection, and what became of it.

    Every field a later calibration phase could want to group by is on the record — the
    selection's rank, score, confidence, agreement, regime and board status alongside the
    prices and the outcome — because a resolved trade is the only durable evidence this system
    produces, and re-deriving its context later is impossible.
    """

    paperTradeId: UUID
    """Deterministic (UUID5) over the selection identity. The same board replayed under the
    same versions produces the same id, so replay cannot fork history."""

    platform: Platform
    slotId: int = Field(ge=1, le=9, strict=True)
    assetName: str = Field(min_length=1, max_length=120)
    contextId: UUID

    direction: PaperDirection

    boardAsOf: int = Field(strict=True)
    """The primary close the Phase 8 cohort described."""
    decisionAvailableAt: int = Field(strict=True)
    """Canonical market event time at which the complete decision first existed. Never assumed
    to equal ``boardAsOf``: the ninth slot of a cohort is processed after the bar it describes
    has closed, and entering at ``boardAsOf`` would be a price nobody could have acted on."""

    rank: int | None = Field(default=None, ge=1, le=9)
    rankScore: float = Field(ge=0, le=1)
    ensembleConfidence: float = Field(ge=0, le=1)
    agreement: float = Field(ge=0, le=1)
    primaryRegime: Regime
    regimeConfidence: float = Field(ge=0, le=1)
    leadMargin: float | None = Field(default=None, ge=0, le=1)
    boardStatus: BoardStatus

    entryTime: int | None = Field(default=None, strict=True)
    entryPrice: float | None = Field(default=None, gt=0, strict=True)
    entrySource: SourceType | None = None
    entryQuality: QualityState | None = None

    durationMs: int = Field(gt=0, strict=True)

    expiryTargetTime: int | None = Field(default=None, strict=True)
    """``entryTime + durationMs``. Absent until an entry price exists, because a horizon with
    no entry has nothing to run from."""
    expiryTime: int | None = Field(default=None, strict=True)
    expiryPrice: float | None = Field(default=None, gt=0, strict=True)
    expirySource: SourceType | None = None
    expiryQuality: QualityState | None = None

    priceDelta: float | None = None
    priceDeltaBps: float | None = None
    """Descriptive movement only. It is not profit, and it is not payout-adjusted."""

    status: PaperTradeStatus
    outcome: PaperOutcome

    paperCurrency: str | None = Field(default=None, max_length=8)
    paperStake: float | None = Field(default=None, gt=0)
    paperPayoutRate: float | None = Field(default=None, ge=0, le=10)
    realizedPaperPnl: float | None = None
    """Simulated money, and only when an explicit stake, payout rate and currency were
    configured at entry. ``None`` means unknown, never zero."""

    featureVersion: str = Field(min_length=1, max_length=40)
    regimeVersion: str = Field(min_length=1, max_length=40)
    strategyVersion: str = Field(min_length=1, max_length=40)
    rankingVersion: str = Field(min_length=1, max_length=40)
    paperVersion: str = Field(min_length=1, max_length=40)

    createdAtMarketTime: int = Field(strict=True)
    resolvedAtMarketTime: int | None = Field(default=None, strict=True)

    reasons: list[Code] = Field(default_factory=list, max_length=MAX_REASONS)
    invalidReasons: list[Code] = Field(default_factory=list, max_length=MAX_REASONS)


class PaperTradeEvent(Model):
    """One append-only lifecycle transition, timed in market time.

    Deliberately small. This is a durable audit trail of *when the state changed and why*, not
    an event-sourced rebuild of the whole trade: the trade snapshot is persisted alongside it
    and is the authoritative record of values.
    """

    paperTradeId: UUID
    eventType: PaperEventType
    eventTime: int = Field(strict=True)

    platform: Platform
    slotId: int = Field(ge=1, le=9, strict=True)
    assetName: str = Field(min_length=1, max_length=120)
    contextId: UUID

    status: PaperTradeStatus
    outcome: PaperOutcome
    price: float | None = Field(default=None, gt=0)
    reason: Code | None = Field(default=None, max_length=48)

    paperVersion: str = Field(min_length=1, max_length=40)


class PaperSettlement(Model):
    """The clean downstream hand-off for a resolved paper trade.

    Phase 9.5's daily session guard will consume exactly this and nothing else, so it carries
    the outcome and the optional simulated money and no analysis at all. Emitted once per
    resolved trade, never re-emitted.
    """

    tradeId: UUID
    platform: Platform
    assetName: str = Field(min_length=1, max_length=120)
    settledAt: int = Field(strict=True)
    outcome: Literal["WIN", "LOSS", "DRAW"]
    currency: str | None = Field(default=None, max_length=8)
    stake: float | None = Field(default=None, gt=0)
    payoutRate: float | None = Field(default=None, ge=0, le=10)
    realizedPnl: float | None = None
    paperVersion: str = Field(min_length=1, max_length=40)


class PaperStats(Model):
    """Descriptive statistics over resolved paper trades. Nothing here is calibrated.

    Phase 9 records evidence; Phase 10 analyses it. A win rate computed over a few dozen
    trades is a description of what happened, not a claim about what will happen, and no
    threshold anywhere in Phases 6-8 may be changed on the strength of it.
    """

    platform: Platform | None = None
    """``None`` is the combined view across platforms."""
    paperVersion: str = Field(min_length=1, max_length=40)

    resolved: int = Field(ge=0)
    wins: int = Field(ge=0)
    losses: int = Field(ge=0)
    draws: int = Field(ge=0)
    invalid: int = Field(ge=0)
    cancelled: int = Field(ge=0)

    winRateExcludingDraws: float | None = Field(default=None, ge=0, le=1)
    winRateIncludingDraws: float | None = Field(default=None, ge=0, le=1)

    currentWinStreak: int = Field(ge=0)
    currentLossStreak: int = Field(ge=0)
    maxWinStreak: int = Field(ge=0)
    maxLossStreak: int = Field(ge=0)

    averagePriceDeltaBps: float | None = None

    grossPaperProfit: float | None = None
    grossPaperLoss: float | None = None
    netPaperPnl: float | None = None
    """All three are ``None`` unless at least one resolved trade carried an accounting
    snapshot. Trades without one contribute their outcome and not their money."""


class PlatformPaperDiagnostics(Model):
    """Compact per-platform summary for the read API and the desktop diagnostics block.

    The outcome tallies describe the retained tail of finished trades, so they match
    ``PaperStats``; the cumulative process counters live on ``PaperEngineState`` instead. The
    two agree until the tail reaches its cap.
    """

    platform: Platform
    pending: int = Field(ge=0)
    open: int = Field(ge=0)
    resolved: int = Field(ge=0)
    wins: int = Field(ge=0)
    losses: int = Field(ge=0)
    draws: int = Field(ge=0)
    invalid: int = Field(ge=0)
    cancelled: int = Field(ge=0)
    durationMs: int = Field(gt=0)
    marketTime: int | None = Field(default=None, strict=True)
    """The latest canonical market event time this platform has shown the engine. The paper
    layer has no other clock."""


class PaperEngineState(Model):
    """Everything the engine will say about itself. Counters are cumulative for the process."""

    paperVersion: str = Field(min_length=1, max_length=40)
    enabled: bool
    accountingConfigured: bool
    settingsError: str | None = Field(default=None, max_length=200)

    pending: int = Field(ge=0)
    open: int = Field(ge=0)
    resolved: int = Field(ge=0)
    cancelled: int = Field(ge=0)
    invalid: int = Field(ge=0)

    duplicateSelections: int = Field(ge=0)
    skippedAlreadyOpen: int = Field(ge=0)
    skippedPlatformLimit: int = Field(ge=0)
    entryTimeouts: int = Field(ge=0)
    resolutionTimeouts: int = Field(ge=0)
    contextCancellations: int = Field(ge=0)
    unsupportedVersions: int = Field(ge=0)
    settlements: int = Field(ge=0)

    platforms: list[PlatformPaperDiagnostics] = Field(default_factory=list, max_length=2)
