"""T-DF, T-DG, T-AN to T-AP: the durable record, the history, and exact replay.

The guard's whole value is that today's number is the same number tomorrow morning, so these
are the tests that hold it to that: the same settlements always produce the same session, the
same transitions and the same totals, and a finished day is never overwritten by the next one.
"""

from __future__ import annotations

from pathlib import Path

import session_guard_fixtures as fixtures
import test_paper_pipeline as pipeline
from features_fixtures import BASE_MS
from quant_engine.market_api import MarketEngine
from quant_engine.market_storage import ParquetStorage
from quant_engine.paper import PaperSettings
from quant_engine.session_guard import (
    DailySession,
    SessionGuard,
    SessionGuardEvent,
    SessionGuardSettings,
)

MONEY = PaperSettings(paperCurrency="THB", paperStake=50, paperPayoutRate=0.82)


def sequence() -> list[float | None]:
    return [200.0, -50.0, 0.0, 150.0, None, -80.0, 41.0]


def run(engine: SessionGuard) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    sessions: list[dict[str, object]] = []
    events: list[dict[str, object]] = []
    for index, amount in enumerate(sequence()):
        update = engine.apply_settlement(
            fixtures.settlement(
                amount,
                label=f"row-{index}",
                outcome="WIN" if amount is None else None,
                settled_at=fixtures.NOON + index * 60_000,
            ),
            unresolved=1 if index < 3 else 0,
        )
        sessions.extend(row.model_dump(mode="json") for row in update.sessions)
        events.extend(row.model_dump(mode="json") for row in update.events)
    return sessions, events


# --- T-DG determinism ------------------------------------------------------------------


def test_the_same_settlements_replay_to_the_same_session_totals_and_events() -> None:
    first = run(fixtures.guard(dailyProfitTarget=600, dailyLossLimit=400))
    second = run(fixtures.guard(dailyProfitTarget=600, dailyLossLimit=400))
    assert first == second
    assert first[0], "the run must actually have produced something"


def test_the_session_identity_is_the_same_engine_after_engine() -> None:
    one = fixtures.guard()
    two = fixtures.guard()
    one.tick(fixtures.NOON)
    two.tick(fixtures.at(23, 30))
    assert one.current is not None and two.current is not None
    assert one.current.sessionId == two.current.sessionId
    assert one.current.sessionDate == two.current.sessionDate == "2026-09-11"


def test_a_different_day_currency_or_timezone_is_a_different_session() -> None:
    identities = set()
    for changes in (
        {},
        {"currency": "USD"},
        {"timezone": "America/New_York"},
    ):
        engine = fixtures.guard(**changes)
        engine.tick(fixtures.NOON)
        assert engine.current is not None
        identities.add(engine.current.sessionId)
    tomorrow = fixtures.guard()
    tomorrow.tick(fixtures.at(12, day=12))
    assert tomorrow.current is not None
    identities.add(tomorrow.current.sessionId)
    assert len(identities) == 4


# --- T-DF history ----------------------------------------------------------------------


def test_two_trading_days_are_two_independent_records() -> None:
    journal = fixtures.journal(dailyProfitTarget=600)
    journal.settle(fixtures.settlement(620, settled_at=fixtures.at(10)))
    journal.tick(fixtures.at(10, 1))
    journal.tick(fixtures.at(9, day=12))
    journal.settle(fixtures.settlement(-120, settled_at=fixtures.at(10, day=12)))

    days = journal.engine.recent(10)
    assert [row.sessionDate for row in days] == ["2026-09-12", "2026-09-11"]
    assert days[0].realizedPnl == -120 and days[0].status == "ACTIVE"
    assert days[1].realizedPnl == 620 and days[1].status == "LOCKED_FOR_DAY"
    assert days[0].sessionId != days[1].sessionId


def test_a_session_can_be_looked_up_by_its_deterministic_id() -> None:
    journal = fixtures.journal()
    journal.settle(fixtures.settlement(100, settled_at=fixtures.at(10)))
    assert journal.engine.current is not None
    identity = journal.engine.current.sessionId
    journal.tick(fixtures.at(9, day=12))
    found = journal.engine.find(identity)
    assert found is not None and found.realizedPnl == 100
    assert journal.engine.find(fixtures.trade_id("nothing")) is None


# --- T-AN the durable record -----------------------------------------------------------


def test_sessions_and_events_survive_parquet_with_every_field_intact(tmp_path: Path) -> None:
    storage = ParquetStorage(tmp_path, batch_size=1)
    journal = fixtures.journal(dailyProfitTarget=600)
    journal.settle(fixtures.settlement(620))
    journal.tick(fixtures.at(12, 1))
    for session in journal.sessions:
        storage.append("daily_sessions", session)
    for event in journal.events:
        storage.append("session_guard_events", event)
    storage.flush()

    sessions = [row for row in storage.reload("daily_sessions") if isinstance(row, DailySession)]
    events = [
        row for row in storage.reload("session_guard_events") if isinstance(row, SessionGuardEvent)
    ]
    assert len(sessions) == len(journal.sessions)
    latest = max(sessions, key=lambda row: row.revision)
    original = max(journal.sessions, key=lambda row: row.revision)
    assert latest.model_dump(mode="json") == original.model_dump(mode="json")
    assert {event.type for event in events} >= {
        "SESSION_CREATED",
        "SETTLEMENT_APPLIED",
        "PROFIT_TARGET_REACHED",
        "SESSION_COMPLETED",
        "SESSION_LOCKED",
    }


def test_a_day_rebuilt_from_parquet_keeps_its_lock(tmp_path: Path) -> None:
    storage = ParquetStorage(tmp_path, batch_size=4)
    journal = fixtures.journal(dailyLossLimit=300)
    journal.settle(fixtures.settlement(-330))
    journal.tick(fixtures.at(12, 1))
    for session in journal.sessions:
        storage.append("daily_sessions", session)
    for event in journal.events:
        storage.append("session_guard_events", event)
    storage.flush()

    rebuilt = SessionGuard(journal.engine.settings)
    rebuilt.restore(
        [row for row in storage.reload("daily_sessions") if isinstance(row, DailySession)],
        [
            row
            for row in storage.reload("session_guard_events")
            if isinstance(row, SessionGuardEvent)
        ],
        fixtures.at(16),
    )
    assert rebuilt.current is not None
    assert rebuilt.current.status == "LOCKED_FOR_DAY"
    assert rebuilt.current.realizedPnl == -330
    assert rebuilt.current.canOpenNewEntry is False


# --- T-AR, T-DI the real Phase 9 hand-off ----------------------------------------------


def test_real_paper_settlements_reach_the_daily_total_through_the_market_engine(
    tmp_path: Path,
) -> None:
    """The whole chain: broker-shaped observations in, a daily realized P/L out.

    Nothing is injected. Phase 5 builds the series, Phase 6-8 decide, Phase 9 resolves the
    outcomes, and the guard accounts exactly the settlements Phase 9 emitted.
    """
    engine = MarketEngine(
        ParquetStorage(tmp_path),
        MONEY,
        SessionGuardSettings(enabled=True, dailyProfitTarget=100),
    )
    rows = []
    for second in range(pipeline.SECONDS):
        rows.append(
            pipeline.observation(pipeline.LEADER, BASE_MS + second * 1000, pipeline.rising(second))
        )
        rows.append(
            pipeline.observation(
                pipeline.LAGGARD,
                BASE_MS + second * 1000 + pipeline.LAG_MS,
                pipeline.choppy(second),
            )
        )
    from quant_engine.market_api import ObservationBatch

    for start in range(0, len(rows), 18):
        engine.ingest(ObservationBatch(observations=rows[start : start + 18]), BASE_MS)

    resolved = [t for t in engine.paper.history if t.status == "RESOLVED"]
    assert resolved, "the chain must actually settle something"
    session = engine.guard.current
    assert session is not None
    assert session.accountingSource == "PAPER"
    assert session.wins == len(resolved)
    assert session.realizedPnl == 41.0 * len(resolved)
    assert session.monetaryTrades == len(resolved)
    # Every Phase 9 settlement was accounted exactly once.
    assert session.duplicateSettlements == 0
    assert session.currencyMismatches == 0
    # The target was passed long before the run ended, and the day stayed stopped.
    assert session.status in (
        "TARGET_REACHED",
        "COMPLETED",
        "LOCKED_FOR_DAY",
        "WAITING_FOR_SETTLEMENT",
    )
    assert session.canOpenNewEntry is False
    assert session.stopReason == "DAILY_PROFIT_TARGET"
    assert session.blockReason in ("DAILY_PROFIT_TARGET", "LOCKED_FOR_DAY")


def test_the_market_engine_restores_the_guard_from_its_own_durable_record(
    tmp_path: Path,
) -> None:
    first = MarketEngine(
        ParquetStorage(tmp_path),
        MONEY,
        SessionGuardSettings(enabled=True, dailyProfitTarget=100),
    )
    from quant_engine.market_api import ObservationBatch

    rows = []
    for second in range(pipeline.SECONDS):
        rows.append(
            pipeline.observation(pipeline.LEADER, BASE_MS + second * 1000, pipeline.rising(second))
        )
        rows.append(
            pipeline.observation(
                pipeline.LAGGARD,
                BASE_MS + second * 1000 + pipeline.LAG_MS,
                pipeline.choppy(second),
            )
        )
    for start in range(0, len(rows), 18):
        first.ingest(ObservationBatch(observations=rows[start : start + 18]), BASE_MS)
    first.storage.flush()
    before = first.guard.current
    assert before is not None

    second_engine = MarketEngine(
        ParquetStorage(tmp_path),
        MONEY,
        SessionGuardSettings(enabled=True, dailyProfitTarget=100),
    )
    restored = second_engine.restore_session_guard(before.startedAt + 3_600_000)
    assert restored > 0
    after = second_engine.guard.current
    assert after is not None
    assert after.sessionId == before.sessionId
    assert after.realizedPnl == before.realizedPnl
    assert after.wins == before.wins
    assert after.canOpenNewEntry is False
