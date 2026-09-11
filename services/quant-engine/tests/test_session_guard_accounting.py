"""T-BW to T-CB, T-CE, T-CP, T-DD: what a day's realized total is, and what it refuses to be.

The stop rules are only as good as the number they read, so these are the tests that keep that
number honest: money only when Phase 9 actually measured some, in this session's own currency,
counted once, and never inferred from the fact that a trade won.
"""

from __future__ import annotations

import pytest
import session_guard_fixtures as fixtures
from quant_engine.session_guard import SessionGuard, summarize


def realized(engine: SessionGuard) -> float:
    assert engine.current is not None
    return engine.current.realizedPnl


def status(engine: SessionGuard) -> str:
    assert engine.current is not None
    return engine.current.status


# --- T-BW to T-BY the profit target ----------------------------------------------------


def test_a_day_that_passes_its_profit_target_stops_accepting_entries() -> None:
    engine = fixtures.guard(dailyProfitTarget=600)
    fixtures.feed(engine, [200, 150, 270])
    assert realized(engine) == 620
    assert status(engine) == "TARGET_REACHED"
    assert engine.current is not None and engine.current.canOpenNewEntry is False
    assert engine.current.blockReason == "DAILY_PROFIT_TARGET"
    assert engine.current.stopReason == "DAILY_PROFIT_TARGET"


def test_landing_exactly_on_the_target_reaches_it() -> None:
    engine = fixtures.guard(dailyProfitTarget=600)
    fixtures.feed(engine, [599, 1])
    assert realized(engine) == 600
    assert status(engine) == "TARGET_REACHED"


def test_overshooting_the_target_triggers_once_and_not_again() -> None:
    engine = fixtures.guard(dailyProfitTarget=600)
    fixtures.feed(engine, [590, 50, 30])
    assert realized(engine) == 670
    reached = [event for event in engine.eventLog if event.type == "PROFIT_TARGET_REACHED"]
    assert len(reached) == 1
    assert reached[0].amount == 640
    assert engine.current is not None and engine.current.targetReachedAt == fixtures.NOON + 60_000


def test_a_target_is_not_reached_before_it_is_reached() -> None:
    engine = fixtures.guard(dailyProfitTarget=600)
    fixtures.feed(engine, [200, 150])
    assert status(engine) == "ACTIVE"
    assert engine.current is not None and engine.current.canOpenNewEntry is True


# --- T-BZ the loss limit ---------------------------------------------------------------


def test_a_day_that_passes_its_loss_limit_stops_accepting_entries() -> None:
    engine = fixtures.guard(dailyLossLimit=300)
    fixtures.feed(engine, [-100, -80, -130])
    assert realized(engine) == -310
    assert status(engine) == "LOSS_LIMIT_REACHED"
    assert engine.current is not None and engine.current.canOpenNewEntry is False
    assert engine.current.blockReason == "DAILY_LOSS_LIMIT"


def test_the_loss_limit_is_a_magnitude_compared_against_a_negative_total() -> None:
    engine = fixtures.guard(dailyLossLimit=300)
    fixtures.feed(engine, [-280, -50])
    assert realized(engine) == -330
    assert status(engine) == "LOSS_LIMIT_REACHED"
    assert engine.current is not None and engine.current.lossLimit == 300


def test_a_profitable_day_never_trips_the_loss_limit() -> None:
    engine = fixtures.guard(dailyLossLimit=300, dailyProfitTarget=10_000)
    fixtures.feed(engine, [500, -100, 200])
    assert status(engine) == "ACTIVE"


# --- T-CA, T-CB what money is not ------------------------------------------------------


def test_a_draw_moves_the_tally_and_not_the_money() -> None:
    engine = fixtures.guard(dailyProfitTarget=600)
    fixtures.feed(engine, [200, 0])
    assert realized(engine) == 200
    assert engine.current is not None
    assert (engine.current.wins, engine.current.draws) == (1, 1)
    assert engine.current.monetaryTrades == 2


def test_a_win_with_no_measured_money_counts_as_a_win_and_adds_nothing() -> None:
    # Phase 9 resolved a direction without an accounting snapshot. The outcome is real; the
    # money is unknown, and unknown is never zero.
    engine = fixtures.guard(dailyProfitTarget=600)
    engine.apply_settlement(fixtures.settlement(500, settled_at=fixtures.NOON))
    engine.apply_settlement(fixtures.settlement(None, outcome="WIN", settled_at=fixtures.at(12, 1)))
    assert realized(engine) == 500
    assert engine.current is not None
    assert engine.current.wins == 2
    assert engine.current.resolvedTrades == 2
    assert engine.current.monetaryTrades == 1
    assert engine.current.nonMonetarySettlements == 1
    assert status(engine) == "ACTIVE"


# --- T-CE currency isolation -----------------------------------------------------------


def test_a_settlement_in_another_currency_is_never_added_to_the_total() -> None:
    engine = fixtures.guard(dailyProfitTarget=600, currency="THB")
    engine.apply_settlement(fixtures.settlement(500, settled_at=fixtures.NOON))
    engine.apply_settlement(fixtures.settlement(500, currency="USD", settled_at=fixtures.at(12, 1)))
    # No rate was configured, so no conversion is possible and none is invented.
    assert realized(engine) == 500
    assert engine.current is not None
    assert engine.current.currencyMismatches == 1
    assert engine.current.wins == 2, "the outcome still happened"
    assert status(engine) == "ACTIVE"
    rejected = [event for event in engine.eventLog if event.reason == "CURRENCY_MISMATCH"]
    assert len(rejected) == 1


# --- T-CP accounting integrity ---------------------------------------------------------


def test_a_non_finite_value_is_refused_and_fails_the_session_closed() -> None:
    engine = fixtures.guard(dailyProfitTarget=600)
    fixtures.feed(engine, [200])
    broken = fixtures.settlement(1.0, settled_at=fixtures.at(12, 1)).model_construct(
        **{
            **fixtures.settlement(1.0, settled_at=fixtures.at(12, 1)).model_dump(),
            "realizedPnl": float("nan"),
        }
    )
    engine.apply_settlement(broken)
    assert realized(engine) == 200, "a value that is not a number changes nothing"
    assert status(engine) == "ACCOUNTING_ERROR"
    assert engine.current is not None and engine.current.canOpenNewEntry is False
    assert engine.current.blockReason == "ACCOUNTING_ERROR"
    assert engine.current.rejectedSettlements == 1


def test_a_settlement_from_an_unsupported_paper_contract_is_refused() -> None:
    engine = fixtures.guard(dailyProfitTarget=600)
    engine.apply_settlement(fixtures.settlement(700, version="qst-paper-v2"))
    assert realized(engine) == 0
    assert status(engine) == "ACTIVE"
    assert engine.current is not None and engine.current.rejectedSettlements == 1


# --- T-AF to T-AK the descriptive statistics -------------------------------------------


def test_wins_losses_and_draws_are_counted_separately_from_the_money() -> None:
    engine = fixtures.guard()
    for index, (amount, outcome) in enumerate(
        [(41.0, "WIN"), (-50.0, "LOSS"), (0.0, "DRAW"), (41.0, "WIN"), (None, "WIN")]
    ):
        engine.apply_settlement(
            fixtures.settlement(amount, outcome=outcome, settled_at=fixtures.NOON + index * 60_000)
        )
    session = engine.current
    assert session is not None
    assert (session.wins, session.losses, session.draws) == (3, 1, 1)
    assert session.resolvedTrades == 5
    assert session.monetaryTrades == 4
    assert session.realizedPnl == pytest.approx(32)
    assert session.grossProfit == pytest.approx(82)
    assert session.grossLoss == pytest.approx(50)


def test_win_rates_are_none_rather_than_zero_when_there_is_nothing_to_divide() -> None:
    engine = fixtures.guard()
    engine.tick(fixtures.NOON)
    assert engine.current is not None
    empty = summarize(engine.current)
    assert empty.winRateExcludingDraws is None
    assert empty.winRateIncludingDraws is None
    fixtures.feed(engine, [41, -50, 0])
    filled = summarize(engine.current)
    assert filled.winRateExcludingDraws == pytest.approx(0.5)
    assert filled.winRateIncludingDraws == pytest.approx(1 / 3)


def test_the_largest_win_and_loss_are_tracked_on_the_same_axis_as_the_total() -> None:
    engine = fixtures.guard()
    fixtures.feed(engine, [41, 82, -50, -120])
    session = engine.current
    assert session is not None
    assert session.largestWin == pytest.approx(82)
    assert session.largestLoss == pytest.approx(-120)


def test_the_realized_equity_path_records_its_peak_trough_and_worst_fall() -> None:
    engine = fixtures.guard()
    fixtures.feed(engine, [200, 150, -250])
    session = engine.current
    assert session is not None
    assert session.realizedPnl == pytest.approx(100)
    assert session.peakRealizedPnl == pytest.approx(350)
    assert session.maxRealizedDrawdown == pytest.approx(250)


def test_a_losing_day_records_its_trough_and_no_drawdown_from_a_peak_it_never_had() -> None:
    engine = fixtures.guard()
    fixtures.feed(engine, [-100, -50])
    session = engine.current
    assert session is not None
    assert session.troughRealizedPnl == pytest.approx(-150)
    assert session.peakRealizedPnl == 0.0
    assert session.maxRealizedDrawdown == pytest.approx(150)
