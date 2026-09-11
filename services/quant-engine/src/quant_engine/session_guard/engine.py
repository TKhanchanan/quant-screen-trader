"""Daily session lifecycle (Phase 9.5).

The guard owns one trading day at a time plus a bounded tail of finished ones. It consumes
Phase 9 ``PaperSettlement`` objects, keeps a realized running total, and uses it for one
purpose only: deciding whether the session may still accept new entries.

Three properties are load-bearing.

**It only ever withdraws permission.** There is no path from a target that is nearly met, or a
run of losses, to a different Phase 8 gate, a different stake or a different strategy. The quant
layers are not imported here and could not be changed from here.

**It counts realized money only.** An open paper trade that is probably going to win is worth
nothing until it settles. Stopping a day on a number that has not happened yet would be
stopping on a prediction.

**It never reads a clock of its own.** Which session a settlement belongs to is decided by
``settledAt``; anything that genuinely needs "now" — rolling into the next trading day, reading
current state — takes it as an argument from the caller. The same settlement sequence therefore
replays to the same sessions, transitions, totals and triggers every time.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal
from uuid import UUID, uuid5

from quant_engine.paper.models import PaperSettlement
from quant_engine.session_guard.accounting import Contribution, aggregate, classify, summarize
from quant_engine.session_guard.models import (
    MAX_HISTORY,
    SESSION_GUARD_VERSION,
    AccountingSource,
    BlockReason,
    DailySession,
    DailySessionStatus,
    NotificationType,
    SessionEventType,
    SessionGuardEvent,
    SessionGuardState,
    SessionNotification,
    StopReason,
)
from quant_engine.session_guard.policy import (
    DEFAULT_PROFILE,
    SESSION_NAMESPACE,
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
from quant_engine.session_guard.settings import SessionGuardSettings

EVENT_CAPACITY = 512
NOTIFICATION_CAPACITY = 16

TERMINAL: frozenset[str] = frozenset(
    {
        "TARGET_REACHED",
        "LOSS_LIMIT_REACHED",
        "WAITING_FOR_SETTLEMENT",
        "STOPPED_MANUALLY",
        "COMPLETED",
        "LOCKED_FOR_DAY",
        "ACCOUNTING_ERROR",
    }
)
"""Every status in which the session has stopped accepting entries. Terminal states are
absorbing: nothing moves a session back out of one, including turning the guard off."""

OPEN_STATUSES: frozenset[str] = frozenset({"ACTIVE", "DISABLED"})


@dataclass(frozen=True, slots=True)
class GuardUpdate:
    """What one input did to the session layer. ``sessions`` and ``events`` are for persistence;
    ``notifications`` are transitions the desktop may show, raised once each."""

    sessions: tuple[DailySession, ...] = ()
    events: tuple[SessionGuardEvent, ...] = ()
    notifications: tuple[SessionNotification, ...] = ()

    @property
    def empty(self) -> bool:
        return not (self.sessions or self.events or self.notifications)


@dataclass(slots=True)
class _Collector:
    """Accumulates one input's output.

    ``dirty`` rather than a row per mutation: one settlement can move a session through several
    fields and two statuses, and persisting each intermediate would fill the durable record with
    states that never meant anything on their own.
    """

    sessions: list[DailySession]
    events: list[SessionGuardEvent]
    notifications: list[SessionNotification]
    dirty: bool = False

    def update(self) -> GuardUpdate:
        return GuardUpdate(
            sessions=tuple(self.sessions),
            events=tuple(self.events),
            notifications=tuple(self.notifications),
        )


def _collector() -> _Collector:
    return _Collector(sessions=[], events=[], notifications=[])


class SessionGuard:
    """One instance owns the current trading day and a bounded tail of finished ones."""

    def __init__(
        self,
        settings: SessionGuardSettings | None = None,
        *,
        profile: str = DEFAULT_PROFILE,
        source: AccountingSource = "PAPER",
    ) -> None:
        self.settings = settings if settings is not None else SessionGuardSettings()
        self.settingsError: str | None = None
        self.profile = profile
        self.source: AccountingSource = source
        self.current: DailySession | None = None
        self.history: deque[DailySession] = deque(maxlen=MAX_HISTORY)
        self.eventLog: deque[SessionGuardEvent] = deque(maxlen=EVENT_CAPACITY)
        self.notifications: deque[SessionNotification] = deque(maxlen=NOTIFICATION_CAPACITY)
        self.shutdownRequested = False
        self.openTrades = 0
        self._processed: set[UUID] = set()
        self._replaying = False

    # --- inputs ------------------------------------------------------------------------

    def tick(self, now_ms: int, *, unresolved: int = 0) -> GuardUpdate:
        """Advance the wall-clock-dependent parts: the trading day and the drain to completion.

        The caller supplies ``now_ms`` because this layer has no clock. The day boundary is the
        only thing in the guard that genuinely depends on the present moment, and a background
        tick is where a new trading day is born rather than inside a read.
        """
        collected = _collector()
        self.openTrades = unresolved
        self._ensure(
            trading_date(now_ms, self.settings.timezone, self.settings.resetHour), now_ms, collected
        )
        self._advance(collected, now_ms, unresolved)
        return self._finish(collected)

    def apply_settlement(self, settlement: PaperSettlement, *, unresolved: int = 0) -> GuardUpdate:
        """Account one resolved Phase 9 outcome against the day it settled in.

        The session is chosen by ``settledAt`` and never by the current time: a trade entered at
        23:59 that settles three seconds after midnight belongs to the new trading day, because
        that is when its money actually existed.
        """
        collected = _collector()
        self.openTrades = unresolved
        day = trading_date(settlement.settledAt, self.settings.timezone, self.settings.resetHour)
        session = self._ensure(day, settlement.settledAt, collected)
        belongs = session.sessionDate == day.isoformat()
        contribution = classify(
            settlement,
            currency=session.currency,
            already_processed=settlement.tradeId in self._processed,
            belongs_to_session=belongs,
        )
        if contribution.reason != "DUPLICATE":
            self._processed.add(settlement.tradeId)
        session = self._write(
            collected,
            session.model_copy(update=aggregate(session, settlement.outcome, contribution)),
        )
        self._record(
            collected,
            session,
            "SETTLEMENT_APPLIED" if contribution.counted else "SETTLEMENT_REJECTED",
            settlement.settledAt,
            trade_id=settlement.tradeId,
            outcome=settlement.outcome,
            amount=contribution.amount if contribution.monetary else None,
            currency=settlement.currency,
            reason=contribution.reason,
        )
        if contribution.reason == "NOT_FINITE":
            self._fail_closed(collected, settlement.settledAt, "Non-finite settlement value")
            return self._finish(collected)
        self._evaluate(collected, settlement.settledAt, unresolved)
        return self._finish(collected)

    def observe(self, unresolved: int, at_ms: int) -> GuardUpdate:
        """Record how many Phase 9 outcomes are still running, and let a stopped day drain.

        Deliberately separate from ``tick``: this runs on market events, which in a replay can
        be any point in history, and a market event must never create tomorrow's session. Only
        the wall-clock tick rolls the calendar.
        """
        collected = _collector()
        self.openTrades = unresolved
        if self.current is not None:
            self._advance(collected, at_ms, unresolved)
        return self._finish(collected)

    def stop_session(self, now_ms: int, *, unresolved: int = 0) -> GuardUpdate:
        """End the trading day on the operator's instruction.

        Nothing may stand between an operator and this. It takes no settlement, needs no target,
        reads no accounting and cannot be refused by a missing configuration: it is the control
        somebody reaches for when something is wrong.
        """
        collected = _collector()
        self.openTrades = unresolved
        day = trading_date(now_ms, self.settings.timezone, self.settings.resetHour)
        session = self._ensure(day, now_ms, collected)
        if session.status in TERMINAL:
            return self._finish(collected)
        session = self._write(
            collected,
            session.model_copy(update={"status": "STOPPED_MANUALLY", "stopReason": "MANUAL_STOP"}),
        )
        self._record(
            collected, session, "MANUAL_STOP", now_ms, message="Operator stopped the session"
        )
        self._advance(collected, now_ms, unresolved, settle=False)
        return self._finish(collected)

    def update_settings(
        self, settings: SessionGuardSettings, now_ms: int, *, unresolved: int = 0
    ) -> GuardUpdate:
        """Apply new limits immediately, including to money already realized today.

        A target lowered below the day's current profit is met the moment it is set, and a loss
        limit tightened below the day's current loss is breached the moment it is set. The
        alternative — new limits only counting from the next settlement — would let an operator
        tighten a limit and still be trading past it.
        """
        collected = _collector()
        previous = self.settings
        self.settings = settings
        self.settingsError = None
        self.openTrades = unresolved
        if self.current is not None and self._identity_changed(previous, settings):
            # Currency or calendar changed, so today is a different session entirely. The old
            # one keeps its own record rather than being retrofitted with new terms.
            self._retire(collected, self.current, now_ms)
            self.current = None
            self._processed.clear()
        session = self._ensure(
            trading_date(now_ms, settings.timezone, settings.resetHour), now_ms, collected
        )
        session = self._write(
            collected,
            session.model_copy(
                update={
                    "profitTarget": settings.dailyProfitTarget,
                    "lossLimit": settings.dailyLossLimit,
                }
            ),
        )
        self._evaluate(collected, now_ms, unresolved)
        return self._finish(collected)

    def restore(
        self,
        sessions: Iterable[DailySession],
        events: Iterable[SessionGuardEvent],
        now_ms: int,
        *,
        unresolved: int = 0,
    ) -> GuardUpdate:
        """Rebuild state at start-up. Today is replayed from its events, never from its snapshot.

        A snapshot and an append-only log live in separate files and can disagree after a crash.
        Trusting the snapshot could double-count a settlement whose id never reached the log;
        replaying the log cannot, because the same record carries both the money and the identity
        that makes it idempotent. Snapshots remain the record for finished days, which no longer
        change.

        Nothing that is reconstructed notifies. A profit target that was reached and announced
        yesterday must not announce itself again because the application restarted.
        """
        collected = _collector()
        latest: dict[UUID, DailySession] = {}
        for stored in sessions:
            previous = latest.get(stored.sessionId)
            if previous is None or stored.revision >= previous.revision:
                latest[stored.sessionId] = stored
        grouped: dict[UUID, list[SessionGuardEvent]] = {}
        for event in sorted(events, key=lambda item: (item.eventTime, str(item.eventId))):
            grouped.setdefault(event.sessionId, []).append(event)
            self.eventLog.append(event)

        current_identity = session_id(
            profile=self.profile,
            day=trading_date(now_ms, self.settings.timezone, self.settings.resetHour),
            timezone=self.settings.timezone,
            currency=self.settings.currency,
            guard_version=SESSION_GUARD_VERSION,
        )
        for identity in sorted(latest, key=lambda key: latest[key].sessionDate):
            if identity != current_identity:
                self.history.append(latest[identity])
        snapshot: DailySession | None = latest.get(current_identity)
        self.openTrades = unresolved
        if snapshot is not None:
            self._replaying = True
            try:
                self.current = self._replay(snapshot, grouped.get(current_identity, ()))
            finally:
                self._replaying = False
        collected.dirty = snapshot is not None
        self._advance(collected, now_ms, unresolved)
        # Reconstruction is not a transition. A locked day comes back locked, and it comes back
        # openable: the close it already performed is not performed again.
        self.shutdownRequested = False
        return self._finish(collected)

    # --- lifecycle ---------------------------------------------------------------------

    def _ensure(self, day: date, at_ms: int, collected: _Collector) -> DailySession:
        """The session that owns ``day``, rolling the calendar forward when it has moved on.

        A settlement for a day already closed cannot reopen it: the current session is returned
        instead, and the caller's identity check turns that into a named LATE_SETTLEMENT rather
        than a silent adjustment to a day somebody has already reviewed.
        """
        key = day.isoformat()
        if self.current is not None and self.current.sessionDate == key:
            return self.current
        if self.current is not None and key < self.current.sessionDate:
            return self.current
        if self.current is not None:
            self._retire(collected, self.current, at_ms)
            self._processed.clear()
        # A new trading day never inherits yesterday's close. The application must be able to
        # open on a day that has not stopped, whatever happened before the boundary.
        self.shutdownRequested = False
        self.current = self._new_session(day, at_ms)
        self._write(collected, self.current)
        self._record(collected, self.current, "SESSION_CREATED", self.current.startedAt)
        return self.current

    def _new_session(self, day: date, at_ms: int) -> DailySession:
        settings = self.settings
        start = max(boundary_ms(day, settings.timezone, settings.resetHour), at_ms)
        return DailySession(
            sessionId=session_id(
                profile=self.profile,
                day=day,
                timezone=settings.timezone,
                currency=settings.currency,
                guard_version=SESSION_GUARD_VERSION,
            ),
            sessionDate=day.isoformat(),
            timezone=settings.timezone,
            resetHour=settings.resetHour,
            startedAt=start,
            nextResetAt=next_boundary_ms(day, settings.timezone, settings.resetHour),
            status="ACTIVE" if settings.enabled else "DISABLED",
            accountingSource=self.source,
            currency=settings.currency,
            profitTarget=settings.dailyProfitTarget,
            lossLimit=settings.dailyLossLimit,
            canOpenNewEntry=True,
            sessionGuardVersion=SESSION_GUARD_VERSION,
        )

    def _retire(self, collected: _Collector, session: DailySession, at_ms: int) -> None:
        """Close a day the calendar has passed and keep it as an immutable record."""
        status: DailySessionStatus = session.status if session.status in TERMINAL else "COMPLETED"
        finished = session.model_copy(
            update={
                "status": status,
                "completedAt": session.completedAt if session.completedAt is not None else at_ms,
                "canOpenNewEntry": False,
                "revision": session.revision + 1,
            }
        )
        self.history.append(finished)
        collected.sessions.append(finished)
        self._record(collected, finished, "SESSION_RESET", at_ms, message="Trading day rolled over")

    def _evaluate(self, collected: _Collector, at_ms: int, unresolved: int) -> None:
        """Check the two stop rules against realized money, then move the session along.

        Only a session that is still open can be stopped. Once a target has fired, a later
        settlement that drags the day back below it changes the final P/L and nothing else:
        the session does not un-trigger, and permission is not returned.
        """
        session = self.current
        if session is None:
            return
        if self.settings.enabled and session.status == "DISABLED":
            session = self._write(collected, session.model_copy(update={"status": "ACTIVE"}))
        elif not self.settings.enabled and session.status == "ACTIVE":
            session = self._write(collected, session.model_copy(update={"status": "DISABLED"}))
        triggered = False
        if self.settings.enabled and session.status in OPEN_STATUSES:
            if target_reached(session.realizedPnl, session.profitTarget):
                session = self._trigger(collected, session, at_ms, "DAILY_PROFIT_TARGET")
                triggered = True
            elif loss_limit_reached(session.realizedPnl, session.lossLimit):
                session = self._trigger(collected, session, at_ms, "DAILY_LOSS_LIMIT")
                triggered = True
        self._advance(collected, at_ms, unresolved, settle=not triggered)

    def _trigger(
        self, collected: _Collector, session: DailySession, at_ms: int, reason: StopReason
    ) -> DailySession:
        profit = reason == "DAILY_PROFIT_TARGET"
        status: DailySessionStatus = "TARGET_REACHED" if profit else "LOSS_LIMIT_REACHED"
        updates: dict[str, object] = {"status": status, "stopReason": reason}
        if profit:
            updates["targetReachedAt"] = at_ms
            # Frozen here on purpose: trades that settle afterwards move the final P/L, and this
            # number is about how many it took to get here.
            updates["tradesToTarget"] = session.monetaryTrades
        else:
            updates["lossLimitReachedAt"] = at_ms
        session = self._write(collected, session.model_copy(update=updates))
        event: SessionEventType = "PROFIT_TARGET_REACHED" if profit else "LOSS_LIMIT_REACHED"
        self._record(collected, session, event, at_ms, amount=session.realizedPnl)
        notify = self.settings.notifyOnProfitTarget if profit else self.settings.notifyOnLossLimit
        if notify:
            self._notify(collected, session, event, at_ms)
        return self.current if self.current is not None else session

    def _advance(
        self, collected: _Collector, at_ms: int, unresolved: int, *, settle: bool = True
    ) -> None:
        """Move a stopped session towards completion, and decide whether a close may be asked for.

        A stop is staged rather than instantaneous: the call that trips a limit blocks new
        entries, records the transition and raises the notification, and completion comes on the
        next observation. Finishing the day inside the same call would mean a target could be
        reached, completed, locked and the application asked to quit before anything had been
        handed to the caller to persist.

        Waiting is not completion, so a session that still has unresolved outcomes moves to
        WAITING_FOR_SETTLEMENT immediately: it has already stopped, and saying so at once is
        what makes the permission and the reason agree.
        """
        session = self.current
        if session is None:
            return
        if session.openTrades != unresolved:
            session = self._write(collected, session.model_copy(update={"openTrades": unresolved}))
        if session.status in ("TARGET_REACHED", "LOSS_LIMIT_REACHED", "STOPPED_MANUALLY"):
            if self.settings.waitForOpenTradesBeforeClose and unresolved > 0:
                session = self._write(
                    collected, session.model_copy(update={"status": "WAITING_FOR_SETTLEMENT"})
                )
                self._record(collected, session, "WAITING_FOR_SETTLEMENT", at_ms)
            elif settle:
                session = self._complete(collected, session, at_ms)
        elif session.status == "WAITING_FOR_SETTLEMENT" and unresolved == 0:
            session = self._complete(collected, session, at_ms)
        self._refresh(collected, session)

    def _complete(self, collected: _Collector, session: DailySession, at_ms: int) -> DailySession:
        session = self._write(
            collected, session.model_copy(update={"status": "COMPLETED", "completedAt": at_ms})
        )
        self._record(collected, session, "SESSION_COMPLETED", at_ms, amount=session.realizedPnl)
        self._notify(collected, session, "SESSION_COMPLETED", at_ms)
        session = self.current if self.current is not None else session
        lock = (
            session.stopReason == "DAILY_PROFIT_TARGET" and self.settings.lockAfterProfitTarget
        ) or (session.stopReason == "DAILY_LOSS_LIMIT" and self.settings.lockAfterLossLimit)
        if lock:
            session = self._write(
                collected, session.model_copy(update={"status": "LOCKED_FOR_DAY"})
            )
            self._record(collected, session, "SESSION_LOCKED", at_ms)
        self._request_close(session)
        return session

    def _fail_closed(self, collected: _Collector, at_ms: int, message: str) -> None:
        """Accounting that cannot be trusted stops the day. Continuing on a number the layer
        knows is wrong would be worse than stopping on one it knows is right."""
        session = self.current
        if session is None:
            return
        session = self._write(
            collected,
            session.model_copy(
                update={"status": "ACCOUNTING_ERROR", "stopReason": "ACCOUNTING_ERROR"}
            ),
        )
        self._record(collected, session, "ACCOUNTING_ERROR", at_ms, message=message[:200])
        self._notify(collected, session, "ACCOUNTING_ERROR", at_ms)
        self._refresh(collected, session)

    def _refresh(self, collected: _Collector, session: DailySession) -> None:
        allowed, reason = self._permission(session)
        if session.canOpenNewEntry == allowed and session.blockReason == reason:
            return
        self._write(
            collected,
            session.model_copy(update={"canOpenNewEntry": allowed, "blockReason": reason}),
        )

    def _permission(self, session: DailySession) -> tuple[bool, BlockReason | None]:
        """The hard gate, and the honest reason behind it.

        A terminal session blocks whether or not the guard is currently enabled. Switching the
        guard off prevents future stops; it does not undo one that has already happened, because
        a loss limit an operator can lift by unticking a box is not a loss limit.
        """
        if session.status == "ACCOUNTING_ERROR":
            return False, "ACCOUNTING_ERROR"
        if session.status == "LOCKED_FOR_DAY":
            return False, "LOCKED_FOR_DAY"
        if session.status in TERMINAL:
            return False, session.stopReason if session.stopReason != "ACCOUNTING_ERROR" else None
        if not self.settings.enabled:
            return True, "GUARD_DISABLED"
        return True, None

    def _request_close(self, session: DailySession) -> None:
        """A close is *requested* once, at the moment the day actually finishes.

        By the time this runs, new entries are blocked, the session and its events have been
        handed to the caller for persistence, the notification has been raised, and — when
        configured — every open outcome has settled. Asking at the moment a target is hit would
        throw away the measurements that were still resolving.

        It is bound to the transition rather than to a recurring check on purpose. A day rebuilt
        from storage has already been closed once, and a background tick that re-asked every
        second would leave an operator unable to open the application at all for the rest of a
        locked day. A manual stop never asks: ending the session is not a request to quit.
        """
        if self._replaying:
            return
        if session.stopReason == "DAILY_PROFIT_TARGET" and self.settings.closeAppOnProfitTarget:
            self.shutdownRequested = True
        elif session.stopReason == "DAILY_LOSS_LIMIT" and self.settings.closeAppOnLossLimit:
            self.shutdownRequested = True

    # --- bookkeeping -------------------------------------------------------------------

    def _write(self, collected: _Collector, session: DailySession) -> DailySession:
        session = session.model_copy(update={"revision": session.revision + 1})
        self.current = session
        collected.dirty = True
        return session

    def _finish(self, collected: _Collector) -> GuardUpdate:
        if collected.dirty and self.current is not None:
            collected.sessions.append(self.current)
        return collected.update()

    def _record(
        self,
        collected: _Collector,
        session: DailySession,
        event_type: SessionEventType,
        at_ms: int,
        *,
        trade_id: UUID | None = None,
        outcome: Literal["WIN", "LOSS", "DRAW"] | None = None,
        amount: float | None = None,
        currency: str | None = None,
        reason: str | None = None,
        message: str | None = None,
    ) -> SessionGuardEvent:
        event = SessionGuardEvent(
            eventId=_event_id(session.sessionId, event_type, trade_id, reason),
            sessionId=session.sessionId,
            eventTime=at_ms,
            type=event_type,
            tradeId=trade_id,
            outcome=outcome,
            amount=amount,
            currency=currency,
            reason=reason,
            message=message,
            sessionGuardVersion=SESSION_GUARD_VERSION,
        )
        if not self._replaying:
            self.eventLog.append(event)
            collected.events.append(event)
        return event

    def _notify(
        self,
        collected: _Collector,
        session: DailySession,
        event_type: SessionEventType,
        at_ms: int,
    ) -> None:
        """Raise a notification-worthy transition once.

        Replay never notifies: a transition rebuilt from storage already happened, and the
        operator has already been told about it. The flags travel with the session so a later
        re-evaluation of the same state cannot announce it a second time either.
        """
        if self._replaying:
            self._mark_notified(collected, session, event_type)
            return
        kind = _NOTIFICATION_OF.get(event_type)
        if kind is None or self._already_notified(session, event_type):
            return
        notification = SessionNotification(
            eventId=_event_id(session.sessionId, event_type, None, None),
            sessionId=session.sessionId,
            type=kind,
            title=_TITLES[kind],
            message=_message(kind, session),
            occurredAt=at_ms,
            sessionGuardVersion=SESSION_GUARD_VERSION,
        )
        self.notifications.append(notification)
        collected.notifications.append(notification)
        self._mark_notified(collected, session, event_type)

    @staticmethod
    def _already_notified(session: DailySession, event_type: SessionEventType) -> bool:
        if event_type == "PROFIT_TARGET_REACHED":
            return session.notifiedTarget
        if event_type == "LOSS_LIMIT_REACHED":
            return session.notifiedLossLimit
        if event_type == "SESSION_COMPLETED":
            return session.notifiedCompleted
        return False

    def _mark_notified(
        self, collected: _Collector, session: DailySession, event_type: SessionEventType
    ) -> None:
        field = {
            "PROFIT_TARGET_REACHED": "notifiedTarget",
            "LOSS_LIMIT_REACHED": "notifiedLossLimit",
            "SESSION_COMPLETED": "notifiedCompleted",
        }.get(event_type)
        if field is None or getattr(session, field):
            return
        if self._replaying:
            self.current = session.model_copy(update={field: True})
            return
        self._write(collected, session.model_copy(update={field: True}))

    @staticmethod
    def _identity_changed(previous: SessionGuardSettings, current: SessionGuardSettings) -> bool:
        return (
            previous.currency != current.currency
            or previous.timezone != current.timezone
            or previous.resetHour != current.resetHour
        )

    # --- replay ------------------------------------------------------------------------

    def _replay(self, snapshot: DailySession, events: Sequence[SessionGuardEvent]) -> DailySession:
        """Rebuild today from its own event log, seeded with the snapshot's terms."""
        self._processed.clear()
        collected = _collector()
        self.current = snapshot.model_copy(
            update={
                "status": "ACTIVE" if self.settings.enabled else "DISABLED",
                "stopReason": None,
                "completedAt": None,
                "targetReachedAt": None,
                "lossLimitReachedAt": None,
                "tradesToTarget": None,
                "realizedPnl": 0.0,
                "grossProfit": 0.0,
                "grossLoss": 0.0,
                "wins": 0,
                "losses": 0,
                "draws": 0,
                "invalid": 0,
                "resolvedTrades": 0,
                "monetaryTrades": 0,
                "largestWin": 0.0,
                "largestLoss": 0.0,
                "peakRealizedPnl": 0.0,
                "troughRealizedPnl": 0.0,
                "maxRealizedDrawdown": 0.0,
                "duplicateSettlements": 0,
                "currencyMismatches": 0,
                "nonMonetarySettlements": 0,
                "rejectedSettlements": 0,
                "lateSettlements": 0,
                "notifiedTarget": False,
                "notifiedLossLimit": False,
                "notifiedCompleted": False,
                "canOpenNewEntry": True,
                "blockReason": None,
                "profitTarget": self.settings.dailyProfitTarget,
                "lossLimit": self.settings.dailyLossLimit,
            }
        )
        for event in events:
            session = self.current
            if session is None:
                break
            if event.type in ("SETTLEMENT_APPLIED", "SETTLEMENT_REJECTED"):
                if event.tradeId is None:
                    continue
                self._processed.add(event.tradeId)
                contribution = Contribution(
                    counted=event.type == "SETTLEMENT_APPLIED",
                    monetary=event.amount is not None,
                    amount=event.amount if event.amount is not None else 0.0,
                    reason=event.reason,
                )
                outcome = event.outcome if event.outcome is not None else "DRAW"
                self.current = session.model_copy(update=aggregate(session, outcome, contribution))
                self._evaluate(collected, event.eventTime, self.openTrades)
            elif event.type == "MANUAL_STOP":
                self.current = session.model_copy(
                    update={"status": "STOPPED_MANUALLY", "stopReason": "MANUAL_STOP"}
                )
            elif event.type == "ACCOUNTING_ERROR":
                self.current = session.model_copy(
                    update={"status": "ACCOUNTING_ERROR", "stopReason": "ACCOUNTING_ERROR"}
                )
        # Converge on the same state the live run ended in, still suppressed. A day that
        # completed before the restart must come back completed, and must not announce it twice.
        last = events[-1].eventTime if events else snapshot.startedAt
        self._advance(collected, last, self.openTrades)
        rebuilt = self.current
        return rebuilt if rebuilt is not None else snapshot

    # --- reads -------------------------------------------------------------------------

    def state(self, now_ms: int) -> SessionGuardState:
        """Everything the guard will say right now. Reads never mutate: the trading day rolls
        over on the background tick, not because somebody opened a panel."""
        session = self.current
        allowed, reason = (
            self._permission(session) if session is not None else (True, self._idle_reason())
        )
        return SessionGuardState(
            sessionGuardVersion=SESSION_GUARD_VERSION,
            enabled=self.settings.enabled,
            accountingSource=self.source,
            settingsError=self.settingsError,
            canOpenNewEntry=allowed,
            blockReason=reason,
            shutdownRequested=self.shutdownRequested,
            session=session,
            summary=summarize(session) if session is not None else None,
            targetProgress=(
                target_progress(session.realizedPnl, session.profitTarget)
                if session is not None
                else None
            ),
            lossProgress=(
                loss_progress(session.realizedPnl, session.lossLimit)
                if session is not None
                else None
            ),
            remainingToTarget=(
                remaining_to_target(session.realizedPnl, session.profitTarget)
                if session is not None
                else None
            ),
            nextResetAt=(
                session.nextResetAt
                if session is not None
                else next_boundary_ms(
                    trading_date(now_ms, self.settings.timezone, self.settings.resetHour),
                    self.settings.timezone,
                    self.settings.resetHour,
                )
            ),
            openTrades=self.openTrades,
            notifications=list(self.notifications),
            sessions=len(self.history) + (1 if session is not None else 0),
        )

    def _idle_reason(self) -> BlockReason | None:
        return None if self.settings.enabled else "GUARD_DISABLED"

    def recent(self, limit: int) -> list[DailySession]:
        """Finished days newest first, with today at the front when there is one."""
        if limit <= 0:
            return []
        rows: list[DailySession] = []
        if self.current is not None:
            rows.append(self.current)
        rows.extend(reversed(self.history))
        return rows[:limit]

    def find(self, identity: UUID) -> DailySession | None:
        if self.current is not None and self.current.sessionId == identity:
            return self.current
        return next((row for row in reversed(self.history) if row.sessionId == identity), None)

    def events(self, limit: int) -> list[SessionGuardEvent]:
        return list(reversed(self.eventLog))[: max(0, limit)]


_NOTIFICATION_OF: dict[str, NotificationType] = {
    "PROFIT_TARGET_REACHED": "PROFIT_TARGET_REACHED",
    "LOSS_LIMIT_REACHED": "LOSS_LIMIT_REACHED",
    "SESSION_COMPLETED": "SESSION_COMPLETED",
    "ACCOUNTING_ERROR": "ACCOUNTING_ERROR",
}

_TITLES: dict[NotificationType, str] = {
    "PROFIT_TARGET_REACHED": "Daily profit target reached",
    "LOSS_LIMIT_REACHED": "Daily loss limit reached",
    "SESSION_COMPLETED": "Daily session completed",
    "ACCOUNTING_ERROR": "Daily accounting error",
}


def _money(value: float, currency: str) -> str:
    symbol = "฿" if currency == "THB" else f"{currency} "
    return f"{'+' if value > 0 else '-' if value < 0 else ''}{symbol}{abs(value):.2f}"


def _message(kind: NotificationType, session: DailySession) -> str:
    realized = _money(session.realizedPnl, session.currency)
    if kind == "PROFIT_TARGET_REACHED":
        target = _money(session.profitTarget or 0.0, session.currency)
        return f"Realized P/L {realized} · target {target} · new entries are stopped."
    if kind == "LOSS_LIMIT_REACHED":
        limit = _money(-(session.lossLimit or 0.0), session.currency)
        return f"Realized P/L {realized} · loss limit {limit} · new entries are stopped."
    if kind == "ACCOUNTING_ERROR":
        return "Daily accounting could not be trusted, so the session was stopped."
    return f"Realized P/L {realized} over {session.resolvedTrades} resolved outcomes."


def _event_id(identity: UUID, event_type: str, trade_id: UUID | None, reason: str | None) -> UUID:
    """Deterministic per session and cause, so a replayed transition is the same event.

    A transition happens once per session, and a settlement is accounted once, so neither needs
    a counter to stay unique — and neither can be duplicated by a restart."""
    parts = [str(identity), event_type]
    if trade_id is not None:
        parts.append(str(trade_id))
    if reason is not None:
        parts.append(reason)
    return uuid5(SESSION_NAMESPACE, "|".join(parts))
