"""Session-risk wire models (Phase 9.5).

This layer answers one question — *how much have we actually gained or lost today?* — and uses
the answer for exactly one purpose: deciding whether the session may still accept new entries.

It is a stop system, not a strategy. Nothing here ranks, scores, sizes or directs anything. A
profit target that is nearly met never loosens a Phase 8 gate, and a losing streak never raises
a stake: the quant layers keep producing exactly the decisions they would have produced anyway,
and the guard only ever withdraws permission. There is no martingale, no loss recovery and no
stake escalation anywhere in the package, and a test asserts the words do not appear in it.

Money is only ever what Phase 9 measured and an operator explicitly configured. A settlement
with no monetary value contributes its outcome to the win/loss tally and nothing to the P/L.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field

from quant_engine.configuration import Model

SESSION_GUARD_VERSION = "qst-session-guard-v1"
"""Target and loss semantics, trading-date derivation, lock rules and the permission contract.
Any change to how a session is stopped requires a new string, so sessions accounted under two
definitions can never be compared as if they meant the same thing."""

SUPPORTED_PAPER_VERSION = "qst-paper-v1"
"""Deliberately a literal, not an import of the Phase 9 constant.

A daily P/L is only meaningful against the payout semantics that produced it. Tracking the
upstream constant would let a later settlement contract flow silently into an accounting
total nobody re-checked; a literal makes that arrive as an explicit rejection instead."""

type DailySessionStatus = Literal[
    "DISABLED",
    "ACTIVE",
    "TARGET_REACHED",
    "LOSS_LIMIT_REACHED",
    "WAITING_FOR_SETTLEMENT",
    "STOPPED_MANUALLY",
    "COMPLETED",
    "LOCKED_FOR_DAY",
    "ACCOUNTING_ERROR",
]
"""DISABLED: the guard is off and enforces nothing. ACTIVE: trading day open, entries permitted.
TARGET_REACHED / LOSS_LIMIT_REACHED: a stop condition fired and new entries are already refused.
WAITING_FOR_SETTLEMENT: stopped, but Phase 9 still has unresolved trades whose outcomes are
allowed to finish. STOPPED_MANUALLY: the operator ended the session. COMPLETED: stopped and
everything has settled. LOCKED_FOR_DAY: completed and no re-arm is available for this trading
date. ACCOUNTING_ERROR: the accounting could not be trusted, so the guard failed closed."""

type StopReason = Literal[
    "DAILY_PROFIT_TARGET", "DAILY_LOSS_LIMIT", "MANUAL_STOP", "ACCOUNTING_ERROR"
]

type BlockReason = Literal[
    "DAILY_PROFIT_TARGET",
    "DAILY_LOSS_LIMIT",
    "MANUAL_STOP",
    "LOCKED_FOR_DAY",
    "ACCOUNTING_ERROR",
    "GUARD_DISABLED",
]
"""Why new entries are refused. ``GUARD_DISABLED`` is never a block: it is reported so a reader
can tell "the guard permits this" from "the guard is not watching", which are different facts."""

type AccountingSource = Literal["PAPER"]
"""Where the money came from. ``PAPER`` is Phase 9's simulated settlements and is the only
source this version has. The field exists so a future trustworthy source can be distinguished
from simulation in stored history rather than inferred from the date."""

type SessionEventType = Literal[
    "SESSION_CREATED",
    "SETTLEMENT_APPLIED",
    "SETTLEMENT_REJECTED",
    "PROFIT_TARGET_REACHED",
    "LOSS_LIMIT_REACHED",
    "MANUAL_STOP",
    "WAITING_FOR_SETTLEMENT",
    "SESSION_COMPLETED",
    "SESSION_LOCKED",
    "SESSION_RESET",
    "ACCOUNTING_ERROR",
]

type NotificationType = Literal[
    "PROFIT_TARGET_REACHED", "LOSS_LIMIT_REACHED", "SESSION_COMPLETED", "ACCOUNTING_ERROR"
]

REJECTION_CODES = (
    "DUPLICATE",
    "CURRENCY_MISMATCH",
    "NO_MONETARY_VALUE",
    "NOT_FINITE",
    "LATE_SETTLEMENT",
    "UNSUPPORTED_PAPER_VERSION",
)
"""Why a settlement did not contribute money. ``NO_MONETARY_VALUE`` and ``CURRENCY_MISMATCH``
still contribute their outcome to the win/loss tally — the market really did do that — while
``DUPLICATE``, ``NOT_FINITE``, ``LATE_SETTLEMENT`` and an unsupported contract contribute
nothing at all."""

MAX_HISTORY = 365


class DailySession(Model):
    """One trading day's realized accounting and the permission that follows from it.

    Every number here is realized: it describes outcomes that have actually settled. Nothing is
    marked to market, estimated from an open position or predicted, because a stop decision
    taken on an unrealized number would stop a session on money that does not exist yet.
    """

    sessionId: UUID
    """Deterministic over profile, trading date, timezone, currency and guard version, so the
    same day reloads as the same session rather than as a fresh one."""

    sessionDate: str = Field(min_length=10, max_length=10)
    """The local trading date, ``YYYY-MM-DD``, in the configured timezone — never the UTC date."""
    timezone: str = Field(min_length=1, max_length=64)
    resetHour: int = Field(ge=0, le=23, strict=True)

    startedAt: int = Field(strict=True)
    completedAt: int | None = Field(default=None, strict=True)
    nextResetAt: int = Field(strict=True)

    status: DailySessionStatus
    accountingSource: AccountingSource

    currency: str = Field(min_length=1, max_length=8)
    profitTarget: float | None = Field(default=None, gt=0)
    lossLimit: float | None = Field(default=None, gt=0)

    realizedPnl: float = 0.0
    """Signed sum of every settlement that carried money in this session's own currency."""
    grossProfit: float = Field(default=0.0, ge=0)
    grossLoss: float = Field(default=0.0, ge=0)
    """Absolute magnitude of the losing side, so ``realizedPnl == grossProfit - grossLoss``."""

    wins: int = Field(default=0, ge=0)
    losses: int = Field(default=0, ge=0)
    draws: int = Field(default=0, ge=0)
    invalid: int = Field(default=0, ge=0)

    resolvedTrades: int = Field(default=0, ge=0)
    """Settlements counted in the win/loss tally, monetary or not."""
    monetaryTrades: int = Field(default=0, ge=0)
    """The subset that moved ``realizedPnl``. The gap between the two is the honest measure of
    how much of the day is unpriced."""
    openTrades: int = Field(default=0, ge=0)
    """Phase 9 trades still unresolved, as last observed. Runtime awareness, never accounting."""

    largestWin: float = Field(default=0.0, ge=0)
    largestLoss: float = Field(default=0.0, le=0)
    """Stored negative, so it reads on the same axis as ``realizedPnl``. Zero means none yet."""

    peakRealizedPnl: float = 0.0
    troughRealizedPnl: float = 0.0
    maxRealizedDrawdown: float = Field(default=0.0, ge=0)
    """Largest fall from a realized peak within the session. Descriptive only: nothing in this
    layer reacts to it."""

    targetReachedAt: int | None = Field(default=None, strict=True)
    lossLimitReachedAt: int | None = Field(default=None, strict=True)
    tradesToTarget: int | None = Field(default=None, ge=0)
    """Monetary settlements counted at the instant the target fired. Frozen there: trades that
    settle afterwards change the final P/L and never this number."""
    stopReason: StopReason | None = None

    canOpenNewEntry: bool = True
    blockReason: BlockReason | None = None

    duplicateSettlements: int = Field(default=0, ge=0)
    currencyMismatches: int = Field(default=0, ge=0)
    nonMonetarySettlements: int = Field(default=0, ge=0)
    rejectedSettlements: int = Field(default=0, ge=0)
    lateSettlements: int = Field(default=0, ge=0)

    notifiedTarget: bool = False
    notifiedLossLimit: bool = False
    notifiedCompleted: bool = False
    """A transition notifies once. Rebuilding this session from storage never notifies again."""

    revision: int = Field(default=0, ge=0)
    """Monotonic per session. The durable record is append-only, so the highest revision for a
    session id is its latest state."""

    sessionGuardVersion: str = Field(min_length=1, max_length=40)


class SessionGuardEvent(Model):
    """One append-only transition. The durable record of *what happened and when*.

    Restart recovery replays these rather than trusting a snapshot: a snapshot and an event log
    written in separate files can disagree after a crash, and an accounting layer that might
    double-count a settlement is worse than one that has to do a little work at start-up.
    """

    eventId: UUID
    sessionId: UUID
    eventTime: int = Field(strict=True)
    type: SessionEventType

    tradeId: UUID | None = None
    outcome: Literal["WIN", "LOSS", "DRAW"] | None = None
    amount: float | None = None
    """The money this event moved, already validated and in the session's currency. ``None``
    when the settlement carried none."""
    currency: str | None = Field(default=None, max_length=8)
    reason: str | None = Field(default=None, max_length=48)
    message: str | None = Field(default=None, max_length=200)

    sessionGuardVersion: str = Field(min_length=1, max_length=40)


class SessionNotification(Model):
    """A state change worth telling the operator about, once.

    Python never talks to an operating system notification centre. It states that something
    notification-worthy happened and the desktop decides how to show it, which keeps the engine
    headless and testable.
    """

    eventId: UUID
    sessionId: UUID
    type: NotificationType
    title: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=400)
    occurredAt: int = Field(strict=True)
    sessionGuardVersion: str = Field(min_length=1, max_length=40)


class DailySessionSummary(Model):
    """The immutable record of a finished (or in-flight) trading day, for history and review.

    Derived entirely from the session it describes. Kept separate so a reader has one flat shape
    with the ratios already computed, rather than re-deriving them in three different places.
    """

    sessionId: UUID
    date: str = Field(min_length=10, max_length=10)
    timezone: str = Field(min_length=1, max_length=64)

    startedAt: int = Field(strict=True)
    endedAt: int | None = Field(default=None, strict=True)

    status: DailySessionStatus
    stopReason: StopReason | None = None
    accountingSource: AccountingSource

    currency: str = Field(min_length=1, max_length=8)
    profitTarget: float | None = Field(default=None, gt=0)
    lossLimit: float | None = Field(default=None, gt=0)

    realizedPnl: float
    grossProfit: float = Field(ge=0)
    grossLoss: float = Field(ge=0)

    wins: int = Field(ge=0)
    losses: int = Field(ge=0)
    draws: int = Field(ge=0)
    invalid: int = Field(ge=0)
    resolvedTrades: int = Field(ge=0)
    monetaryTrades: int = Field(ge=0)

    winRateExcludingDraws: float | None = Field(default=None, ge=0, le=1)
    winRateIncludingDraws: float | None = Field(default=None, ge=0, le=1)
    """``None`` whenever the denominator is zero. Descriptive, over one day, and never a claim
    about what the next day will do."""

    largestWin: float = Field(ge=0)
    largestLoss: float = Field(le=0)

    peakRealizedPnl: float
    troughRealizedPnl: float
    maxRealizedDrawdown: float = Field(ge=0)

    targetReached: bool
    lossLimitReached: bool
    targetReachedAt: int | None = Field(default=None, strict=True)
    lossLimitReachedAt: int | None = Field(default=None, strict=True)
    tradesToTarget: int | None = Field(default=None, ge=0)
    durationToTargetMs: int | None = Field(default=None, ge=0)

    sessionGuardVersion: str = Field(min_length=1, max_length=40)


class SessionGuardState(Model):
    """Everything the guard will say about itself right now."""

    sessionGuardVersion: str = Field(min_length=1, max_length=40)
    enabled: bool
    accountingSource: AccountingSource
    settingsError: str | None = Field(default=None, max_length=200)

    canOpenNewEntry: bool
    blockReason: BlockReason | None = None
    """The hard permission the surrounding application may treat as a circuit breaker. It is a
    veto and nothing else: it never asks for an entry, and it carries no direction, slot, asset
    or score."""

    shutdownRequested: bool = False
    """Set only once new entries are blocked, the session is persisted, the notification has
    been raised and — when configured — every open outcome has settled. Python never terminates
    the application; Electron owns that and reads this."""

    session: DailySession | None = None
    summary: DailySessionSummary | None = None
    targetProgress: float | None = Field(default=None, ge=0, le=1)
    lossProgress: float | None = Field(default=None, ge=0, le=1)
    remainingToTarget: float | None = None
    nextResetAt: int | None = Field(default=None, strict=True)
    openTrades: int = Field(default=0, ge=0)

    notifications: list[SessionNotification] = Field(default_factory=list, max_length=16)
    sessions: int = Field(default=0, ge=0)
