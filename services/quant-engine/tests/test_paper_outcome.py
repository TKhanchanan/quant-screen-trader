"""T-AR to T-AV, T-U, T-V, T-W: what a paper trade concludes, and what it refuses to claim.

The outcome rules are the whole point of Phase 9, so they are asserted both as pure functions
and through the engine's real lifecycle — a correct comparison wired to the wrong price would
pass the first and fail the second.
"""

from __future__ import annotations

import paper_fixtures as fixtures
import pytest
from quant_engine.paper import (
    PAPER_VERSION,
    PaperEngine,
    PaperOutcome,
    PaperSettings,
    outcome_for,
    price_delta_bps,
    realized_pnl,
)
from quant_engine.paper.models import PaperDirection


def resolve(
    direction: PaperDirection,
    entry: float,
    expiry: float,
    *,
    settings: PaperSettings | None = None,
) -> PaperEngine:
    """Drive one whole trade: selection, post-decision entry, horizon, expiry."""
    engine = PaperEngine(settings if settings is not None else PaperSettings())
    board = fixtures.board(direction=direction)
    available = fixtures.decision_time(board)
    engine.on_board(board, available)
    engine.on_market_sample(fixtures.sample(available, entry))
    engine.on_market_sample(fixtures.sample(available + 5_000, expiry))
    return engine


def only(engine: PaperEngine) -> PaperOutcome:
    assert len(engine.history) == 1
    return engine.history[0].outcome


# --- T-AR to T-AV the five outcomes ----------------------------------------------------


def test_up_that_rises_is_a_win() -> None:
    assert only(resolve("UP", 100, 101)) == "WIN"


def test_up_that_falls_is_a_loss() -> None:
    assert only(resolve("UP", 100, 99)) == "LOSS"


def test_down_that_falls_is_a_win() -> None:
    assert only(resolve("DOWN", 100, 99)) == "WIN"


def test_down_that_rises_is_a_loss() -> None:
    assert only(resolve("DOWN", 100, 101)) == "LOSS"


@pytest.mark.parametrize("direction", ["UP", "DOWN"])
def test_an_unchanged_price_is_a_draw_in_either_direction(direction: PaperDirection) -> None:
    # No tolerance band. The project has no canonical tick-size contract, so any band would be
    # an invented number that quietly reclassified real losses as draws.
    assert only(resolve(direction, 100, 100)) == "DRAW"


@pytest.mark.parametrize(
    ("direction", "entry", "expiry", "expected"),
    [
        ("UP", 100.0, 100.000001, "WIN"),
        ("UP", 100.0, 99.999999, "LOSS"),
        ("DOWN", 100.0, 99.999999, "WIN"),
        ("DOWN", 100.0, 100.000001, "LOSS"),
    ],
)
def test_the_smallest_representable_move_still_decides_the_outcome(
    direction: PaperDirection, entry: float, expiry: float, expected: PaperOutcome
) -> None:
    assert outcome_for(direction, entry, expiry) == expected


def test_the_outcome_rule_is_symmetric_between_the_two_directions() -> None:
    mirrored = {"WIN": "LOSS", "LOSS": "WIN", "DRAW": "DRAW"}
    for entry, expiry in ((100.0, 101.0), (100.0, 99.0), (100.0, 100.0)):
        up = outcome_for("UP", entry, expiry)
        assert outcome_for("DOWN", entry, expiry) == mirrored[up]


# --- T-U movement diagnostics ----------------------------------------------------------


def test_price_movement_is_reported_in_basis_points_and_is_not_profit() -> None:
    engine = resolve("UP", 1.08420, 1.08451)
    trade = engine.history[0]
    assert trade.priceDelta == pytest.approx(0.00031)
    assert trade.priceDeltaBps == pytest.approx(2.859, abs=0.01)
    # Movement is descriptive. Without accounting settings there is no monetary result at all.
    assert trade.realizedPaperPnl is None


def test_basis_points_are_signed_and_refuse_a_zero_entry_price() -> None:
    assert price_delta_bps(100, 99) == pytest.approx(-100.0)
    assert price_delta_bps(0, 99) is None


# --- T-BR to T-BU paper accounting -----------------------------------------------------


def accounting(stake: float = 50, payout: float = 0.82, currency: str = "THB") -> PaperSettings:
    return PaperSettings(paperCurrency=currency, paperStake=stake, paperPayoutRate=payout)


def test_a_win_returns_the_configured_net_profit_fraction_of_the_stake() -> None:
    trade = resolve("UP", 100, 101, settings=accounting()).history[0]
    assert trade.outcome == "WIN"
    assert trade.realizedPaperPnl == pytest.approx(41.0)
    assert (trade.paperCurrency, trade.paperStake, trade.paperPayoutRate) == ("THB", 50.0, 0.82)


def test_a_loss_returns_the_whole_stake_and_not_a_payout_adjusted_fraction() -> None:
    trade = resolve("UP", 100, 99, settings=accounting()).history[0]
    assert trade.outcome == "LOSS"
    assert trade.realizedPaperPnl == pytest.approx(-50.0)


def test_a_draw_returns_the_stake_so_the_result_is_exactly_zero() -> None:
    trade = resolve("UP", 100, 100, settings=accounting()).history[0]
    assert trade.outcome == "DRAW"
    assert trade.realizedPaperPnl == 0.0


def test_without_accounting_settings_the_direction_still_resolves_and_no_money_is_invented() -> (
    None
):
    trade = resolve("UP", 100, 101).history[0]
    assert trade.outcome == "WIN"
    # None means "not known". It is never collapsed to 0.0, which would read as break-even.
    assert trade.realizedPaperPnl is None
    assert trade.paperStake is None and trade.paperPayoutRate is None


def test_an_unresolved_outcome_has_no_monetary_result_even_with_a_stake_configured() -> None:
    for outcome in ("INVALID", "UNRESOLVED"):
        assert realized_pnl(outcome, stake=50, payout_rate=0.82) is None


def test_accounting_settings_must_be_complete_or_absent() -> None:
    # A half-configured block cannot produce an honest number, so it is refused outright
    # rather than silently ignoring the fields that were set.
    with pytest.raises(ValueError, match="together"):
        PaperSettings(paperStake=50, paperPayoutRate=None, paperCurrency="THB")
    assert PaperSettings().accountingConfigured is False
    assert accounting().accountingConfigured is True


# --- T-BV the accounting snapshot ------------------------------------------------------


def test_changing_the_settings_cannot_rewrite_a_trade_that_is_already_open() -> None:
    engine = PaperEngine(accounting(stake=50, payout=0.80))
    board = fixtures.board(direction="UP")
    available = fixtures.decision_time(board)
    engine.on_board(board, available)
    engine.on_market_sample(fixtures.sample(available, 100))
    assert engine.live[("capitalbear", 1)].paperStake == 50.0

    engine.settings = accounting(stake=100, payout=0.90)
    engine.on_market_sample(fixtures.sample(available + 5_000, 101))

    trade = engine.history[0]
    assert (trade.paperStake, trade.paperPayoutRate) == (50.0, 0.80)
    assert trade.realizedPaperPnl == pytest.approx(40.0)


# --- T-AL the Phase 9.5 hand-off -------------------------------------------------------


def test_a_resolved_trade_emits_exactly_one_settlement() -> None:
    engine = resolve("UP", 100, 101, settings=accounting())
    trade = engine.history[0]
    assert len(engine.settlements) == 1
    settlement = engine.settlements[0]
    assert settlement.tradeId == trade.paperTradeId
    assert settlement.outcome == "WIN"
    assert settlement.realizedPnl == pytest.approx(41.0)
    assert settlement.currency == "THB"
    assert settlement.paperVersion == PAPER_VERSION
    # T-BX: re-offering the same resolved trade cannot produce a second settlement, which a
    # daily session guard would otherwise count twice.
    assert engine._settle_once(trade) is None
    assert len(engine.settlements) == 1


def test_a_settlement_without_accounting_carries_the_outcome_and_no_money() -> None:
    engine = resolve("DOWN", 100, 99)
    settlement = engine.settlements[0]
    assert settlement.outcome == "WIN"
    assert settlement.currency is None
    assert settlement.stake is None
    assert settlement.realizedPnl is None
