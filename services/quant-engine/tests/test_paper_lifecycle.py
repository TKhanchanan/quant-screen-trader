"""T-AW to T-BO: when a paper trade may enter, when it may resolve, and when it may not.

These are the tests that make Phase 9 evidence rather than storytelling. Every one of them
describes a way a measurement layer can quietly flatter itself — entering at a price that
existed before the decision did, resolving on the best price in a window instead of the first,
settling a EUR/USD trade with a BTC tick — and asserts that it does not happen.
"""

from __future__ import annotations

from uuid import UUID

import paper_fixtures as fixtures
import pytest
from features_fixtures import CONTEXT_A, CONTEXT_B
from quant_engine.configuration import Platform
from quant_engine.paper import (
    MAX_OPEN_PER_PLATFORM,
    PAPER_DURATION_MS,
    PaperEngine,
    PaperSettings,
)
from quant_engine.paper.models import PaperDirection


def start(
    *,
    platform: Platform = "capitalbear",
    direction: PaperDirection = "UP",
    slot: int = 1,
    settings: PaperSettings | None = None,
) -> tuple[PaperEngine, int]:
    """A paper intent waiting for its first post-decision price."""
    engine = PaperEngine(settings if settings is not None else PaperSettings())
    board = fixtures.board(platform=platform, slot=slot, direction=direction)
    available = fixtures.decision_time(board)
    update = engine.on_board(board, available)
    assert update.rejection is None
    return engine, available


# --- T-AW, T-AX, T-AY entry has no lookahead -------------------------------------------


def test_a_price_from_before_the_decision_can_never_become_the_entry() -> None:
    engine, available = start()
    engine.on_market_sample(fixtures.sample(available - 100, 100.5))
    trade = engine.live[("capitalbear", 1)]
    assert trade.status == "PENDING_ENTRY" and trade.entryPrice is None

    engine.on_market_sample(fixtures.sample(available + 100, 101.0))
    trade = engine.live[("capitalbear", 1)]
    assert trade.status == "OPEN"
    assert trade.entryPrice == 101.0


def test_the_board_close_time_is_not_the_entry_time_when_the_decision_came_later() -> None:
    # The critical one. The bar closes at asOf, but the ninth slot of the cohort is only
    # processed 1.2s later, so the price at asOf is one nobody could have acted on.
    engine = PaperEngine()
    board = fixtures.board(direction="UP")
    available = board.asOf + 1_200
    engine.on_board(board, available)

    engine.on_market_sample(fixtures.sample(board.asOf, 100.0))
    assert engine.live[("capitalbear", 1)].entryPrice is None

    engine.on_market_sample(fixtures.sample(board.asOf + 1_300, 102.0))
    trade = engine.live[("capitalbear", 1)]
    assert trade.decisionAvailableAt == available
    assert trade.boardAsOf == board.asOf
    assert trade.entryTime == board.asOf + 1_300
    assert trade.entryPrice == 102.0


def test_the_entry_is_the_first_eligible_price_and_never_a_better_later_one() -> None:
    engine, available = start()
    engine.on_market_sample(fixtures.sample(available + 20, 100.0))
    engine.on_market_sample(fixtures.sample(available + 70, 101.0))
    trade = engine.live[("capitalbear", 1)]
    assert trade.entryTime == available + 20
    assert trade.entryPrice == 100.0


def test_a_sample_exactly_at_the_decision_time_is_eligible() -> None:
    engine, available = start()
    engine.on_market_sample(fixtures.sample(available, 100.0))
    assert engine.live[("capitalbear", 1)].entryPrice == 100.0


def test_an_unusable_price_is_skipped_rather_than_coerced_to_anything() -> None:
    # T-Q: there is no fallback price, no last-known value and no zero.
    engine, available = start()
    engine.on_market_sample(fixtures.sample(available + 10, 99.0, state="INVALID"))
    engine.on_market_sample(fixtures.sample(available + 20, 98.0, state="STALE"))
    assert engine.live[("capitalbear", 1)].status == "PENDING_ENTRY"
    engine.on_market_sample(fixtures.sample(available + 30, 97.0, state="DEGRADED"))
    assert engine.live[("capitalbear", 1)].entryPrice == 97.0


# --- T-AZ entry timeout ----------------------------------------------------------------


def test_an_intent_that_never_finds_a_price_is_invalid_and_never_invented() -> None:
    engine, available = start()
    engine.on_market_time("capitalbear", available + 5_001)
    trade = engine.history[0]
    assert (trade.status, trade.outcome) == ("INVALID", "INVALID")
    assert trade.invalidReasons == ["ENTRY_TIMEOUT"]
    assert trade.entryPrice is None
    assert engine.entryTimeouts == 1


def test_a_price_arriving_after_the_entry_deadline_expires_the_intent_instead_of_filling_it() -> (
    None
):
    engine, available = start()
    engine.on_market_sample(fixtures.sample(available + 5_001, 100.0))
    trade = engine.history[0]
    assert trade.status == "INVALID"
    assert trade.entryPrice is None


def test_the_entry_deadline_is_measured_from_the_decision_and_not_from_the_board_close() -> None:
    engine, available = start()
    # 5.1s after the bar closed, but only 3.9s after the decision existed: still eligible.
    engine.on_market_sample(fixtures.sample(available + 3_900, 100.0))
    assert engine.live[("capitalbear", 1)].status == "OPEN"


# --- T-BA, T-BB, T-BC expiry has no lookahead ------------------------------------------


def test_a_price_a_millisecond_before_the_horizon_never_settles_the_trade() -> None:
    engine, available = start(direction="UP")
    engine.on_market_sample(fixtures.sample(available, 100.0))
    entry = available
    engine.on_market_sample(fixtures.sample(entry + 4_999, 110.0))
    assert engine.live[("capitalbear", 1)].status == "OPEN"

    engine.on_market_sample(fixtures.sample(entry + 5_050, 99.0))
    trade = engine.history[0]
    assert trade.expiryPrice == 99.0
    assert trade.outcome == "LOSS"


def test_the_first_sample_at_or_after_the_horizon_settles_it_and_later_ones_cannot() -> None:
    engine, available = start()
    engine.on_market_sample(fixtures.sample(available, 100.0))
    engine.on_market_sample(fixtures.sample(available + 5_100, 101.0))
    engine.on_market_sample(fixtures.sample(available + 5_200, 90.0))
    assert len(engine.history) == 1
    assert engine.history[0].expiryPrice == 101.0
    assert engine.history[0].outcome == "WIN"


def test_an_expiry_price_that_arrives_too_late_invalidates_rather_than_settling() -> None:
    engine, available = start()
    engine.on_market_sample(fixtures.sample(available, 100.0))
    engine.on_market_sample(fixtures.sample(available + 10_001, 120.0))
    trade = engine.history[0]
    assert (trade.status, trade.outcome) == ("INVALID", "INVALID")
    assert trade.invalidReasons == ["RESOLUTION_TIMEOUT"]
    assert trade.expiryPrice is None
    assert engine.resolutionTimeouts == 1


def test_a_quiet_slot_still_times_out_on_another_slots_market_event() -> None:
    engine, available = start()
    engine.on_market_sample(fixtures.sample(available, 100.0))
    # Nothing further on slot 1; the platform's market time advances on slot 4.
    engine.on_market_sample(fixtures.sample(available + 10_001, 100.0, slot=4))
    assert engine.history[0].invalidReasons == ["RESOLUTION_TIMEOUT"]


# --- T-BN, T-BO, T-AB the platform horizons --------------------------------------------


def test_capitalbear_runs_a_five_second_horizon() -> None:
    engine, available = start(platform="capitalbear")
    engine.on_market_sample(fixtures.sample(available, 100.0))
    trade = engine.live[("capitalbear", 1)]
    assert trade.durationMs == 5_000
    assert trade.expiryTargetTime is not None and trade.entryTime is not None
    assert trade.expiryTargetTime - trade.entryTime == 5_000


def test_iq_option_runs_a_sixty_second_horizon() -> None:
    engine, available = start(platform="iqoption")
    engine.on_market_sample(fixtures.sample(available, 100.0, platform="iqoption"))
    trade = engine.live[("iqoption", 1)]
    assert trade.durationMs == 60_000
    assert trade.expiryTargetTime is not None and trade.entryTime is not None
    assert trade.expiryTargetTime - trade.entryTime == 60_000
    assert PAPER_DURATION_MS["iqoption"] == 60_000


def test_the_same_slot_number_on_two_platforms_is_two_independent_trades() -> None:
    engine = PaperEngine()
    platforms: tuple[Platform, ...] = ("capitalbear", "iqoption")
    for platform in platforms:
        board = fixtures.board(platform=platform, slot=3, direction="UP")
        available = fixtures.decision_time(board)
        assert engine.on_board(board, available).rejection is None
        engine.on_market_sample(fixtures.sample(available, 100.0, platform=platform, slot=3))
    assert len(engine.live) == 2
    capitalbear = engine.live[("capitalbear", 3)]
    iqoption = engine.live[("iqoption", 3)]
    assert capitalbear.paperTradeId != iqoption.paperTradeId
    assert (capitalbear.durationMs, iqoption.durationMs) == (5_000, 60_000)

    # Resolving CapitalBear leaves IQ Option untouched: its horizon has not run yet.
    engine.on_market_sample(fixtures.sample(available + 5_000, 101.0, slot=3))
    assert engine.history[0].platform == "capitalbear"
    assert engine.live[("iqoption", 3)].status == "OPEN"


# --- T-BD, T-BE context isolation ------------------------------------------------------


def test_a_context_change_before_entry_cancels_the_intent() -> None:
    engine, available = start()
    engine.on_market_sample(fixtures.sample(available + 10, 100.0, context=CONTEXT_B))
    trade = engine.history[0]
    assert (trade.status, trade.outcome) == ("CANCELLED", "UNRESOLVED")
    assert "CONTEXT_CHANGED" in trade.reasons
    assert engine.contextCancellations == 1


def test_a_context_change_while_open_cancels_rather_than_resolving_on_the_new_market() -> None:
    engine, available = start()
    engine.on_market_sample(fixtures.sample(available, 100.0))
    engine.on_market_sample(fixtures.sample(available + 5_000, 200.0, context=CONTEXT_B))
    trade = engine.history[0]
    assert trade.status == "CANCELLED"
    assert trade.expiryPrice is None
    assert trade.outcome == "UNRESOLVED"


def test_a_reused_slot_carrying_a_different_asset_never_settles_the_old_trade() -> None:
    engine, available = start()
    engine.on_market_sample(fixtures.sample(available, 1.0842))
    engine.on_market_sample(
        fixtures.sample(available + 5_000, 64000.0, asset="BTC/USD OTC", context=CONTEXT_A)
    )
    trade = engine.history[0]
    assert trade.status == "CANCELLED"
    assert "ASSET_CHANGED" in trade.reasons
    assert trade.assetName == "EUR/USD OTC"


# --- T-BW source mode isolation --------------------------------------------------------


def test_a_replay_entry_is_never_settled_by_a_live_price() -> None:
    engine, available = start()
    engine.on_market_sample(fixtures.sample(available, 100.0, source="REPLAY"))
    assert engine.live[("capitalbear", 1)].entrySource == "REPLAY"

    engine.on_market_sample(fixtures.sample(available + 5_000, 101.0, source="DOM"))
    assert engine.live[("capitalbear", 1)].status == "OPEN"

    engine.on_market_sample(fixtures.sample(available + 5_100, 102.0, source="REPLAY"))
    trade = engine.history[0]
    assert trade.expirySource == "REPLAY"
    assert trade.expiryPrice == 102.0


def test_the_two_live_capture_paths_describe_one_market_and_may_appear_in_one_trade() -> None:
    # DOM and OCR are the same real broker screen; the capture layer falls back between them
    # mid-series on purpose, and refusing that would time out honest live trades.
    engine, available = start()
    engine.on_market_sample(fixtures.sample(available, 100.0, source="DOM"))
    engine.on_market_sample(fixtures.sample(available + 5_000, 101.0, source="VISUAL"))
    trade = engine.history[0]
    assert (trade.entrySource, trade.expirySource) == ("DOM", "VISUAL")
    assert trade.outcome == "WIN"


# --- T-BF, T-BP duplicates and identity ------------------------------------------------


def test_the_same_board_delivered_twice_creates_one_trade() -> None:
    engine = PaperEngine()
    board = fixtures.board()
    available = fixtures.decision_time(board)
    first = engine.on_board(board, available)
    second = engine.on_board(board, available)
    assert first.rejection is None
    assert second.rejection == "DUPLICATE"
    assert len(engine.live) == 1
    assert engine.duplicateSelections == 1


def test_the_same_selection_always_produces_the_same_paper_trade_id() -> None:
    first = PaperEngine()
    second = PaperEngine()
    board = fixtures.board()
    available = fixtures.decision_time(board)
    first.on_board(board, available)
    second.on_board(board, available)
    assert (
        first.live[("capitalbear", 1)].paperTradeId == second.live[("capitalbear", 1)].paperTradeId
    )


def test_a_different_epoch_or_slot_is_a_different_identity() -> None:
    board = fixtures.board()
    later = fixtures.board(as_of=board.asOf + 5_000)
    other_slot = fixtures.board(slot=4)
    ids: set[UUID] = set()
    for value in (board, later, other_slot):
        other = PaperEngine()
        other.on_board(value, fixtures.decision_time(value))
        ids.update(trade.paperTradeId for trade in other.live.values())
    assert len(ids) == 3


# --- T-BL, T-AA concurrency ------------------------------------------------------------


def test_a_slot_that_already_has_a_live_trade_never_stacks_a_second() -> None:
    engine, available = start(slot=3)
    engine.on_market_sample(fixtures.sample(available, 100.0, slot=3))
    later = fixtures.board(slot=3, as_of=fixtures.EPOCH + 5_000)
    update = engine.on_board(later, fixtures.decision_time(later))
    assert update.rejection == "SKIPPED_ALREADY_OPEN"
    assert engine.skippedAlreadyOpen == 1
    assert len(engine.live) == 1


def test_separate_slots_are_separate_selections_up_to_a_bounded_concurrency() -> None:
    # The epochs are placed a millisecond apart on purpose, so the only thing that can stop
    # the fourth selection is the concurrency cap rather than an entry deadline passing.
    engine = PaperEngine()
    for index, slot in enumerate((2, 5, 7, 9)):
        board = fixtures.board(slot=slot, as_of=fixtures.EPOCH + index)
        update = engine.on_board(board, fixtures.decision_time(board))
        if index < MAX_OPEN_PER_PLATFORM:
            assert update.rejection is None
        else:
            assert update.rejection == "SKIPPED_PLATFORM_LIMIT"
    assert len(engine.live) == MAX_OPEN_PER_PLATFORM
    assert engine.skippedPlatformLimit == 1


def test_the_platform_cap_is_per_platform_and_not_shared() -> None:
    engine = PaperEngine()
    for index, slot in enumerate((2, 5, 7)):
        board = fixtures.board(slot=slot, as_of=fixtures.EPOCH + index)
        engine.on_board(board, fixtures.decision_time(board))
    other = fixtures.board(platform="iqoption", slot=2)
    assert engine.on_board(other, fixtures.decision_time(other)).rejection is None


# --- T-BY the reset chain --------------------------------------------------------------


def test_a_slot_reset_cancels_live_trades_and_keeps_resolved_history() -> None:
    engine, available = start()
    engine.on_market_sample(fixtures.sample(available, 100.0))
    engine.on_market_sample(fixtures.sample(available + 5_000, 101.0))
    assert engine.history[0].outcome == "WIN"

    later = fixtures.board(as_of=fixtures.EPOCH + 10_000)
    engine.on_board(later, fixtures.decision_time(later))
    assert len(engine.live) == 1

    update = engine.reset_slot("capitalbear", [1])
    assert len(engine.live) == 0
    assert update.events[0].eventType == "CANCELLED"
    assert engine.history[-1].reasons[-1] == "SLOT_RESET"
    # History is evidence about a market that really moved that way, and survives the reset.
    assert engine.history[0].outcome == "WIN"
    assert engine.stats().wins == 1


def test_resetting_a_slot_with_nothing_live_changes_nothing() -> None:
    engine = PaperEngine()
    assert engine.reset_slot("capitalbear", [1, 2, 3]).empty


# --- T-BQ replay determinism -----------------------------------------------------------


def run(seed: int) -> list[dict[str, object]]:
    engine = PaperEngine(PaperSettings(paperCurrency="THB", paperStake=50, paperPayoutRate=0.82))
    board = fixtures.board()
    available = fixtures.decision_time(board)
    engine.on_board(board, available)
    for offset, price in ((20, 100.0), (900, 100.4), (5_030, 100.9), (5_200, 90.0)):
        engine.on_market_sample(fixtures.sample(available + offset, price + seed * 0))
    return [trade.model_dump(mode="json") for trade in engine.history]


def test_an_identical_sequence_replays_to_an_identical_history() -> None:
    first, second = run(0), run(0)
    assert first == second
    trade = first[0]
    assert trade["outcome"] == "WIN"
    assert trade["entryPrice"] == 100.0
    assert trade["expiryPrice"] == 100.9
    assert trade["realizedPaperPnl"] == pytest.approx(41.0)


def test_the_engine_reads_no_clock_of_its_own() -> None:
    # Every timestamp on a paper trade comes from a canonical market event. There is no
    # platform clock at all until the pipeline shows the engine one.
    engine = PaperEngine()
    assert engine._clock == {}
    board = fixtures.board()
    engine.on_board(board, fixtures.decision_time(board))
    assert engine._clock == {"capitalbear": fixtures.decision_time(board)}
