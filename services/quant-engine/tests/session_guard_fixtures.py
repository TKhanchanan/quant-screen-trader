"""Controlled Phase 9 settlements and guard configuration for the session tests.

Settlements are built directly. Phase 9.5's input contract *is* the ``PaperSettlement``, so
constructing one here is reading the contract rather than bypassing a layer — and the money a
daily limit has to react to cannot be steered out of a real market on demand. The end-to-end
test drives real Phase 9 outcomes through the guard so the wiring is covered too.

Every time here is an explicit epoch millisecond in Asia/Bangkok. Nothing in these tests reads
the machine's clock or its timezone: a daily accounting layer that passed only in one country
would not be tested at all.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from quant_engine.configuration import Platform
from quant_engine.paper import PAPER_VERSION
from quant_engine.paper.models import PaperSettlement
from quant_engine.session_guard import SessionGuard, SessionGuardSettings

BANGKOK = ZoneInfo("Asia/Bangkok")
TRADE_NAMESPACE = UUID("9b2f1c07-4d3a-4e55-9c18-7f6a0d2b3e41")


def at(hour: int, minute: int = 0, second: int = 0, millisecond: int = 0, *, day: int = 11) -> int:
    """One instant on 2026-09-{day} in Bangkok, as epoch milliseconds."""
    moment = datetime(2026, 9, day, hour, minute, second, millisecond * 1000, tzinfo=BANGKOK)
    return int(moment.timestamp() * 1000)


def utc(hour: int, minute: int = 0, *, day: int = 11) -> int:
    """The same, stated in UTC, for the tests that prove the two are not interchangeable."""
    return int(datetime(2026, 9, day, hour, minute, tzinfo=UTC).timestamp() * 1000)


NOON = at(12)


def trade_id(label: str) -> UUID:
    """Stable per label, so a test can redeliver the same settlement without holding an id."""
    return uuid5(TRADE_NAMESPACE, label)


def settlement(
    amount: float | None,
    *,
    settled_at: int = NOON,
    label: str | None = None,
    outcome: str | None = None,
    currency: str | None = "THB",
    stake: float | None = 50.0,
    payout: float | None = 0.82,
    platform: Platform = "capitalbear",
    asset: str = "EUR/USD OTC",
    version: str = PAPER_VERSION,
) -> PaperSettlement:
    """One resolved Phase 9 outcome. ``amount=None`` is a real outcome with unknown money."""
    resolved = outcome or ("WIN" if (amount or 0) > 0 else "LOSS" if (amount or 0) < 0 else "DRAW")
    return PaperSettlement(
        tradeId=trade_id(label if label is not None else f"{settled_at}:{amount}:{resolved}"),
        platform=platform,
        assetName=asset,
        settledAt=settled_at,
        outcome=resolved,  # type: ignore[arg-type]
        currency=currency if amount is not None else None,
        stake=stake if amount is not None else None,
        payoutRate=payout if amount is not None else None,
        realizedPnl=amount,
        paperVersion=version,
    )


def settings(**changes: Any) -> SessionGuardSettings:
    """An enabled guard with no limits unless the test states one."""
    values: dict[str, Any] = {"enabled": True}
    values.update(changes)
    return SessionGuardSettings(**values)


def guard(**changes: Any) -> SessionGuard:
    return SessionGuard(settings(**changes))


class Journal:
    """A guard plus everything it asked to have persisted.

    Restart is the property most worth testing here and the easiest to fake, so the tests never
    reach inside the engine for its state: they keep exactly the rows the durable record would
    have kept, throw the instance away, and rebuild from those.
    """

    def __init__(self, engine: SessionGuard) -> None:
        self.engine = engine
        self.sessions: list[Any] = []
        self.events: list[Any] = []

    def take(self, update: Any) -> Any:
        self.sessions.extend(update.sessions)
        self.events.extend(update.events)
        return update

    def settle(self, *settlements: PaperSettlement, unresolved: int = 0) -> None:
        for value in settlements:
            self.take(self.engine.apply_settlement(value, unresolved=unresolved))

    def tick(self, now_ms: int, unresolved: int = 0) -> Any:
        return self.take(self.engine.tick(now_ms, unresolved=unresolved))

    def observe(self, unresolved: int, at_ms: int) -> Any:
        return self.take(self.engine.observe(unresolved, at_ms))

    def stop(self, now_ms: int, unresolved: int = 0) -> Any:
        return self.take(self.engine.stop_session(now_ms, unresolved=unresolved))

    def restart(self, now_ms: int, *, unresolved: int = 0, **changes: Any) -> Journal:
        """Throw the engine away and rebuild it from the rows alone."""
        rebuilt = SessionGuard(settings(**changes) if changes else self.engine.settings)
        journal = Journal(rebuilt)
        journal.sessions = list(self.sessions)
        journal.events = list(self.events)
        journal.take(rebuilt.restore(self.sessions, self.events, now_ms, unresolved=unresolved))
        return journal


def journal(**changes: Any) -> Journal:
    return Journal(guard(**changes))


def feed(
    engine: SessionGuard, amounts: list[float | None], *, start: int = NOON, step: int = 60_000
) -> None:
    """Settle a sequence a minute apart, so every settlement has its own event time."""
    for index, amount in enumerate(amounts):
        engine.apply_settlement(settlement(amount, settled_at=start + index * step))
