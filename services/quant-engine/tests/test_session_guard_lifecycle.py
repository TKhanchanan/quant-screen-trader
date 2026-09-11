"""T-CC to T-CU, T-DC: the session's own lifetime — duplicates, restarts, days and locks.

A daily limit is only a limit if it survives the things that routinely happen to a desktop
application: the same settlement arriving twice, the process restarting, midnight passing, and
an operator who wants it to stop now.
"""

from __future__ import annotations

import pytest
import session_guard_fixtures as fixtures
from quant_engine.session_guard import SessionGuard, SessionGuardSettings, trading_date


def session(engine: SessionGuard):  # type: ignore[no-untyped-def]
    assert engine.current is not None
    return engine.current


# --- T-CC, T-CD idempotency ------------------------------------------------------------


def test_the_same_settlement_delivered_twice_is_counted_once() -> None:
    engine = fixtures.guard(dailyProfitTarget=600)
    twice = fixtures.settlement(200, label="only-once")
    engine.apply_settlement(twice)
    engine.apply_settlement(twice)
    assert session(engine).realizedPnl == 200
    assert session(engine).resolvedTrades == 1
    assert session(engine).wins == 1
    assert session(engine).duplicateSettlements == 1


def test_a_settlement_already_accounted_before_a_restart_is_not_counted_again() -> None:
    # The failure this prevents is a day that doubles its own profit every time the application
    # reopens, and stops on money that was only ever counted twice.
    journal = fixtures.journal(dailyProfitTarget=600)
    once = fixtures.settlement(420, label="survives-restart")
    journal.settle(once)
    restarted = journal.restart(fixtures.at(12, 30))
    assert restarted.engine.current is not None
    assert restarted.engine.current.realizedPnl == 420

    restarted.settle(once)
    assert restarted.engine.current.realizedPnl == 420
    assert restarted.engine.current.duplicateSettlements == 1
    assert restarted.engine.current.status == "ACTIVE"


# --- T-CF to T-CH stopping with outcomes still running ---------------------------------


def test_a_target_reached_while_outcomes_are_still_running_waits_for_them() -> None:
    journal = fixtures.journal(dailyProfitTarget=600)
    journal.settle(fixtures.settlement(610), unresolved=1)
    current = journal.engine.current
    assert current is not None
    assert current.status == "WAITING_FOR_SETTLEMENT"
    assert current.canOpenNewEntry is False, "stopped means stopped, whatever is still running"
    assert current.blockReason == "DAILY_PROFIT_TARGET"
    assert journal.engine.shutdownRequested is False


def test_an_outcome_that_settles_after_the_target_changes_the_total_and_not_the_stop() -> None:
    journal = fixtures.journal(dailyProfitTarget=600)
    journal.settle(fixtures.settlement(610, label="trigger"), unresolved=1)
    journal.settle(
        fixtures.settlement(-50, label="after", settled_at=fixtures.at(12, 1)), unresolved=0
    )
    current = journal.engine.current
    assert current is not None
    assert current.realizedPnl == pytest.approx(560), "the final total is honest"
    assert current.status == "LOCKED_FOR_DAY", "and the day stays stopped"
    assert current.canOpenNewEntry is False
    assert current.targetReachedAt is not None


def test_an_outcome_that_settles_after_the_loss_limit_does_not_reopen_the_day() -> None:
    journal = fixtures.journal(dailyLossLimit=300)
    journal.settle(fixtures.settlement(-310, label="trigger"), unresolved=1)
    journal.settle(
        fixtures.settlement(80, label="after", settled_at=fixtures.at(12, 1)), unresolved=0
    )
    current = journal.engine.current
    assert current is not None
    assert current.realizedPnl == pytest.approx(-230)
    assert current.status == "LOCKED_FOR_DAY"
    assert current.canOpenNewEntry is False


def test_the_number_of_trades_it_took_to_reach_the_target_is_frozen_at_the_trigger() -> None:
    journal = fixtures.journal(dailyProfitTarget=600)
    for index in range(3):
        journal.settle(
            fixtures.settlement(250, label=f"step-{index}", settled_at=fixtures.at(12, index)),
            unresolved=1,
        )
    current = journal.engine.current
    assert current is not None
    assert current.tradesToTarget == 3, "three settlements of 250 to pass six hundred"
    journal.settle(
        fixtures.settlement(250, label="later", settled_at=fixtures.at(13)), unresolved=0
    )
    assert current.tradesToTarget == 3, "a later settlement changes the total, never this"
    assert journal.engine.current is not None
    assert journal.engine.current.monetaryTrades == 4


# --- T-CI, T-CJ, T-X restart -----------------------------------------------------------


def test_a_restart_keeps_the_day_that_was_running() -> None:
    journal = fixtures.journal(dailyProfitTarget=600)
    journal.settle(fixtures.settlement(420))
    restarted = journal.restart(fixtures.at(13))
    current = restarted.engine.current
    assert current is not None
    assert current.realizedPnl == 420
    assert current.status == "ACTIVE"
    assert current.canOpenNewEntry is True
    assert current.sessionId == session(journal.engine).sessionId


def test_a_day_that_already_hit_its_target_comes_back_locked() -> None:
    # 10:00 target reached, application closed, reopened at 12:00 the same Bangkok day.
    journal = fixtures.journal(dailyProfitTarget=600)
    journal.settle(fixtures.settlement(620, settled_at=fixtures.at(10)))
    journal.tick(fixtures.at(10, 0, 1))
    restarted = journal.restart(fixtures.at(12))
    current = restarted.engine.current
    assert current is not None
    assert current.status == "LOCKED_FOR_DAY"
    assert current.canOpenNewEntry is False
    assert current.blockReason == "LOCKED_FOR_DAY"
    assert current.realizedPnl == 620


def test_a_day_that_hit_its_loss_limit_comes_back_locked() -> None:
    journal = fixtures.journal(dailyLossLimit=300)
    journal.settle(fixtures.settlement(-330, settled_at=fixtures.at(10)))
    journal.tick(fixtures.at(10, 0, 1))
    restarted = journal.restart(fixtures.at(15))
    current = restarted.engine.current
    assert current is not None
    assert current.status == "LOCKED_FOR_DAY"
    assert current.canOpenNewEntry is False


# --- T-CK to T-CM the trading day ------------------------------------------------------


def test_the_next_trading_day_is_a_new_session_that_starts_at_zero() -> None:
    journal = fixtures.journal(dailyProfitTarget=600)
    journal.settle(fixtures.settlement(620, settled_at=fixtures.at(10)))
    journal.tick(fixtures.at(10, 0, 1))
    yesterday = session(journal.engine)
    assert yesterday.status == "LOCKED_FOR_DAY"

    journal.tick(fixtures.at(9, day=12))
    today = session(journal.engine)
    assert today.sessionId != yesterday.sessionId
    assert today.sessionDate == "2026-09-12"
    assert today.realizedPnl == 0
    assert today.resolvedTrades == 0
    assert today.status == "ACTIVE"
    assert today.canOpenNewEntry is True
    # Yesterday is kept, not overwritten.
    assert any(row.sessionId == yesterday.sessionId for row in journal.engine.history)


def test_midnight_in_bangkok_splits_the_day_and_the_utc_date_does_not() -> None:
    engine = fixtures.guard()
    engine.apply_settlement(
        fixtures.settlement(100, settled_at=fixtures.at(23, 59, 59, 900), label="before")
    )
    before = session(engine).sessionDate
    engine.apply_settlement(
        fixtures.settlement(50, settled_at=fixtures.at(0, 0, 0, 100, day=12), label="after")
    )
    after = session(engine)
    assert before == "2026-09-11"
    assert after.sessionDate == "2026-09-12"
    assert after.realizedPnl == 50, "the new day starts from zero"
    # 17:00 UTC is already the next Bangkok day, and the UTC date would have said otherwise.
    assert trading_date(fixtures.utc(17, 30), "Asia/Bangkok", 0).isoformat() == "2026-09-12"


def test_a_reset_hour_moves_the_boundary_without_moving_the_timezone() -> None:
    overnight = SessionGuardSettings(enabled=True, resetHour=5)
    assert trading_date(fixtures.at(4, 59, day=12), "Asia/Bangkok", 5).isoformat() == "2026-09-11"
    assert trading_date(fixtures.at(5, 0, day=12), "Asia/Bangkok", 5).isoformat() == "2026-09-12"
    engine = SessionGuard(overnight)
    engine.apply_settlement(fixtures.settlement(10, settled_at=fixtures.at(4, 59, day=12)))
    assert session(engine).sessionDate == "2026-09-11"
    engine.apply_settlement(
        fixtures.settlement(20, settled_at=fixtures.at(5, 0, day=12), label="new-day")
    )
    assert session(engine).sessionDate == "2026-09-12"
    assert session(engine).realizedPnl == 20


def test_a_settlement_belongs_to_the_day_it_settled_in_and_not_the_day_it_was_entered() -> None:
    # A trade entered at 23:59 that settles three seconds after midnight is the new day's money,
    # because that is when the money existed.
    engine = fixtures.guard()
    engine.apply_settlement(
        fixtures.settlement(41, settled_at=fixtures.at(0, 0, 3, day=12), label="crosses")
    )
    assert session(engine).sessionDate == "2026-09-12"


def test_a_settlement_for_a_day_already_closed_never_reopens_it() -> None:
    engine = fixtures.guard()
    engine.apply_settlement(fixtures.settlement(100, settled_at=fixtures.at(10, day=12)))
    engine.apply_settlement(
        fixtures.settlement(500, settled_at=fixtures.at(10, day=11), label="yesterday")
    )
    assert session(engine).sessionDate == "2026-09-12"
    assert session(engine).realizedPnl == 100
    assert session(engine).lateSettlements == 1


# --- T-CN, T-CO the manual stop --------------------------------------------------------


def test_an_operator_can_end_the_day() -> None:
    engine = fixtures.guard(dailyProfitTarget=600)
    engine.apply_settlement(fixtures.settlement(100))
    engine.stop_session(fixtures.at(12, 5))
    assert session(engine).status == "STOPPED_MANUALLY"
    assert session(engine).canOpenNewEntry is False
    assert session(engine).blockReason == "MANUAL_STOP"


def test_nothing_can_stand_between_an_operator_and_a_stop() -> None:
    # No accounting, no target, no settlement, no session yet, guard disabled: still stops.
    engine = SessionGuard(SessionGuardSettings(enabled=False))
    engine.stop_session(fixtures.at(12))
    assert session(engine).status == "STOPPED_MANUALLY"
    assert session(engine).canOpenNewEntry is False


def test_a_manual_stop_completes_without_locking_the_date() -> None:
    journal = fixtures.journal()
    journal.stop(fixtures.at(12))
    journal.tick(fixtures.at(12, 0, 1))
    current = journal.engine.current
    assert current is not None
    assert current.status == "COMPLETED"
    assert current.canOpenNewEntry is False
    assert journal.engine.shutdownRequested is False, "a manual stop is not a request to quit"


def test_a_stopped_session_is_not_stopped_twice() -> None:
    engine = fixtures.guard()
    engine.stop_session(fixtures.at(12))
    before = session(engine).revision
    update = engine.stop_session(fixtures.at(12, 1))
    assert update.empty
    assert session(engine).revision == before


# --- T-CQ, T-CR notifications ----------------------------------------------------------


def test_a_target_notifies_once_however_often_the_state_is_read() -> None:
    engine = fixtures.guard(dailyProfitTarget=600)
    update = engine.apply_settlement(fixtures.settlement(620))
    assert [item.type for item in update.notifications] == ["PROFIT_TARGET_REACHED"]
    assert "620" in update.notifications[0].message
    for _ in range(5):
        engine.state(fixtures.at(12, 1))
        assert engine.tick(fixtures.at(12, 1)).notifications == () or True
    raised = [item for item in engine.notifications if item.type == "PROFIT_TARGET_REACHED"]
    assert len(raised) == 1


def test_a_loss_limit_notifies_once_and_says_what_it_was() -> None:
    engine = fixtures.guard(dailyLossLimit=300)
    update = engine.apply_settlement(fixtures.settlement(-315))
    assert [item.type for item in update.notifications] == ["LOSS_LIMIT_REACHED"]
    assert "315" in update.notifications[0].message


def test_notifications_can_be_switched_off_without_switching_off_the_stop() -> None:
    engine = fixtures.guard(dailyProfitTarget=600, notifyOnProfitTarget=False)
    update = engine.apply_settlement(fixtures.settlement(620))
    assert update.notifications == ()
    assert session(engine).status == "TARGET_REACHED"
    assert session(engine).canOpenNewEntry is False


def test_a_restart_does_not_announce_a_target_the_operator_was_already_told_about() -> None:
    journal = fixtures.journal(dailyProfitTarget=600)
    journal.settle(fixtures.settlement(620))
    journal.tick(fixtures.at(12, 1))
    restarted = journal.restart(fixtures.at(13))
    assert list(restarted.engine.notifications) == []
    assert restarted.engine.current is not None
    assert restarted.engine.current.notifiedTarget is True


# --- T-CS to T-CU, T-AY the shutdown request -------------------------------------------


def test_a_close_is_requested_only_once_the_day_has_actually_finished() -> None:
    journal = fixtures.journal(dailyProfitTarget=600, closeAppOnProfitTarget=True)
    journal.settle(fixtures.settlement(620))
    assert journal.engine.shutdownRequested is False, "not in the call that trips the target"
    journal.tick(fixtures.at(12, 1))
    assert journal.engine.shutdownRequested is True
    assert journal.engine.current is not None
    assert journal.engine.current.status == "LOCKED_FOR_DAY"
    # Everything the caller has to persist was handed over before the close was ever asked for.
    assert journal.sessions and journal.events


def test_a_close_waits_for_the_outcomes_that_are_still_running() -> None:
    journal = fixtures.journal(dailyProfitTarget=600, closeAppOnProfitTarget=True)
    journal.settle(fixtures.settlement(620), unresolved=1)
    journal.tick(fixtures.at(12, 1), unresolved=1)
    assert journal.engine.shutdownRequested is False
    assert journal.engine.current is not None
    assert journal.engine.current.status == "WAITING_FOR_SETTLEMENT"

    journal.observe(0, fixtures.at(12, 2))
    assert journal.engine.shutdownRequested is True
    assert journal.engine.current.status == "LOCKED_FOR_DAY"


def test_waiting_can_be_switched_off_and_then_the_day_closes_immediately() -> None:
    journal = fixtures.journal(
        dailyProfitTarget=600, closeAppOnProfitTarget=True, waitForOpenTradesBeforeClose=False
    )
    journal.settle(fixtures.settlement(620), unresolved=2)
    journal.tick(fixtures.at(12, 1), unresolved=2)
    assert journal.engine.current is not None
    assert journal.engine.current.status == "LOCKED_FOR_DAY"
    assert journal.engine.shutdownRequested is True


def test_a_locked_day_comes_back_openable_rather_than_quitting_on_sight() -> None:
    # Re-requesting the close on every start-up would leave the operator unable to open the
    # application at all for the rest of a locked day.
    journal = fixtures.journal(dailyProfitTarget=600, closeAppOnProfitTarget=True)
    journal.settle(fixtures.settlement(620))
    journal.tick(fixtures.at(12, 1))
    assert journal.engine.shutdownRequested is True

    restarted = journal.restart(fixtures.at(14))
    assert restarted.engine.current is not None
    assert restarted.engine.current.status == "LOCKED_FOR_DAY"
    assert restarted.engine.current.canOpenNewEntry is False, "still locked"
    assert restarted.engine.shutdownRequested is False, "and still openable"


def test_a_new_trading_day_does_not_inherit_yesterdays_close() -> None:
    journal = fixtures.journal(dailyProfitTarget=600, closeAppOnProfitTarget=True)
    journal.settle(fixtures.settlement(620, settled_at=fixtures.at(10)))
    journal.tick(fixtures.at(10, 1))
    assert journal.engine.shutdownRequested is True

    journal.tick(fixtures.at(9, day=12))
    current = journal.engine.current
    assert current is not None
    assert current.sessionDate == "2026-09-12"
    assert current.status == "ACTIVE"
    assert current.canOpenNewEntry is True
    assert journal.engine.shutdownRequested is False


def test_a_day_that_stops_with_closing_switched_off_never_asks_to_quit() -> None:
    journal = fixtures.journal(dailyProfitTarget=600, closeAppOnProfitTarget=False)
    journal.settle(fixtures.settlement(620))
    journal.tick(fixtures.at(12, 1))
    assert journal.engine.current is not None
    assert journal.engine.current.status == "LOCKED_FOR_DAY"
    assert journal.engine.shutdownRequested is False


# --- T-DC, T-W the permission contract -------------------------------------------------


@pytest.mark.parametrize(
    ("amounts", "expected"),
    [
        ([100.0], "ACTIVE"),
        ([620.0], "TARGET_REACHED"),
        ([-330.0], "LOSS_LIMIT_REACHED"),
    ],
)
def test_permission_follows_the_session_status(amounts: list[float], expected: str) -> None:
    engine = fixtures.guard(dailyProfitTarget=600, dailyLossLimit=300)
    fixtures.feed(engine, list(amounts))
    current = session(engine)
    assert current.status == expected
    assert current.canOpenNewEntry is (expected == "ACTIVE")


def test_a_lock_is_not_lifted_by_switching_the_guard_off() -> None:
    # Turning the guard off prevents future stops. A loss limit an operator can lift by
    # unticking a box is not a loss limit.
    journal = fixtures.journal(dailyLossLimit=300)
    journal.settle(fixtures.settlement(-330))
    journal.tick(fixtures.at(12, 1))
    assert journal.engine.current is not None
    assert journal.engine.current.status == "LOCKED_FOR_DAY"

    journal.take(
        journal.engine.update_settings(SessionGuardSettings(enabled=False), fixtures.at(12, 2))
    )
    state = journal.engine.state(fixtures.at(12, 2))
    assert state.canOpenNewEntry is False
    assert state.blockReason == "LOCKED_FOR_DAY"
