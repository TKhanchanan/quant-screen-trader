"""T-CB, T-AM, T-AN, T-AO: the paper read API sits behind the same local-only boundary.

The whole surface is read-only. There is no endpoint that opens, settles, cancels or prices a
paper trade, and none that writes a simulated stake: a browser that reached this port could
still not change what a running simulation is measuring.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import test_paper_pipeline as pipeline
from fastapi.testclient import TestClient
from features_fixtures import BASE_MS
from quant_engine.app import create_app
from quant_engine.market_api import MarketEngine, ObservationBatch
from quant_engine.paper import PAPER_VERSION

PATHS = (
    "/api/paper/state",
    "/api/paper/open",
    "/api/paper/history",
    "/api/paper/stats",
    "/api/paper/settings",
    "/api/paper/settlements",
)


def seeded(client: TestClient) -> MarketEngine:
    """Drive the real Phase 5-9 path on the application's own engine, as live does."""
    engine = cast(MarketEngine, client.app.state.market)  # type: ignore[attr-defined]
    rows = []
    for second in range(pipeline.SECONDS):
        rows.append(
            pipeline.observation(pipeline.LEADER, BASE_MS + second * 1000, pipeline.rising(second))
        )
        rows.append(
            pipeline.observation(
                pipeline.LAGGARD,
                BASE_MS + second * 1000 + pipeline.LAG_MS,
                pipeline.choppy(second),
            )
        )
    for start in range(0, len(rows), 18):
        engine.ingest(ObservationBatch(observations=rows[start : start + 18]), BASE_MS)
    assert engine.paper.resolvedCount > 0
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


def test_state_reports_the_version_contract_and_both_platforms(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        seeded(client)
        state = client.get("/api/paper/state").json()
        assert state["paperVersion"] == PAPER_VERSION
        assert state["featureVersion"] == "qfe-v2"
        assert state["regimeVersion"] == "qst-regime-v1"
        assert state["strategyVersion"] == "qst-strategy-v1"
        assert state["rankingVersion"] == "qst-ranking-v1"
        assert state["resolved"] > 0
        assert {p["platform"] for p in state["platforms"]} == {"capitalbear", "iqoption"}
        capitalbear = next(p for p in state["platforms"] if p["platform"] == "capitalbear")
        assert capitalbear["durationMs"] == 5_000
        assert capitalbear["wins"] > 0
        iqoption = next(p for p in state["platforms"] if p["platform"] == "iqoption")
        assert (iqoption["durationMs"], iqoption["resolved"]) == (60_000, 0)


def test_history_is_bounded_and_filterable(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        seeded(client)
        assert client.get("/api/paper/history", params={"limit": 0}).status_code == 422
        assert client.get("/api/paper/history", params={"limit": 501}).status_code == 422
        capped = client.get("/api/paper/history", params={"limit": 3}).json()
        assert len(capped["trades"]) == 3
        other = client.get("/api/paper/history", params={"platform": "iqoption"}).json()
        assert other["trades"] == []
        default = client.get("/api/paper/history").json()
        assert 0 < len(default["trades"]) <= 100


def test_a_single_trade_can_be_looked_up_by_its_deterministic_id(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        engine = seeded(client)
        identity = engine.paper.history[0].paperTradeId
        found = client.get(f"/api/paper/trades/{identity}").json()
        assert found["trade"]["paperTradeId"] == str(identity)
        assert found["paperVersion"] == PAPER_VERSION
        missing = client.get("/api/paper/trades/11111111-1111-4111-8111-111111111111")
        assert missing.status_code == 404
        assert client.get("/api/paper/trades/not-a-uuid").status_code == 422


def test_stats_are_descriptive_and_carry_no_monetary_claim_by_default(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        seeded(client)
        stats = client.get("/api/paper/stats", params={"platform": "capitalbear"}).json()["stats"]
        assert stats["wins"] > 0
        assert stats["winRateExcludingDraws"] == 1.0
        assert stats["netPaperPnl"] is None
        assert stats["grossPaperProfit"] is None


def test_settings_report_the_frozen_policy_and_whether_money_is_configured(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        seeded(client)
        body = client.get("/api/paper/settings").json()
        assert body["accountingConfigured"] is False
        assert body["settings"]["paperStake"] is None
        assert body["settings"]["paperPayoutRate"] is None
        assert body["settings"]["requireReadyBoard"] is True
        assert body["durationMs"] == {"capitalbear": 5_000, "iqoption": 60_000}
        assert body["maxEntryDelayMs"] == {"capitalbear": 5_000, "iqoption": 10_000}
        assert body["maxResolutionLagMs"] == {"capitalbear": 5_000, "iqoption": 10_000}
        assert body["maxOpenPerPlatform"] == 3


def test_settlements_are_reported_newest_first_and_bounded(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        engine = seeded(client)
        body = client.get("/api/paper/settlements", params={"limit": 2}).json()
        assert len(body["settlements"]) == 2
        assert body["settlements"][0]["tradeId"] == str(engine.paper.settlements[-1].tradeId)
        assert body["settlements"][0]["realizedPnl"] is None
        assert client.get("/api/paper/settlements", params={"limit": 0}).status_code == 422
