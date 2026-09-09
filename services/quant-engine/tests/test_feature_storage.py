"""W19, W20 and the live wiring: persistence, warm-up hydration, engine integration and API."""

from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient
from features_fixtures import BASE_MS, CONTEXT_A, CONTEXT_B, candle, walk_or_flat
from quant_engine.app import create_app
from quant_engine.configuration import Platform
from quant_engine.features import FEATURE_VERSION, FeatureEngine, FeatureSnapshot
from quant_engine.market_api import MarketEngine, ObservationBatch
from quant_engine.market_models import Candle, Timeframe
from quant_engine.market_storage import ParquetStorage
from test_market import observation

MINUTE = 60_000


def history(count: int, **changes: object) -> list[Candle]:
    return walk_or_flat(count, **changes)


def store(root: Path, candles: list[Candle]) -> ParquetStorage:
    storage = ParquetStorage(root)
    for record in candles:
        storage.append("candles", record)
    storage.flush()
    return storage


def test_feature_snapshots_survive_a_parquet_round_trip(tmp_path: Path) -> None:
    engine = FeatureEngine()
    snapshots = [
        snapshot for record in history(60) if (snapshot := engine.ingest_candle(record)) is not None
    ]
    storage = ParquetStorage(tmp_path)
    for snapshot in (snapshots[0], snapshots[-1]):
        storage.append("features", snapshot)
    storage.flush()
    reloaded = cast(list[FeatureSnapshot], storage.reload("features"))
    assert len(reloaded) == 2
    by_time = {record.featureTime: record for record in reloaded}
    for original in (snapshots[0], snapshots[-1]):
        assert by_time[original.featureTime].model_dump() == original.model_dump()
    assert {record.featureVersion for record in reloaded} == {FEATURE_VERSION}
    assert (tmp_path / "features").exists()
    assert not any("EUR/USD" in str(path) for path in tmp_path.rglob("*")), "raw asset in path"


def test_history_loader_only_returns_earlier_bars_of_the_exact_series(tmp_path: Path) -> None:
    live = history(30, source="DOM")
    storage = store(
        tmp_path,
        live
        + history(30, source="DOM", asset="EUR/USD")
        + history(30, source="DOM", platform="iqoption")
        + history(30, source="SYNTHETIC")
        + history(30, source="DOM", timeframe="M5"),
    )
    cutoff = BASE_MS + 20 * MINUTE
    loaded = storage.load_history("capitalbear", "EUR/USD OTC", "M1", before=cutoff)
    assert [record.openTime for record in loaded] == [
        BASE_MS + index * MINUTE for index in range(20)
    ]
    assert all(record.closeTime <= cutoff for record in loaded)
    assert all(record.assetName == "EUR/USD OTC" for record in loaded), "OTC must not mix"
    assert all(record.platform == "capitalbear" for record in loaded)
    assert all(record.timeframe == "M1" for record in loaded)
    assert all(record.sourceType == "DOM" for record in loaded)
    assert storage.load_history("capitalbear", "Sui OTC", "M1", before=cutoff) == []


def test_history_loader_stops_at_a_gap_and_prefers_the_better_duplicate(tmp_path: Path) -> None:
    complete = history(30, source="DOM")
    with_gap = [record for record in complete if record.openTime != BASE_MS + 20 * MINUTE]
    poor = complete[25].model_copy(update={"quality": "DEGRADED", "coverage": 0.3})
    storage = store(tmp_path, [poor, *with_gap])
    loaded = storage.load_history("capitalbear", "EUR/USD OTC", "M1", before=BASE_MS + 30 * MINUTE)
    assert [record.openTime for record in loaded] == [
        BASE_MS + index * MINUTE for index in range(21, 30)
    ], "the tail must stop before the missing bar rather than bridge it"
    chosen = next(record for record in loaded if record.openTime == complete[25].openTime)
    assert chosen.quality == "GOOD" and chosen.coverage == 1.0


def test_history_loader_respects_the_bounded_limit(tmp_path: Path) -> None:
    storage = store(tmp_path, history(40, source="DOM"))
    loaded = storage.load_history(
        "capitalbear", "EUR/USD OTC", "M1", before=BASE_MS + 40 * MINUTE, limit=10
    )
    assert len(loaded) == 10
    assert loaded[-1].openTime == BASE_MS + 39 * MINUTE


def test_hydration_seeds_indicators_without_leaking_the_old_context(tmp_path: Path) -> None:
    storage = store(tmp_path, history(60, source="DOM", context=CONTEXT_A))
    engine = FeatureEngine(
        hydrator=lambda platform, asset, timeframe, before: storage.load_history(
            platform, asset, timeframe, before=before
        )
    )
    live = candle(60, 100.0, 100.2, 99.8, 100.0, source="VISUAL", context=CONTEXT_B, base=BASE_MS)
    snapshot = engine.ingest_candle(live)
    assert snapshot is not None
    assert snapshot.contextId == CONTEXT_B, "the emitted snapshot belongs to the live context"
    assert snapshot.sourceType == "VISUAL"
    assert snapshot.barCount == 61
    assert snapshot.quality.hydratedBars == 60
    assert snapshot.status == "READY"
    assert snapshot.trend.ema50 is not None
    assert snapshot.momentum.rsi14 is not None


def test_hydration_is_attempted_once_and_never_blocks_ingestion() -> None:
    calls: list[tuple[str, str]] = []

    def failing(platform: Platform, asset: str, timeframe: Timeframe, before: int) -> list[Candle]:
        calls.append((platform, asset))
        raise RuntimeError("storage unavailable")

    engine = FeatureEngine(hydrator=failing)
    with pytest.raises(RuntimeError):
        engine.ingest_candle(history(1)[0])
    assert calls == [("capitalbear", "EUR/USD OTC")]
    engine.ingest_candle(history(2)[1])
    assert len(calls) == 1, "hydration is a one-shot seed, not a per-bar lookup"


def test_market_engine_feeds_and_persists_features(tmp_path: Path) -> None:
    storage = ParquetStorage(tmp_path)
    engine = MarketEngine(storage)
    now = int(observation(0).observedAt.timestamp() * 1000)
    for step in range(40):
        engine.ingest(ObservationBatch(observations=[observation(step, 1.0 + step / 1000)]), now)
    engine.advance_live(now + 40_000)
    diagnostics = engine.features.diagnostics()
    assert diagnostics, "canonical events must reach the feature engine"
    s5 = next(entry for entry in diagnostics[0].timeframes if entry.timeframe == "S5")
    assert s5.barCount > 0 and s5.status == "WARMING"
    assert diagnostics[0].microSamples > 0
    assert any(category == "features" for category, _ in storage.pending)
    assert engine.reset_slots("capitalbear", [1]) == 1
    assert engine.features.diagnostics() == []


def test_feature_api_is_local_only_and_reports_state(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        now = int(observation(0).observedAt.timestamp() * 1000)
        engine = cast(MarketEngine, client.app.state.market)  # type: ignore[attr-defined]
        for step in range(40):
            engine.ingest(
                ObservationBatch(observations=[observation(step, 1.0 + step / 1000)]), now
            )
        engine.advance_live(now + 40_000)
        assert (
            client.get("/api/features/state", headers={"origin": "https://x.test"}).status_code
            == 403
        )
        state = client.get("/api/features/state").json()
        assert state["featureVersion"] == FEATURE_VERSION
        assert state["slots"] and state["slots"][0]["primaryTimeframe"] == "S5"
        slot = client.get("/api/features/capitalbear/1").json()
        assert slot["bundle"]["primaryTimeframe"] == "S5"
        assert slot["bundle"]["micro"]["samples"] > 0
        assert "S5" in slot["snapshots"]
        single = client.get("/api/features/capitalbear/1?timeframe=S5").json()
        assert set(single["snapshots"]) == {"S5"}
        assert client.get("/api/features/capitalbear/9").status_code == 404
        assert client.get("/api/features/capitalbear/0").status_code == 422
        # Feature state is read straight off the live engine, so it must refuse to read it
        # while an ingestion thread owns it rather than iterate a mutating structure.
        engine.busy = True
        try:
            assert client.get("/api/features/state").status_code == 429
            assert client.get("/api/features/capitalbear/1").status_code == 429
        finally:
            engine.busy = False
        assert client.get("/api/features/state").status_code == 200
