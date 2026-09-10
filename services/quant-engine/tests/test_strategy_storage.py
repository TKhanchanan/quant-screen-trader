"""T21: the three Phase 7 categories survive Parquet and come back meaning the same thing."""

import json
from pathlib import Path

import strategy_fixtures as fixtures
from quant_engine.market_storage import (
    MODELS,
    Category,
    ParquetStorage,
    asset_partition,
    record_stamp,
)
from quant_engine.strategy import evaluate_bundle
from quant_engine.strategy.models import EnsembleSnapshot, RegimeSnapshot, StrategyEvaluation

CATEGORIES: tuple[Category, ...] = ("regimes", "strategy_evaluations", "ensembles")


def store(root: Path, *closes: list[float]) -> tuple[ParquetStorage, list[EnsembleSnapshot]]:
    storage = ParquetStorage(root)
    snapshots = []
    for slot, series in enumerate(closes, start=1):
        spread = 0.0025 if slot > 1 else 0.0004
        snapshot = evaluate_bundle(fixtures.bundle(series, slot=slot, spread=spread))
        snapshots.append(snapshot)
        storage.append("regimes", snapshot.regime)
        for item in snapshot.strategies:
            storage.append("strategy_evaluations", item)
        storage.append("ensembles", snapshot)
    storage.flush()
    return storage, snapshots


def test_an_ensemble_round_trips_with_its_regime_and_every_evaluation(tmp_path: Path) -> None:
    storage, written = store(tmp_path, fixtures.trending(), fixtures.noisy())
    assert storage.pending == []
    reloaded = {
        (snapshot.slotId): snapshot
        for snapshot in storage.reload("ensembles")
        if isinstance(snapshot, EnsembleSnapshot)
    }
    assert len(reloaded) == 2
    for original in written:
        assert reloaded[original.slotId].model_dump(mode="json") == original.model_dump(mode="json")
    assert len(reloaded[1].strategies) == 6
    assert reloaded[1].regime.primaryRegime == "TREND_UP"
    assert reloaded[2].direction == "SKIP"


def test_regimes_and_evaluations_round_trip_on_their_own(tmp_path: Path) -> None:
    storage, written = store(tmp_path, fixtures.trending())
    regimes = storage.reload("regimes")
    evaluations = storage.reload("strategy_evaluations")
    assert [item.model_dump(mode="json") for item in regimes] == [
        written[0].regime.model_dump(mode="json")
    ]
    assert [item.model_dump(mode="json") for item in evaluations] == [
        item.model_dump(mode="json") for item in written[0].strategies
    ]
    assert all(isinstance(item, RegimeSnapshot) for item in regimes)
    assert all(isinstance(item, StrategyEvaluation) for item in evaluations)


def test_reasons_and_vetoes_survive_as_structured_rows_not_as_text(tmp_path: Path) -> None:
    storage, written = store(tmp_path, fixtures.noisy())
    reloaded = storage.reload("ensembles")[0]
    assert isinstance(reloaded, EnsembleSnapshot)
    assert [veto.code for veto in reloaded.vetoes] == [veto.code for veto in written[0].vetoes]
    assert reloaded.vetoes[0].value == written[0].vetoes[0].value
    assert reloaded.regime.reasons[0].code == written[0].regime.reasons[0].code


def test_every_row_carries_its_identity_and_the_three_versions(tmp_path: Path) -> None:
    storage, _ = store(tmp_path, fixtures.trending())
    for category in CATEGORIES:
        for record in storage.reload(category):
            values = record.model_dump(mode="json")
            assert {"platform", "assetName", "slotId", "contextId", "asOf"} <= set(values)
            assert values["featureVersion"] == "qfe-v2"
            assert values["regimeVersion"] == "qst-regime-v1"
            if category != "regimes":
                assert values["strategyVersion"] == "qst-strategy-v1"


def test_the_partition_layout_matches_the_existing_categories(tmp_path: Path) -> None:
    store(tmp_path, fixtures.trending())
    for category in CATEGORIES:
        files = list((tmp_path / category).rglob("*.parquet"))
        assert files, f"{category} wrote nothing"
        parts = files[0].relative_to(tmp_path).parts
        assert parts[0] == category
        assert parts[1] == "platform=iqoption"
        assert parts[2] == f"asset={asset_partition(fixtures.ASSET)}"
        assert parts[3].startswith("date=")


def test_the_asset_name_never_reaches_the_path_verbatim(tmp_path: Path) -> None:
    storage = ParquetStorage(tmp_path)
    snapshot = evaluate_bundle(fixtures.bundle(fixtures.trending(), asset="EUR/USD OTC"))
    storage.append("ensembles", snapshot)
    storage.flush()
    assert not any("/" in part for path in tmp_path.rglob("*") for part in path.parts[-2:])
    reloaded = storage.reload("ensembles")[0]
    assert reloaded.assetName == "EUR/USD OTC"


def test_each_category_is_reloaded_through_its_own_model(tmp_path: Path) -> None:
    assert MODELS["regimes"] is RegimeSnapshot
    assert MODELS["strategy_evaluations"] is StrategyEvaluation
    assert MODELS["ensembles"] is EnsembleSnapshot


def test_a_snapshot_is_partitioned_by_the_time_it_describes(tmp_path: Path) -> None:
    snapshot = evaluate_bundle(fixtures.bundle(fixtures.trending()))
    stamp = record_stamp(snapshot)
    assert int(stamp.timestamp() * 1000) == snapshot.asOf


def test_no_screenshot_or_raw_capture_reaches_the_strategy_store(tmp_path: Path) -> None:
    store(tmp_path, fixtures.trending())
    storage = ParquetStorage(tmp_path)
    for category in CATEGORIES:
        for record in storage.reload(category):
            payload = json.dumps(record.model_dump(mode="json"))
            assert "image" not in payload and "png" not in payload
            assert "pixel" not in payload.lower()
