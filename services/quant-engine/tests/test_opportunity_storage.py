"""T-AW: the two Phase 8 categories survive Parquet and come back meaning the same thing."""

import json
from pathlib import Path

import opportunity_fixtures as fixtures
from quant_engine.market_storage import (
    BOARD_PARTITION,
    MODELS,
    Category,
    ParquetStorage,
    asset_partition,
    partition_asset,
    record_stamp,
)
from quant_engine.opportunity import OpportunityBoard, OpportunityCandidate, OpportunityEngine

CATEGORIES: tuple[Category, ...] = ("opportunity_candidates", "opportunity_boards")


def store(root: Path, *, weak: bool = False) -> tuple[ParquetStorage, OpportunityBoard]:
    """A whole finalized board — a leader, a runner-up, a NEUTRAL and an EXCLUDED slot."""
    engine = OpportunityEngine()
    expected = {1, 2, 3, 4}
    lead, follow = (0.20, 0.10) if weak else (0.75, 0.45)
    engine.ingest(fixtures.ensemble(slot=1, confidence=lead), expected)
    engine.ingest(fixtures.ensemble(slot=2, direction="DOWN", confidence=follow), expected)
    engine.ingest(fixtures.ensemble(slot=3, direction="NEUTRAL"), expected)
    engine.ingest(fixtures.ensemble(slot=4, direction="SKIP", confidence=0.0), expected)
    board = engine.latest_board("capitalbear")
    assert board is not None
    storage = ParquetStorage(root)
    for candidate in board.candidates:
        storage.append("opportunity_candidates", candidate)
    storage.append("opportunity_boards", board)
    storage.flush()
    return storage, board


def test_a_board_round_trips_with_every_candidate_it_ranked(tmp_path: Path) -> None:
    storage, written = store(tmp_path)
    assert storage.pending == []
    reloaded = storage.reload("opportunity_boards")
    assert len(reloaded) == 1
    assert isinstance(reloaded[0], OpportunityBoard)
    assert reloaded[0].model_dump(mode="json") == written.model_dump(mode="json")
    assert reloaded[0].selectedSlotId == 1
    assert len(reloaded[0].candidates) == 4


def test_candidates_round_trip_on_their_own(tmp_path: Path) -> None:
    storage, written = store(tmp_path)
    rows = storage.reload("opportunity_candidates")
    assert all(isinstance(item, OpportunityCandidate) for item in rows)
    reloaded = sorted(
        (item for item in rows if isinstance(item, OpportunityCandidate)),
        key=lambda item: item.slotId,
    )
    assert [item.model_dump(mode="json") for item in reloaded] == [
        item.model_dump(mode="json")
        for item in sorted(written.candidates, key=lambda item: item.slotId)
    ]


def test_an_absent_selection_survives_as_absent_rather_than_as_zero(tmp_path: Path) -> None:
    # A board that named nothing must reload as a board that named nothing. A null collapsing
    # to 0 would invent slot 0 and a score of zero where the layer deliberately said neither.
    storage, written = store(tmp_path, weak=True)
    reloaded = storage.reload("opportunity_boards")[0]
    assert isinstance(reloaded, OpportunityBoard)
    assert written.selectedSlotId is None
    assert reloaded.selectedSlotId is None
    assert reloaded.selectedScore is None
    assert reloaded.selectedDirection is None
    assert reloaded.status == "NO_OPPORTUNITY"
    assert reloaded.model_dump(mode="json") == written.model_dump(mode="json")


def test_reason_and_exclusion_codes_survive_as_structured_rows(tmp_path: Path) -> None:
    storage, written = store(tmp_path)
    reloaded = storage.reload("opportunity_boards")[0]
    assert isinstance(reloaded, OpportunityBoard)
    assert reloaded.reasons == written.reasons
    excluded = next(item for item in reloaded.candidates if item.candidateStatus == "EXCLUDED")
    assert excluded.exclusionReasons == ["STRATEGY_SKIP"]
    assert [entry.model_dump() for entry in reloaded.watchlist] == [
        entry.model_dump() for entry in written.watchlist
    ]


def test_every_row_carries_its_identity_and_all_four_versions(tmp_path: Path) -> None:
    storage, _ = store(tmp_path)
    for category in CATEGORIES:
        for record in storage.reload(category):
            values = record.model_dump(mode="json")
            assert values["featureVersion"] == "qfe-v2"
            assert values["regimeVersion"] == "qst-regime-v1"
            assert values["strategyVersion"] == "qst-strategy-v1"
            assert values["rankingVersion"] == "qst-ranking-v1"
            assert {"platform", "asOf"} <= set(values)


def test_candidates_partition_by_asset_and_boards_by_the_cohort_they_rank(
    tmp_path: Path,
) -> None:
    store(tmp_path)
    candidates = list((tmp_path / "opportunity_candidates").rglob("*.parquet"))
    boards = list((tmp_path / "opportunity_boards").rglob("*.parquet"))
    assert len(candidates) == 4  # one asset partition each
    assert len(boards) == 1
    partitions = {path.relative_to(tmp_path).parts[2] for path in candidates}
    assert partitions == {
        f"asset={asset_partition(fixtures.ASSETS[number])}" for number in (1, 2, 3, 4)
    }
    for path in candidates:
        parts = path.relative_to(tmp_path).parts
        assert parts[1] == "platform=capitalbear"
        assert parts[3].startswith("date=")
    assert boards[0].relative_to(tmp_path).parts[2] == f"asset={BOARD_PARTITION}"


def test_a_cross_asset_row_is_never_filed_under_one_of_its_assets(tmp_path: Path) -> None:
    _, board = store(tmp_path)
    assert partition_asset(board) == BOARD_PARTITION
    assert partition_asset(board.candidates[0]) == asset_partition(board.candidates[0].assetName)
    assert BOARD_PARTITION not in {asset_partition(name) for name in fixtures.ASSETS.values()}


def test_the_asset_name_never_reaches_the_path_verbatim(tmp_path: Path) -> None:
    store(tmp_path)
    assert not any("/" in part for path in tmp_path.rglob("*") for part in path.parts[-3:])


def test_a_board_is_partitioned_by_the_market_time_it_describes(tmp_path: Path) -> None:
    _, board = store(tmp_path)
    assert int(record_stamp(board).timestamp() * 1000) == board.asOf
    assert int(record_stamp(board.candidates[0]).timestamp() * 1000) == board.candidates[0].asOf


def test_each_category_is_reloaded_through_its_own_model() -> None:
    assert MODELS["opportunity_candidates"] is OpportunityCandidate
    assert MODELS["opportunity_boards"] is OpportunityBoard


def test_no_wall_clock_or_render_age_is_ever_persisted(tmp_path: Path) -> None:
    # X1: a stored ranking must replay identically. Anything measured against "now" would
    # make the same data produce a different board a second later.
    storage, _ = store(tmp_path)
    for category in CATEGORIES:
        for record in storage.reload(category):
            payload = json.dumps(record.model_dump(mode="json")).lower()
            for name in ("ageMs", "renderedAt", "polledAt", "receivedAt", "observedAt"):
                assert name.lower() not in payload
            assert "image" not in payload and "pixel" not in payload


def test_the_market_engine_writes_a_board_once_it_can_no_longer_change(
    tmp_path: Path,
) -> None:
    from quant_engine.market_api import MarketEngine

    engine = MarketEngine(ParquetStorage(tmp_path))
    import strategy_fixtures as strategy

    for candle in strategy.bars(
        strategy.trending(), platform="capitalbear", timeframe="S5", slot=1
    ):
        snapshot = engine.features.ingest_candle(candle)
        if snapshot is not None:
            engine.evaluate_primary_close(snapshot, snapshot.featureTime)
    engine.storage.flush()
    boards = engine.storage.reload("opportunity_boards")
    candidates = engine.storage.reload("opportunity_candidates")
    assert boards, "no finalized board reached storage"
    assert len(boards) == engine.opportunities.finalized
    assert len(candidates) == len(boards)  # one live slot, one candidate per board
    assert {board.asOf for board in boards}.isdisjoint(  # type: ignore[union-attr]
        {engine.opportunities.latest_board("capitalbear").asOf}  # type: ignore[union-attr]
    )
