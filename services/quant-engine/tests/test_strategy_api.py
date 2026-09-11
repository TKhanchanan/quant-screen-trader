"""T22: the strategy read API sits behind the same local-only boundary as the rest."""

from pathlib import Path
from typing import cast

import strategy_fixtures as fixtures
from fastapi.testclient import TestClient
from quant_engine.app import create_app
from quant_engine.market_api import MarketEngine
from quant_engine.strategy import REGIME_VERSION, STRATEGY_VERSION, SUPPORTED_FEATURE_VERSION


def seeded(client: TestClient, count: int = 3) -> MarketEngine:
    """Drive the engine through the real Phase 6 path, then trigger Phase 7 as live does."""
    engine = cast(MarketEngine, client.app.state.market)  # type: ignore[attr-defined]
    for candle in fixtures.bars(
        fixtures.trending(), platform="capitalbear", timeframe="S5", slot=1
    ):
        snapshot = engine.features.ingest_candle(candle)
        if snapshot is not None:
            engine.evaluate_primary_close(snapshot, snapshot.featureTime)
    assert engine.strategy.evaluated >= count
    return engine


def test_a_browser_origin_is_refused(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        seeded(client)
        for path in (
            "/api/strategy/state",
            "/api/strategy/capitalbear/1",
            "/api/strategy/capitalbear/1/history",
        ):
            assert client.get(path, headers={"origin": "https://x.test"}).status_code == 403
            assert client.get(path, headers={"sec-fetch-site": "cross-site"}).status_code == 403
            assert client.get(path).status_code == 200


def test_state_reports_the_version_contract_and_one_row_per_live_slot(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        engine = seeded(client)
        state = client.get("/api/strategy/state").json()
        assert state["featureVersion"] == SUPPORTED_FEATURE_VERSION
        assert state["regimeVersion"] == REGIME_VERSION
        assert state["strategyVersion"] == STRATEGY_VERSION
        assert state["evaluated"] == engine.strategy.evaluated
        assert len(state["slots"]) == 1
        assert state["slots"][0]["primaryRegime"] == "TREND_UP"
        assert state["slots"][0]["direction"] == "UP"


def test_a_slot_returns_its_regime_evaluations_and_ensemble(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        seeded(client)
        payload = client.get("/api/strategy/capitalbear/1").json()
        assert payload["regime"]["primaryRegime"] == "TREND_UP"
        assert len(payload["strategies"]) == 6
        assert payload["ensemble"]["direction"] == "UP"
        assert payload["ensemble"]["weights"]["active"] > 0
        assert 0 <= payload["ensemble"]["confidence"] <= 1


def test_history_is_bounded_and_ordered(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        seeded(client)
        payload = client.get("/api/strategy/capitalbear/1/history?limit=5").json()
        stamps = [item["asOf"] for item in payload["ensembles"]]
        assert len(stamps) == 5
        assert stamps == sorted(stamps)
        assert client.get("/api/strategy/capitalbear/1/history").json()["ensembles"] != []
        assert client.get("/api/strategy/capitalbear/1/history?limit=99").status_code == 422
        assert client.get("/api/strategy/capitalbear/1/history?limit=0").status_code == 422


def test_an_unknown_slot_is_a_404_and_an_impossible_one_a_422(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        seeded(client)
        assert client.get("/api/strategy/capitalbear/7").status_code == 404
        assert client.get("/api/strategy/capitalbear/0").status_code == 422
        assert client.get("/api/strategy/nasdaq/1").status_code == 422


def test_reading_while_an_ingestion_thread_owns_the_engine_is_a_retryable_429(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        engine = seeded(client)
        engine.busy = True
        try:
            assert client.get("/api/strategy/state").status_code == 429
            assert client.get("/api/strategy/capitalbear/1").status_code == 429
            assert client.get("/api/strategy/capitalbear/1/history").status_code == 429
        finally:
            engine.busy = False
        assert client.get("/api/strategy/state").status_code == 200


def test_the_api_offers_no_write_or_execution_route(tmp_path: Path) -> None:
    # The published surface, not the router object: whatever a caller can reach is what
    # matters, and for this layer that is three reads and nothing else.
    with TestClient(create_app(data_dir=tmp_path)) as client:
        paths = client.get("/openapi.json").json()["paths"]
        strategy = {
            path: set(operations)
            for path, operations in paths.items()
            if path.startswith("/api/strategy")
        }
        assert set(strategy) == {
            "/api/strategy/state",
            "/api/strategy/{platform}/{slot_id}",
            "/api/strategy/{platform}/{slot_id}/history",
        }
        assert all(methods == {"get"} for methods in strategy.values())
