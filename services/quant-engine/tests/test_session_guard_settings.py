"""T-E, T-CV to T-CY: what an operator may configure, and what happens the moment they do.

Every value here decides when trading stops, so none of them is quietly corrected. A target of
zero is not a target, a negative limit is not a limit, and an unknown timezone would silently
move the boundary of the trading day — each is refused with its reason instead.
"""

from __future__ import annotations

import pytest
import session_guard_fixtures as fixtures
from quant_engine.session_guard import SessionGuard, SessionGuardSettings


def status(engine: SessionGuard) -> str:
    """Read through a plain string: the session is replaced between assertions, and a narrowed
    literal from the first one would make the second look impossible."""
    assert engine.current is not None
    return engine.current.status


# --- T-E validation --------------------------------------------------------------------


def test_the_defaults_enforce_nothing_and_invent_no_amounts() -> None:
    settings = SessionGuardSettings()
    assert settings.enabled is False
    assert settings.dailyProfitTarget is None
    assert settings.dailyLossLimit is None
    assert settings.currency == "THB"
    assert settings.timezone == "Asia/Bangkok"
    assert settings.resetHour == 0
    assert settings.enforcing is False


@pytest.mark.parametrize("value", [0, -1, -0.01])
def test_a_profit_target_that_is_not_positive_is_refused(value: float) -> None:
    with pytest.raises(ValueError):
        SessionGuardSettings(dailyProfitTarget=value)


@pytest.mark.parametrize("value", [0, -1, -300])
def test_a_loss_limit_that_is_not_positive_is_refused(value: float) -> None:
    with pytest.raises(ValueError):
        SessionGuardSettings(dailyLossLimit=value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_an_amount_that_is_not_a_number_is_refused(value: float) -> None:
    with pytest.raises(ValueError):
        SessionGuardSettings(dailyProfitTarget=value)
    with pytest.raises(ValueError):
        SessionGuardSettings(dailyLossLimit=value)


@pytest.mark.parametrize("value", [-1, 24, 99])
def test_a_reset_hour_outside_the_clock_is_refused(value: int) -> None:
    with pytest.raises(ValueError):
        SessionGuardSettings(resetHour=value)


def test_an_unknown_timezone_is_refused_rather_than_falling_back_to_the_machine() -> None:
    # Falling back to the operating system's timezone would move the trading day boundary
    # silently, and a daily total split in the wrong place is not a daily total.
    with pytest.raises(ValueError, match="timezone"):
        SessionGuardSettings(timezone="Mars/Olympus")
    with pytest.raises(ValueError, match="timezone"):
        SessionGuardSettings(timezone="not a zone")
    assert SessionGuardSettings(timezone="America/New_York").timezone == "America/New_York"


def test_a_guard_with_no_limits_accounts_the_day_and_refuses_nothing() -> None:
    settings = SessionGuardSettings(enabled=True)
    assert settings.enabled is True
    assert settings.enforcing is False


# --- T-CV the disabled guard -----------------------------------------------------------


def test_a_disabled_guard_accounts_the_day_and_enforces_nothing() -> None:
    engine = fixtures.guard(enabled=False, dailyProfitTarget=600, dailyLossLimit=300)
    fixtures.feed(engine, [500, 500])
    session = engine.current
    assert session is not None
    assert session.realizedPnl == 1000, "the day is still counted"
    assert status(engine) == "DISABLED"
    assert session.canOpenNewEntry is True, "and nothing is refused"
    state = engine.state(fixtures.at(12, 5))
    assert state.canOpenNewEntry is True
    assert state.blockReason == "GUARD_DISABLED"


# --- T-CW, T-CX, T-CY changing the limits mid-day --------------------------------------


def test_lowering_the_target_below_the_days_profit_stops_the_session_at_once() -> None:
    # The alternative would let an operator tighten a limit and keep trading past it until the
    # next settlement happened to arrive.
    engine = fixtures.guard(dailyProfitTarget=600)
    fixtures.feed(engine, [500])
    assert status(engine) == "ACTIVE"

    engine.update_settings(fixtures.settings(dailyProfitTarget=400), fixtures.at(12, 5))
    session = engine.current
    assert session is not None
    assert status(engine) == "TARGET_REACHED"
    assert session.canOpenNewEntry is False
    assert session.profitTarget == 400


def test_tightening_the_loss_limit_below_the_days_loss_stops_the_session_at_once() -> None:
    engine = fixtures.guard(dailyLossLimit=300)
    fixtures.feed(engine, [-250])
    assert status(engine) == "ACTIVE"

    engine.update_settings(fixtures.settings(dailyLossLimit=200), fixtures.at(12, 5))
    session = engine.current
    assert session is not None
    assert status(engine) == "LOSS_LIMIT_REACHED"
    assert session.canOpenNewEntry is False


def test_removing_the_target_leaves_the_loss_guard_watching() -> None:
    engine = fixtures.guard(dailyProfitTarget=600, dailyLossLimit=300)
    fixtures.feed(engine, [700])
    assert status(engine) == "TARGET_REACHED"

    engine = fixtures.guard(dailyProfitTarget=600, dailyLossLimit=300)
    fixtures.feed(engine, [500])
    engine.update_settings(
        fixtures.settings(dailyProfitTarget=None, dailyLossLimit=300), fixtures.at(12, 5)
    )
    session = engine.current
    assert session is not None
    assert status(engine) == "ACTIVE"
    assert session.profitTarget is None
    assert session.lossLimit == 300

    engine.apply_settlement(fixtures.settlement(-900, settled_at=fixtures.at(12, 6)))
    assert status(engine) == "LOSS_LIMIT_REACHED"


def test_enabling_the_guard_mid_day_measures_the_money_already_realized() -> None:
    engine = fixtures.guard(enabled=False, dailyProfitTarget=600)
    fixtures.feed(engine, [700])
    assert status(engine) == "DISABLED"

    engine.update_settings(
        fixtures.settings(enabled=True, dailyProfitTarget=600), fixtures.at(12, 5)
    )
    assert engine.current is not None
    assert status(engine) == "TARGET_REACHED"
    assert engine.current.canOpenNewEntry is False


def test_changing_the_currency_starts_a_separate_session_rather_than_retrofitting_one() -> None:
    # Two currencies in one total would be a number with no meaning, so the day under the old
    # terms keeps its own record instead of being rewritten under the new ones.
    engine = fixtures.guard(currency="THB")
    fixtures.feed(engine, [500])
    first = engine.current
    assert first is not None

    engine.update_settings(fixtures.settings(currency="USD"), fixtures.at(12, 5))
    second = engine.current
    assert second is not None
    assert second.sessionId != first.sessionId
    assert second.currency == "USD"
    assert second.realizedPnl == 0
    assert any(row.sessionId == first.sessionId for row in engine.history)
