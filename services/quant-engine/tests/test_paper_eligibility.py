"""T-BG to T-BK, T-B: which boards create a paper intent, and which versions are simulated.

Phase 9 consumes finished decisions. It never lowers a Phase 8 gate to obtain more paper
trades, never converts an abstention into a direction, and never simulates a decision produced
under a contract it has not been checked against.
"""

from __future__ import annotations

import opportunity_fixtures as ranking
import paper_fixtures as fixtures
import pytest
from quant_engine.opportunity import OpportunityBoard, OpportunityEngine
from quant_engine.paper import (
    PAPER_VERSION,
    SUPPORTED_FEATURE_VERSION,
    SUPPORTED_RANKING_VERSION,
    SUPPORTED_REGIME_VERSION,
    SUPPORTED_STRATEGY_VERSION,
    PaperEngine,
    PaperSettings,
)


def offer(board: OpportunityBoard, settings: PaperSettings | None = None) -> tuple[str | None, int]:
    engine = PaperEngine(settings if settings is not None else PaperSettings())
    update = engine.on_board(board, fixtures.decision_time(board))
    return update.rejection, len(engine.live)


# --- T-BG to T-BJ board eligibility ----------------------------------------------------


def test_a_ready_board_with_a_selection_creates_exactly_one_intent() -> None:
    board = fixtures.board()
    assert board.status == "READY"
    assert board.selectedSlotId == 1
    assert offer(board) == (None, 1)


def test_a_collecting_board_creates_nothing() -> None:
    engine = OpportunityEngine()
    result = engine.ingest(ranking.ensemble(confidence=0.75), {1, 2, 3})
    assert result is not None and result.board.status == "COLLECTING"
    assert offer(result.board) == ("BOARD_INELIGIBLE", 0)


def test_a_partial_board_creates_nothing_by_default() -> None:
    engine = OpportunityEngine()
    engine.ingest(ranking.ensemble(confidence=0.75), {1, 2})
    result = engine.ingest(ranking.ensemble(confidence=0.75, as_of=ranking.next_epoch()), {1, 2})
    assert result is not None and result.finalized is not None
    partial = result.finalized
    assert partial.status == "PARTIAL"
    # A closed but incomplete cohort can still name a leader, and Phase 9 still declines it:
    # the decision was made over markets that had not all reported.
    assert partial.selectedSlotId == 1
    assert offer(partial) == ("BOARD_INELIGIBLE", 0)
    # Only an explicit operator setting accepts one, and that is not the default.
    assert offer(partial, PaperSettings(requireReadyBoard=False)) == (None, 1)


def test_a_board_with_no_opportunity_creates_nothing() -> None:
    engine = OpportunityEngine()
    result = engine.ingest(ranking.ensemble(confidence=0.05, agreement=0.05), {1})
    assert result is not None and result.board.status == "NO_OPPORTUNITY"
    assert result.board.selectedSlotId is None
    assert offer(result.board) == ("BOARD_INELIGIBLE", 0)
    # And it is still refused with the board-status gate relaxed, because a complete cohort
    # that named no leader has nothing to simulate.
    assert offer(result.board, PaperSettings(requireReadyBoard=False)) == ("BOARD_INELIGIBLE", 0)


def test_a_ready_board_with_no_selected_slot_creates_nothing() -> None:
    board = fixtures.board().model_copy(
        update={"selectedSlotId": None, "selectedDirection": None, "selectedScore": None}
    )
    assert offer(board) == ("NO_SELECTION", 0)


@pytest.mark.parametrize("direction", ["NEUTRAL", "SKIP"])
def test_an_abstention_is_never_converted_into_a_paper_direction(direction: str) -> None:
    board = fixtures.board().model_copy(update={"selectedDirection": direction})
    assert offer(board) == ("NO_SELECTION", 0)


def test_an_invalid_board_creates_nothing() -> None:
    engine = OpportunityEngine()
    result = engine.ingest(ranking.ensemble(feature_version="qfe-v3"), {1})
    assert result is not None and result.board.status == "INVALID"
    assert offer(result.board)[1] == 0


# --- T-BK the version contract ---------------------------------------------------------


def test_the_four_supported_upstream_versions_are_named_explicitly() -> None:
    assert SUPPORTED_FEATURE_VERSION == "qfe-v2"
    assert SUPPORTED_REGIME_VERSION == "qst-regime-v1"
    assert SUPPORTED_STRATEGY_VERSION == "qst-strategy-v1"
    assert SUPPORTED_RANKING_VERSION == "qst-ranking-v1"
    assert PAPER_VERSION == "qst-paper-v1"


@pytest.mark.parametrize(
    "change",
    [
        {"featureVersion": "qfe-v3"},
        {"regimeVersion": "qst-regime-v2"},
        {"strategyVersion": "qst-strategy-v2"},
        {"rankingVersion": "qst-ranking-v2"},
    ],
)
def test_a_future_upstream_version_is_refused_rather_than_silently_simulated(
    change: dict[str, str],
) -> None:
    # A later ranking definition means something different by "selected". Pooling its outcomes
    # with qst-ranking-v1 outcomes would make both statistics meaningless.
    engine = PaperEngine()
    board = fixtures.board().model_copy(update=change)
    update = engine.on_board(board, fixtures.decision_time(board))
    assert update.rejection == "UNSUPPORTED_VERSION"
    assert engine.unsupportedVersions == 1
    assert len(engine.live) == 0


def test_every_trade_records_all_five_versions() -> None:
    engine = PaperEngine()
    board = fixtures.board()
    engine.on_board(board, fixtures.decision_time(board))
    trade = engine.live[("capitalbear", 1)]
    assert trade.featureVersion == SUPPORTED_FEATURE_VERSION
    assert trade.regimeVersion == SUPPORTED_REGIME_VERSION
    assert trade.strategyVersion == SUPPORTED_STRATEGY_VERSION
    assert trade.rankingVersion == SUPPORTED_RANKING_VERSION
    assert trade.paperVersion == PAPER_VERSION


# --- T-D, T-I the settings contract ----------------------------------------------------


def test_the_layer_can_be_switched_off_entirely() -> None:
    engine = PaperEngine(PaperSettings(enabled=False))
    board = fixtures.board()
    assert engine.on_board(board, fixtures.decision_time(board)).rejection == "PAPER_DISABLED"
    assert engine.on_market_sample(fixtures.sample(fixtures.EPOCH, 100.0)).empty


def test_a_decision_cannot_predate_the_bar_it_was_made_from() -> None:
    engine = PaperEngine()
    board = fixtures.board()
    assert engine.on_board(board, board.asOf - 1).rejection == "CHRONOLOGY_INVALID"


# --- T-AK the metadata Phase 10 will need ----------------------------------------------


def test_a_trade_retains_everything_a_later_calibration_would_group_by() -> None:
    engine = PaperEngine()
    board = fixtures.board()
    engine.on_board(board, fixtures.decision_time(board))
    values = engine.live[("capitalbear", 1)].model_dump()
    for field in (
        "platform",
        "assetName",
        "slotId",
        "boardAsOf",
        "decisionAvailableAt",
        "direction",
        "rank",
        "rankScore",
        "ensembleConfidence",
        "agreement",
        "primaryRegime",
        "regimeConfidence",
        "boardStatus",
        "leadMargin",
    ):
        assert field in values, field
    assert values["rankScore"] > 0
    assert values["primaryRegime"] == "TREND_UP"
