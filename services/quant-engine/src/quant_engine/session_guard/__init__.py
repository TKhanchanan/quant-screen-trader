"""Daily session guard (Phase 9.5).

Consumes Phase 9 ``PaperSettlement`` objects, keeps one trading day's realized accounting, and
exposes a single hard permission — ``canOpenNewEntry`` — that the surrounding application may
treat as a circuit breaker.

It is a stop system. It never creates an entry, never chooses a direction, slot, asset or score,
and never changes a threshold anywhere upstream: the quant layers keep producing exactly the
decisions they would have produced, and this layer only decides whether the session is still
allowed to act on them. There is no martingale, no loss recovery and no stake escalation in it,
and a test asserts that.
"""

from quant_engine.session_guard.accounting import Contribution, aggregate, classify, summarize
from quant_engine.session_guard.engine import GuardUpdate, SessionGuard
from quant_engine.session_guard.models import (
    MAX_HISTORY,
    REJECTION_CODES,
    SESSION_GUARD_VERSION,
    SUPPORTED_PAPER_VERSION,
    AccountingSource,
    BlockReason,
    DailySession,
    DailySessionStatus,
    DailySessionSummary,
    NotificationType,
    SessionEventType,
    SessionGuardEvent,
    SessionGuardState,
    SessionNotification,
    StopReason,
)
from quant_engine.session_guard.policy import (
    DEFAULT_PROFILE,
    boundary_ms,
    loss_limit_reached,
    loss_progress,
    next_boundary_ms,
    remaining_to_target,
    session_id,
    target_progress,
    target_reached,
    trading_date,
)
from quant_engine.session_guard.settings import (
    DEFAULT_CURRENCY,
    DEFAULT_TIMEZONE,
    SessionGuardSettings,
)

__all__ = [
    "DEFAULT_CURRENCY",
    "DEFAULT_PROFILE",
    "DEFAULT_TIMEZONE",
    "MAX_HISTORY",
    "REJECTION_CODES",
    "SESSION_GUARD_VERSION",
    "SUPPORTED_PAPER_VERSION",
    "AccountingSource",
    "BlockReason",
    "Contribution",
    "DailySession",
    "DailySessionStatus",
    "DailySessionSummary",
    "GuardUpdate",
    "NotificationType",
    "SessionEventType",
    "SessionGuard",
    "SessionGuardEvent",
    "SessionGuardSettings",
    "SessionGuardState",
    "SessionNotification",
    "StopReason",
    "aggregate",
    "boundary_ms",
    "classify",
    "loss_limit_reached",
    "loss_progress",
    "next_boundary_ms",
    "remaining_to_target",
    "session_id",
    "summarize",
    "target_progress",
    "target_reached",
    "trading_date",
]
