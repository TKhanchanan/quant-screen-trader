"""T-AX: the ranking read API sits behind the same local-only boundary as the rest."""

from pathlib import Path
from typing import cast

import strategy_fixtures as strategy
from fastapi.testclient import TestClient
from quant_engine.app import create_app
from quant_engine.market_api import MarketEngine
from quant_engine.opportunity import (
    RANKING_VERSION,
    SUPPORTED_FEATURE_VERSION,
    SUPPORTED_REGIME_VERSION,
    SUPPORTED_STRATEGY_VERSION,
)

PATHS = (
    "/api/opportunities/state",
    "/api/opportunities/capitalbear",
    "/api/opportunities/capitalbear/history",
)


def seeded(client: TestClient) -> MarketEngine:
    """Drive the real Phase 5-7 path, then let Phase 8 rank it as live does."""
    engine = cast(MarketEngine, client.app.state.market)  # type: ignore[attr-defined]
    for candle in strategy.bars(
        strategy.trending(), platform="capitalbear", timeframe="S5", slot=1
    ):
        snapshot = engine.features.ingest_candle(candle)
        if snapshot is not None:
            engine.evaluate_primary_close(snapshot)
    assert engine.opportunities.ingested > 0
    return engine


def test_a_browser_origin_is_refused(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        seeded(client)
        for path in PATHS:
            assert client.get(path, headers={"origin": "https://x.test"}).status_code == 403
            assert client.get(path, headers={"sec-fetch-site": "cross-site"}).status_code == 403
            assert client.get(path).status_code == 200


def test_a_busy_engine_is_retried_rather_than_read_half_updated(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        engine = seeded(client)
        engine.busy = True
        try:
            for path in PATHS:
                assert client.get(path).status_code == 429
        finally:
            engine.busy = False
        assert client.get(PATHS[0]).status_code == 200


def test_state_reports_all_four_versions_and_both_platforms_separately(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        engine = seeded(client)
        state = client.get("/api/opportunities/state").json()
        assert state["featureVersion"] == SUPPORTED_FEATURE_VERSION
        assert state["regimeVersion"] == SUPPORTED_REGIME_VERSION
        assert state["strategyVersion"] == SUPPORTED_STRATEGY_VERSION
        assert state["rankingVersion"] == RANKING_VERSION
        assert state["ingested"] == engine.opportunities.ingested
        platforms = {entry["platform"]: entry for entry in state["platforms"]}
        assert set(platforms) == {"capitalbear"}
        assert platforms["capitalbear"]["primaryTimeframe"] == "S5"
        assert platforms["capitalbear"]["board"]["platform"] == "capitalbear"


def test_a_platform_board_is_returned_whole(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        seeded(client)
        payload = client.get("/api/opportunities/capitalbear").json()
        board = payload["board"]
        assert board["primaryTimeframe"] == "S5"
        assert board["rankingVersion"] == RANKING_VERSION
        assert board["candidates"][0]["direction"] == "UP"
        assert 0 <= board["candidates"][0]["rankScore"] <= 1
        assert board["status"] in ("COLLECTING", "READY", "PARTIAL", "NO_OPPORTUNITY", "INVALID")


def test_a_platform_with_no_board_is_reported_as_absent_rather_than_invented(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        seeded(client)
        assert client.get("/api/opportunities/iqoption").status_code == 404


def test_an_unknown_platform_is_rejected(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        seeded(client)
        assert client.get("/api/opportunities/binance").status_code == 422


def test_history_is_bounded_at_both_ends(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        seeded(client)
        assert client.get("/api/opportunities/capitalbear/history?limit=0").status_code == 422
        assert client.get("/api/opportunities/capitalbear/history?limit=101").status_code == 422
        boards = client.get("/api/opportunities/capitalbear/history?limit=3").json()["boards"]
        assert 1 <= len(boards) <= 3
        assert [board["asOf"] for board in boards] == sorted(
            (board["asOf"] for board in boards), reverse=True
        )


def test_reading_the_api_never_computes_a_ranking(tmp_path: Path) -> None:
    # V: ranking is driven by a Phase 7 event, never by a UI poll. Polling every endpoint
    # repeatedly must leave every counter and the live board exactly where they were.
    with TestClient(create_app(data_dir=tmp_path)) as client:
        engine = seeded(client)
        before = (
            engine.opportunities.ingested,
            engine.opportunities.finalized,
            engine.opportunities.latest_board("capitalbear").model_dump(),  # type: ignore[union-attr]
        )
        for _ in range(3):
            for path in PATHS:
                assert client.get(path).status_code == 200
        assert (
            engine.opportunities.ingested,
            engine.opportunities.finalized,
            engine.opportunities.latest_board("capitalbear").model_dump(),  # type: ignore[union-attr]
        ) == before


def test_the_surface_offers_no_way_to_act_on_a_ranking(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        seeded(client)
        routes = {
            (str(getattr(route, "path", "")), method)
            for route in client.app.routes  # type: ignore[attr-defined]
            for method in getattr(route, "methods", set())
            if str(getattr(route, "path", "")).startswith("/api/opportunities")
        }
        assert {method for _, method in routes} <= {"GET", "HEAD"}
        for path in PATHS:
            assert client.post(path, json={}).status_code in (404, 405)
            assert client.delete(path).status_code in (404, 405)
