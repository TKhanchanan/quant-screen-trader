"""T-AD..T-AV, T-AY: cohorts, selection, isolation and the live trigger.

These tests are about the board rather than the score: which snapshots belong to one market
decision time, what happens when some never arrive, and what the layer refuses to claim.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import opportunity_fixtures as fixtures
import strategy_fixtures as strategy
from features_fixtures import CONTEXT_A, CONTEXT_B
from quant_engine.market_api import MarketEngine
from quant_engine.market_storage import ParquetStorage
from quant_engine.opportunity import (
    BOARD_HISTORY_CAPACITY,
    RECENT_CAPACITY,
    OpportunityBoard,
    OpportunityEngine,
)
from quant_engine.opportunity.scoring import MIN_LEAD_MARGIN, MIN_SELECTION_SCORE

CLEAR_LEADER = 0.75
STRONG = 0.70
MIDDLING = 0.50
WEAK = 0.20


def feed(engine: OpportunityEngine, expected: set[int], *snapshots: Any) -> OpportunityBoard | None:
    board = None
    for snapshot in snapshots:
        result = engine.ingest(snapshot, expected)
        if result is not None:
            board = result.board
    return board


def slot(board: OpportunityBoard, slot_id: int) -> Any:
    return next((item for item in board.candidates if item.slotId == slot_id), None)


# --- T-AD Phase 7 verdicts are preserved, never rewritten -------------------------------


def test_a_phase_seven_skip_is_excluded_and_never_resurrected() -> None:
    engine = OpportunityEngine()
    board = feed(engine, {1}, fixtures.ensemble(direction="SKIP", confidence=0.0))
    assert board is not None
    candidate = slot(board, 1)
    assert candidate.candidateStatus == "EXCLUDED"
    assert candidate.exclusionReasons == ["STRATEGY_SKIP"]
    assert candidate.rank is None
    assert board.rankedSlots == 0
    assert board.selectedSlotId is None
    assert board.status == "NO_OPPORTUNITY"


def test_a_phase_seven_neutral_is_preserved_and_never_becomes_a_direction() -> None:
    engine = OpportunityEngine()
    board = feed(engine, {1}, fixtures.ensemble(direction="NEUTRAL"))
    assert board is not None
    candidate = slot(board, 1)
    assert candidate.candidateStatus == "NEUTRAL"
    assert candidate.direction == "NEUTRAL"
    assert candidate.rank is None
    assert candidate.exclusionReasons == []
    assert board.rankedSlots == 0 and board.excludedSlots == 0


def test_neither_a_skip_nor_a_neutral_can_reach_the_watchlist() -> None:
    engine = OpportunityEngine()
    board = feed(
        engine,
        {1, 2, 3},
        fixtures.ensemble(slot=1, direction="SKIP", confidence=0.0),
        fixtures.ensemble(slot=2, direction="NEUTRAL"),
        fixtures.ensemble(slot=3, confidence=STRONG),
    )
    assert board is not None
    assert [entry.slotId for entry in board.watchlist] == [3]
    assert board.selectedSlotId == 3


# --- T-AE same-epoch cohort ------------------------------------------------------------


def test_a_slot_still_carrying_the_previous_close_is_not_ranked_as_current() -> None:
    # Two markets are only comparable if they describe the same decision time. A slot whose
    # ensemble is one period old is owed, not weak, and saying otherwise would rank a market
    # that has already moved on against one that has not.
    engine = OpportunityEngine()
    current = fixtures.next_epoch()
    engine.ingest(fixtures.ensemble(slot=1, as_of=current, confidence=STRONG), {1, 2})
    board = feed(
        engine, {1, 2}, fixtures.ensemble(slot=2, as_of=fixtures.EPOCH, confidence=CLEAR_LEADER)
    )
    assert board is not None
    assert board.asOf == current
    assert slot(board, 2).candidateStatus == "EXCLUDED"
    assert "STALE_FOR_EPOCH" in slot(board, 2).exclusionReasons
    assert slot(board, 2).rank is None
    assert board.receivedSlots == 1
    assert board.missingSlots == [2]
    assert board.status == "COLLECTING"
    assert "STALE_SLOT_PRESENT" in board.reasons


def test_a_stale_slot_becomes_a_real_candidate_once_it_catches_up() -> None:
    engine = OpportunityEngine()
    current = fixtures.next_epoch()
    engine.ingest(fixtures.ensemble(slot=1, as_of=current, confidence=STRONG), {1, 2})
    engine.ingest(fixtures.ensemble(slot=2, as_of=fixtures.EPOCH, confidence=WEAK), {1, 2})
    board = feed(engine, {1, 2}, fixtures.ensemble(slot=2, as_of=current, confidence=MIDDLING))
    assert board is not None
    assert slot(board, 2).candidateStatus == "ACTIONABLE"
    assert slot(board, 2).exclusionReasons == []
    assert board.receivedSlots == 2
    assert board.missingSlots == []
    assert board.status == "READY"


# --- T-AF / T-AG / T-AH cohort completion ----------------------------------------------


def test_a_cohort_collects_until_every_expected_slot_has_reported() -> None:
    engine = OpportunityEngine()
    expected = {1, 2, 3}
    first = engine.ingest(fixtures.ensemble(slot=1, confidence=STRONG), expected)
    assert first is not None and first.board.status == "COLLECTING"
    second = engine.ingest(fixtures.ensemble(slot=2, confidence=MIDDLING), expected)
    assert second is not None and second.board.status == "COLLECTING"
    third = engine.ingest(fixtures.ensemble(slot=3, confidence=0.40), expected)
    assert third is not None
    assert third.board.status == "READY"
    assert third.board.receivedSlots == third.board.expectedSlots == 3
    assert third.board.missingSlots == []


def test_an_incomplete_cohort_is_finalized_as_partial_when_the_next_epoch_starts() -> None:
    engine = OpportunityEngine()
    expected = {1, 2, 3}
    engine.ingest(fixtures.ensemble(slot=1, confidence=STRONG), expected)
    engine.ingest(fixtures.ensemble(slot=2, confidence=WEAK), expected)
    assert engine.latest_board("capitalbear").status == "COLLECTING"  # type: ignore[union-attr]
    later = engine.ingest(
        fixtures.ensemble(slot=1, as_of=fixtures.next_epoch(), confidence=STRONG), expected
    )
    assert later is not None and later.finalized is not None
    previous = later.finalized
    assert previous.status == "PARTIAL"
    assert previous.asOf == fixtures.EPOCH
    assert previous.missingSlots == [3]
    assert "MISSING_SLOTS" in previous.reasons
    assert len(previous.candidates) == 2
    assert later.board.asOf == fixtures.next_epoch()


def test_a_missing_slot_is_named_and_never_invented() -> None:
    engine = OpportunityEngine()
    board = feed(engine, {1, 2, 3}, fixtures.ensemble(slot=1))
    assert board is not None
    assert board.missingSlots == [2, 3]
    assert {item.slotId for item in board.candidates} == {1}


def test_a_disabled_slot_cannot_hold_a_cohort_at_collecting() -> None:
    # Expectation follows the live observation pipeline. Slot 9 is not in it, so the board
    # completes on the slots that exist rather than waiting forever for one that does not.
    engine = OpportunityEngine()
    board = feed(
        engine,
        {1, 2},
        fixtures.ensemble(slot=1, confidence=STRONG),
        fixtures.ensemble(slot=2, confidence=WEAK),
    )
    assert board is not None
    assert board.status == "READY"
    assert board.expectedSlots == 2
    assert 9 not in board.missingSlots


def test_an_expected_set_that_shrinks_does_not_strand_the_board() -> None:
    engine = OpportunityEngine()
    engine.ingest(fixtures.ensemble(slot=1, confidence=STRONG), {1, 2, 3})
    assert engine.latest_board("capitalbear").status == "COLLECTING"  # type: ignore[union-attr]
    board = feed(engine, {1, 2}, fixtures.ensemble(slot=2, confidence=WEAK))
    assert board is not None
    assert board.status == "READY"
    assert board.expectedSlots == 2


# --- T-AK context isolation ------------------------------------------------------------


def test_no_stability_survives_an_asset_or_context_change() -> None:
    engine = OpportunityEngine()
    for step in range(3):
        engine.ingest(
            fixtures.ensemble(
                slot=1,
                asset="EUR/USD OTC",
                context=CONTEXT_A,
                as_of=fixtures.next_epoch(periods=step),
                direction="UP",
                confidence=0.60,
            ),
            {1},
        )
    inherited = engine.candidate("capitalbear", 1)
    assert inherited is not None
    assert inherited.directionPersistence3 == 1.0
    assert inherited.confidenceMedian3 == 0.60

    board = feed(
        engine,
        {1},
        fixtures.ensemble(
            slot=1,
            asset="GBP/JPY OTC",
            context=CONTEXT_B,
            as_of=fixtures.next_epoch(periods=3),
            direction="DOWN",
            confidence=0.40,
        ),
    )
    assert board is not None
    fresh = slot(board, 1)
    assert fresh.assetName == "GBP/JPY OTC"
    # Its own single reading only: had the previous asset's window survived, the median would
    # still be 0.60 and persistence would carry three UP readings against this DOWN one.
    assert fresh.confidenceMedian3 == 0.40
    assert fresh.confidenceMedian5 == 0.40
    assert fresh.directionPersistence3 == 1.0
    assert len(engine.slots[("capitalbear", 1)].entries) == 1


def test_otc_and_non_otc_of_the_same_pair_are_different_identities() -> None:
    engine = OpportunityEngine()
    engine.ingest(fixtures.ensemble(slot=1, asset="EUR/USD", confidence=0.60), {1})
    board = feed(
        engine,
        {1},
        fixtures.ensemble(
            slot=1, asset="EUR/USD OTC", as_of=fixtures.next_epoch(), confidence=0.30
        ),
    )
    assert board is not None
    assert slot(board, 1).confidenceMedian3 == 0.30
    assert len(engine.slots[("capitalbear", 1)].entries) == 1


# --- T-AM order independence -----------------------------------------------------------


def test_one_complete_epoch_ranks_identically_whatever_order_it_arrives_in() -> None:
    expected = set(range(1, 10))
    confidences = {number: 0.10 + 0.06 * number for number in expected}

    def board_for(order: list[int]) -> OpportunityBoard:
        engine = OpportunityEngine()
        result = feed(
            engine,
            expected,
            *(fixtures.ensemble(slot=number, confidence=confidences[number]) for number in order),
        )
        assert result is not None
        return result

    forward = board_for([1, 2, 3, 4, 5, 6, 7, 8, 9])
    scrambled = board_for([9, 5, 1, 8, 2, 7, 3, 6, 4])
    assert forward.status == "READY"
    assert forward.model_dump() == scrambled.model_dump()
    assert [item.rank for item in forward.candidates] == list(range(1, 10))
    assert forward.selectedSlotId == 9


# --- T-AN / T-AO / T-AP / T-AQ selection -----------------------------------------------


def test_a_uniformly_weak_cohort_selects_nothing() -> None:
    engine = OpportunityEngine()
    board = feed(
        engine,
        {1, 2, 3},
        *(
            fixtures.ensemble(slot=number, confidence=value)
            for number, value in ((1, 0.20), (2, 0.15), (3, 0.10))
        ),
    )
    assert board is not None
    assert board.status == "NO_OPPORTUNITY"
    assert board.selectedSlotId is None
    assert board.selectedScore is None
    assert all(item.candidateStatus == "WATCH" for item in board.candidates)
    assert all(item.rankScore < MIN_SELECTION_SCORE for item in board.candidates)
    assert "BELOW_SELECTION_SCORE" in board.reasons


def test_two_candidates_a_hundredth_apart_are_not_called_a_leader() -> None:
    engine = OpportunityEngine()
    board = feed(
        engine,
        {1, 2},
        fixtures.ensemble(slot=1, confidence=0.65),
        fixtures.ensemble(slot=2, confidence=0.64),
    )
    assert board is not None
    assert board.leadMargin is not None and board.leadMargin < MIN_LEAD_MARGIN
    assert board.selectedSlotId is None
    assert board.status == "NO_OPPORTUNITY"
    assert "LOW_LEAD_MARGIN" in board.reasons
    assert all("LOW_LEAD_MARGIN" in item.rankReasonCodes for item in board.candidates[:2])
    assert board.runnerUpSlotId == 2


def test_a_clear_leader_is_named_with_its_margin() -> None:
    engine = OpportunityEngine()
    board = feed(
        engine,
        {1, 2, 3},
        fixtures.ensemble(slot=1, confidence=0.45),
        fixtures.ensemble(slot=2, confidence=CLEAR_LEADER),
        fixtures.ensemble(slot=3, confidence=0.30),
    )
    assert board is not None
    assert board.status == "READY"
    assert board.selectedSlotId == 2
    assert board.selectedAssetName == fixtures.ASSETS[2]
    assert board.selectedDirection == "UP"
    assert board.selectedScore == board.candidates[0].rankScore
    assert board.runnerUpSlotId == 1
    assert board.leadMargin is not None and board.leadMargin >= MIN_LEAD_MARGIN
    assert "TOP_CANDIDATE" in board.candidates[0].rankReasonCodes
    assert [entry.rank for entry in board.watchlist] == [1, 2, 3]


def test_a_sole_candidate_still_has_to_clear_the_bar_on_its_own() -> None:
    weak = OpportunityEngine()
    board = feed(weak, {1}, fixtures.ensemble(slot=1, confidence=WEAK))
    assert board is not None
    assert board.status == "NO_OPPORTUNITY"
    assert board.selectedSlotId is None
    assert board.runnerUpSlotId is None and board.leadMargin is None

    strong = OpportunityEngine()
    board = feed(strong, {1}, fixtures.ensemble(slot=1, confidence=STRONG))
    assert board is not None
    assert board.status == "READY"
    assert board.selectedSlotId == 1
    assert board.leadMargin is None
    assert "SOLE_CANDIDATE" in board.candidates[0].rankReasonCodes


def test_an_unfinished_cohort_never_names_a_leader() -> None:
    engine = OpportunityEngine()
    board = feed(engine, {1, 2}, fixtures.ensemble(slot=1, confidence=CLEAR_LEADER))
    assert board is not None
    assert board.status == "COLLECTING"
    assert board.selectedSlotId is None
    assert "COHORT_INCOMPLETE" in board.reasons
    assert board.candidates[0].rank == 1  # ranked, but provisional


# --- T-AR platform separation ----------------------------------------------------------


def test_the_two_platforms_keep_separate_boards_and_separate_rank_one() -> None:
    engine = OpportunityEngine()
    engine.ingest(fixtures.ensemble(platform="capitalbear", slot=1, confidence=0.45), {1})
    engine.ingest(
        fixtures.ensemble(
            platform="iqoption", slot=1, as_of=fixtures.EPOCH, confidence=CLEAR_LEADER
        ),
        {1},
    )
    capitalbear = engine.latest_board("capitalbear")
    iqoption = engine.latest_board("iqoption")
    assert capitalbear is not None and iqoption is not None
    assert capitalbear.primaryTimeframe == "S5"
    assert iqoption.primaryTimeframe == "M1"
    assert capitalbear.candidates[0].rank == 1
    assert iqoption.candidates[0].rank == 1
    assert capitalbear.selectedSlotId == 1 and iqoption.selectedSlotId == 1
    assert max(item.rank or 0 for item in [*capitalbear.candidates, *iqoption.candidates]) == 1


def test_no_board_or_candidate_carries_a_combined_ranking() -> None:
    engine = OpportunityEngine()
    engine.ingest(fixtures.ensemble(platform="capitalbear", slot=1), {1})
    board = engine.latest_board("capitalbear")
    assert board is not None
    assert "globalRank" not in board.model_dump()
    assert "globalRank" not in board.candidates[0].model_dump()
    assert all(1 <= (item.rank or 1) <= 9 for item in board.candidates)


def test_one_platform_epoch_never_finalizes_the_other() -> None:
    engine = OpportunityEngine()
    engine.ingest(fixtures.ensemble(platform="capitalbear", slot=1), {1})
    engine.ingest(fixtures.ensemble(platform="iqoption", slot=1), {1})
    result = engine.ingest(
        fixtures.ensemble(platform="iqoption", slot=1, as_of=fixtures.next_epoch("iqoption")),
        {1},
    )
    assert result is not None and result.finalized is not None
    assert result.finalized.platform == "iqoption"
    assert engine.latest_board("capitalbear").asOf == fixtures.EPOCH  # type: ignore[union-attr]


# --- T-AS version contract -------------------------------------------------------------


def test_an_unsupported_contract_is_excluded_loudly_rather_than_consumed() -> None:
    changes: list[dict[str, Any]] = [
        {"feature_version": "qfe-v1"},
        {"regime_version": "qst-regime-v2"},
        {"strategy_version": "qst-strategy-v2"},
    ]
    for change in changes:
        engine = OpportunityEngine()
        board = feed(engine, {1}, fixtures.ensemble(slot=1, confidence=CLEAR_LEADER, **change))
        assert board is not None, change
        candidate = slot(board, 1)
        assert candidate.candidateStatus == "EXCLUDED", change
        assert "UNSUPPORTED_VERSION" in candidate.exclusionReasons, change
        assert candidate.rankScore == 0.0 and candidate.rank is None, change
        assert board.selectedSlotId is None, change
        assert "UNSUPPORTED_VERSION_PRESENT" in board.reasons, change


def test_a_cohort_that_is_entirely_untrustworthy_is_invalid() -> None:
    engine = OpportunityEngine()
    board = feed(
        engine,
        {1, 2},
        fixtures.ensemble(slot=1, feature_version="qfe-v1"),
        fixtures.ensemble(slot=2, strategy_version="qst-strategy-v2"),
    )
    assert board is not None
    assert board.status == "INVALID"
    assert board.selectedSlotId is None


def test_an_ensemble_from_the_wrong_horizon_is_an_identity_failure() -> None:
    engine = OpportunityEngine()
    board = feed(
        engine, {1}, fixtures.ensemble(slot=1, platform="capitalbear", primary_timeframe="M5")
    )
    assert board is not None
    assert "INVALID_IDENTITY" in slot(board, 1).exclusionReasons
    assert board.status == "INVALID"


def test_every_persisted_shape_carries_all_four_versions() -> None:
    engine = OpportunityEngine()
    board = feed(engine, {1}, fixtures.ensemble(slot=1))
    assert board is not None
    for values in (board.model_dump(), board.candidates[0].model_dump()):
        assert values["featureVersion"] == "qfe-v2"
        assert values["regimeVersion"] == "qst-regime-v1"
        assert values["strategyVersion"] == "qst-strategy-v1"
        assert values["rankingVersion"] == "qst-ranking-v1"


def test_an_excluded_snapshot_never_enters_the_stability_window() -> None:
    engine = OpportunityEngine()
    engine.ingest(fixtures.ensemble(slot=1, feature_version="qfe-v1"), {1})
    assert ("capitalbear", 1) not in engine.slots
    assert engine.ingested == 0


# --- T-AT duplicates -------------------------------------------------------------------


def test_the_same_ensemble_twice_contributes_exactly_once() -> None:
    engine = OpportunityEngine()
    snapshot = fixtures.ensemble(slot=1, confidence=STRONG)
    first = engine.ingest(snapshot, {1})
    second = engine.ingest(snapshot, {1})
    assert first is not None and second is not None
    assert second.accepted is False
    assert engine.duplicates == 1
    assert engine.ingested == 1
    assert first.board.model_dump() == second.board.model_dump()
    assert len(engine.slots[("capitalbear", 1)].entries) == 1
    assert len(second.board.candidates) == 1


def test_a_duplicate_cannot_inflate_persistence() -> None:
    engine = OpportunityEngine()
    engine.ingest(fixtures.ensemble(slot=1, direction="DOWN", confidence=0.50), {1})
    repeated = fixtures.ensemble(
        slot=1, as_of=fixtures.next_epoch(), direction="UP", confidence=0.50
    )
    for _ in range(4):
        engine.ingest(repeated, {1})
    candidate = engine.candidate("capitalbear", 1)
    assert candidate is not None
    assert engine.duplicates == 3
    assert candidate.directionPersistence3 == 0.5  # one UP against the one DOWN before it


# --- T-AU out-of-order -----------------------------------------------------------------


def test_an_event_older_than_the_slot_has_already_ranked_is_rejected() -> None:
    engine = OpportunityEngine()
    engine.ingest(fixtures.ensemble(slot=1, as_of=fixtures.next_epoch(), confidence=STRONG), {1})
    before = engine.latest_board("capitalbear")
    result = engine.ingest(
        fixtures.ensemble(slot=1, as_of=fixtures.EPOCH, confidence=CLEAR_LEADER), {1}
    )
    assert result is not None and result.accepted is False
    assert engine.outOfOrder == 1
    assert before is not None
    assert engine.latest_board("capitalbear").model_dump() == before.model_dump()  # type: ignore[union-attr]
    assert len(engine.slots[("capitalbear", 1)].entries) == 1


def test_a_late_event_never_rewrites_a_finalized_board() -> None:
    engine = OpportunityEngine()
    engine.ingest(fixtures.ensemble(slot=1, confidence=STRONG), {1})
    engine.ingest(fixtures.ensemble(slot=1, as_of=fixtures.next_epoch(), confidence=MIDDLING), {1})
    finalized = engine.recent_boards("capitalbear", 5)[-1].model_dump()
    engine.ingest(fixtures.ensemble(slot=1, as_of=fixtures.EPOCH, confidence=WEAK), {1})
    assert engine.recent_boards("capitalbear", 5)[-1].model_dump() == finalized


# --- T-AV reset ------------------------------------------------------------------------


def test_resetting_a_slot_removes_its_candidate_and_its_stability() -> None:
    engine = OpportunityEngine()
    feed(
        engine,
        {1, 2},
        fixtures.ensemble(slot=1, confidence=CLEAR_LEADER),
        fixtures.ensemble(slot=2, confidence=0.40),
    )
    assert engine.latest_board("capitalbear").selectedSlotId == 1  # type: ignore[union-attr]
    assert engine.reset_slot("capitalbear", [1]) == 1
    board = engine.latest_board("capitalbear")
    assert board is not None
    assert ("capitalbear", 1) not in engine.slots
    assert engine.candidate("capitalbear", 1) is None
    assert {item.slotId for item in board.candidates} == {2}
    assert board.selectedSlotId == 2
    assert board.expectedSlots == 1


def test_a_reset_slot_rejoins_the_cohort_only_when_it_reports_again() -> None:
    engine = OpportunityEngine()
    engine.ingest(fixtures.ensemble(slot=1, confidence=STRONG), {1})
    engine.reset_slot("capitalbear", [1])
    board = feed(engine, {1}, fixtures.ensemble(slot=1, asset="Sui OTC", context=CONTEXT_B))
    assert board is not None
    assert board.expectedSlots == 1
    assert slot(board, 1).assetName == "Sui OTC"
    assert slot(board, 1).confidenceMedian3 == slot(board, 1).ensembleConfidence


# --- T1 bounded state ------------------------------------------------------------------


def test_neither_stability_nor_board_history_grows_without_limit() -> None:
    engine = OpportunityEngine()
    for step in range(BOARD_HISTORY_CAPACITY * 2):
        engine.ingest(fixtures.ensemble(slot=1, as_of=fixtures.next_epoch(periods=step)), {1})
    assert len(engine.slots[("capitalbear", 1)].entries) == RECENT_CAPACITY
    assert len(engine.history["capitalbear"]) == BOARD_HISTORY_CAPACITY
    assert len(engine.recent_boards("capitalbear", 500)) == BOARD_HISTORY_CAPACITY + 1
    assert engine.recent_boards("capitalbear", 0) == []


def test_recent_boards_are_newest_first_and_start_with_the_live_one() -> None:
    engine = OpportunityEngine()
    for step in range(3):
        engine.ingest(fixtures.ensemble(slot=1, as_of=fixtures.next_epoch(periods=step)), {1})
    boards = engine.recent_boards("capitalbear", 3)
    assert [board.asOf for board in boards] == [
        fixtures.next_epoch(periods=2),
        fixtures.next_epoch(periods=1),
        fixtures.next_epoch(periods=0),
    ]


# --- T-AY event integration ------------------------------------------------------------


def drive(engine: MarketEngine, slot_id: int, closes: list[float]) -> None:
    """The real chain: Phase 5 candle, Phase 6 features, Phase 7 ensemble, Phase 8 board."""
    for candle in strategy.bars(
        closes,
        platform="capitalbear",
        timeframe="S5",
        slot=slot_id,
        asset=fixtures.ASSETS[slot_id],
    ):
        snapshot = engine.features.ingest_candle(candle)
        if snapshot is not None:
            engine.evaluate_primary_close(snapshot, snapshot.featureTime)


def test_one_new_phase_seven_ensemble_produces_exactly_one_candidate_update(
    tmp_path: Path,
) -> None:
    engine = MarketEngine(ParquetStorage(tmp_path))
    drive(engine, 1, strategy.trending())
    assert engine.strategy.evaluated > 0
    assert engine.opportunities.ingested == engine.strategy.evaluated
    assert engine.opportunities.duplicates == 0
    board = engine.opportunities.latest_board("capitalbear")
    assert board is not None
    assert board.candidates[0].direction == "UP"
    assert board.candidates[0].primaryRegime == "TREND_UP"
    assert board.rankingVersion == "qst-ranking-v1"


def test_a_repeated_primary_close_does_not_rank_twice(tmp_path: Path) -> None:
    engine = MarketEngine(ParquetStorage(tmp_path))
    candles = strategy.bars(strategy.trending(), platform="capitalbear", timeframe="S5")
    for candle in candles:
        snapshot = engine.features.ingest_candle(candle)
        if snapshot is not None:
            engine.evaluate_primary_close(snapshot, snapshot.featureTime)
            engine.evaluate_primary_close(snapshot, snapshot.featureTime)  # the same close, twice
    assert engine.opportunities.ingested == engine.strategy.evaluated
    assert engine.opportunities.duplicates == 0  # Phase 7 absorbed it before Phase 8 saw it


def test_both_platforms_rank_on_their_own_primary_horizon(tmp_path: Path) -> None:
    engine = MarketEngine(ParquetStorage(tmp_path))
    drive(engine, 1, strategy.trending())
    for candle in strategy.bars(
        strategy.falling(), platform="iqoption", timeframe="M1", slot=1, asset="EUR/USD OTC"
    ):
        snapshot = engine.features.ingest_candle(candle)
        if snapshot is not None:
            engine.evaluate_primary_close(snapshot, snapshot.featureTime)
    capitalbear = engine.opportunities.latest_board("capitalbear")
    iqoption = engine.opportunities.latest_board("iqoption")
    assert capitalbear is not None and iqoption is not None
    assert capitalbear.primaryTimeframe == "S5" and capitalbear.candidates[0].direction == "UP"
    assert iqoption.primaryTimeframe == "M1" and iqoption.candidates[0].direction == "DOWN"


def test_resetting_a_slot_on_the_market_engine_clears_the_whole_chain(tmp_path: Path) -> None:
    engine = MarketEngine(ParquetStorage(tmp_path))
    drive(engine, 1, strategy.trending())
    assert engine.opportunities.latest_board("capitalbear") is not None
    engine.reset_slots("capitalbear", [1])
    assert engine.strategy.latest("capitalbear", 1) is None
    assert engine.opportunities.candidate("capitalbear", 1) is None
    board = engine.opportunities.latest_board("capitalbear")
    assert board is not None and board.candidates == []
