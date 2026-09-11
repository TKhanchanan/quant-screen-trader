"""T-CN, T-CO, T-AV, T-AW, T-AX: the read surface, its trust boundary and its refusals.

The whole surface is read-only, and read-only in a stronger sense than the other diagnostics
readers: there is no endpoint here that writes anything at all, and none that could apply a
threshold it has just reported. A browser that reached this port could read a calibration; it
could not change what the application will do next, because nothing on this port can.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import analytics_fixtures as fixtures
from fastapi.testclient import TestClient
from quant_engine.analytics import ANALYTICS_VERSION
from quant_engine.app import create_app
from quant_engine.market_api import MarketEngine

PATHS = (
    "/api/analytics/summary",
    "/api/analytics/calibration/rank",
    "/api/analytics/calibration/confidence",
    "/api/analytics/regimes",
    "/api/analytics/strategies",
    "/api/analytics/assets",
    "/api/analytics/time",
    "/api/analytics/thresholds",
    "/api/analytics/snapshot",
    "/api/analytics/export",
)

SAMPLE = 180


def seeded(client: TestClient) -> MarketEngine:
    """Put a real durable record under the API, through the storage the engine actually uses."""
    engine = cast(MarketEngine, client.app.state.market)  # type: ignore[attr-defined]
    trades, evaluations = fixtures.corpus()
    for trade in trades[:SAMPLE]:
        engine.storage.append("paper_trades", trade)
    identities = {trade.paperTradeId for trade in trades[:SAMPLE]}
    keys = {
        (trade.platform, trade.slotId, trade.contextId, trade.boardAsOf)
        for trade in trades
        if trade.paperTradeId in identities
    }
    for evaluation in evaluations:
        key = (evaluation.platform, evaluation.slotId, evaluation.contextId, evaluation.asOf)
        if key in keys:
            engine.storage.append("strategy_evaluations", evaluation)
    engine.storage.flush()
    return engine


# --- T-CN the trust boundary -----------------------------------------------------------


def test_a_browser_origin_is_refused_and_a_local_client_is_not(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        seeded(client)
        for path in PATHS:
            assert client.get(path, headers={"origin": "https://x.test"}).status_code == 403
            assert client.get(path, headers={"sec-fetch-site": "cross-site"}).status_code == 403
            assert client.get(path).status_code == 200


# --- T-CO busy safety ------------------------------------------------------------------


def test_a_busy_engine_is_retried_rather_than_read_half_updated(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        engine = seeded(client)
        engine.busy = True
        try:
            for path in PATHS:
                assert client.get(path).status_code == 429
        finally:
            engine.busy = False


def test_an_analysis_being_rebuilt_is_retried_rather_than_served_half_built(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        engine = seeded(client)
        engine.analytics.rebuilding = True
        try:
            for path in PATHS:
                assert client.get(path).status_code == 429
        finally:
            engine.analytics.rebuilding = False
        assert client.get("/api/analytics/summary").status_code == 200


# --- T-AV the endpoints ----------------------------------------------------------------


def test_every_response_names_its_version_and_its_sample_size(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        seeded(client)
        for path in PATHS:
            body = client.get(path).json()
            assert body["analyticsVersion"] == ANALYTICS_VERSION
            assert body["paperVersion"] == "qst-paper-v1"
            assert "sampleCount" in body


def test_the_summary_reports_data_quality_beside_the_outcome(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        seeded(client)
        body = client.get("/api/analytics/summary").json()
        assert body["sampleCount"] == SAMPLE
        assert body["quality"]["resolved"] == SAMPLE
        assert body["quality"]["resolvedRate"] == 1.0
        assert (
            body["overall"]["wins"] + body["overall"]["losses"] + body["overall"]["draws"] == SAMPLE
        )
        assert body["money"]["available"] is True
        assert {item["key"] for item in body["platforms"]} == {"capitalbear", "iqoption"}
        assert body["temporalSplit"]["ordering"] == "CHRONOLOGICAL"
        assert body["temporalSplit"]["shuffled"] is False


def test_the_calibration_endpoints_report_bands_and_say_what_they_are_not(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        seeded(client)
        for path, metric in (
            ("/api/analytics/calibration/rank", "rankScore"),
            ("/api/analytics/calibration/confidence", "ensembleConfidence"),
        ):
            calibration = client.get(path).json()["calibration"]
            assert calibration["metric"] == metric
            assert len(calibration["bins"]) == 10
            assert calibration["bins"][0]["label"] == "[0.00, 0.10)"
            assert calibration["bins"][-1]["label"] == "[0.90, 1.00]"
            assert "not a probability calibration" in calibration["note"]
            assert calibration["correlation"]["sampleCount"] > 0


def test_the_regime_strategy_asset_and_time_endpoints_carry_their_tables(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        seeded(client)
        regimes = client.get("/api/analytics/regimes").json()
        assert regimes["regimes"] and regimes["regimeDirection"]["columns"] == ["UP", "DOWN"]
        strategies = client.get("/api/analytics/strategies").json()
        assert len(strategies["strategies"]) == len(fixtures.STRATEGIES)
        assert strategies["strategyRegime"]["cells"]
        assert strategies["joinedTrades"] > 0
        assets = client.get("/api/analytics/assets").json()
        assert assets["assets"] and assets["minAssetSample"] == 30
        assert all("|" in item["key"] for item in assets["assets"])
        time_of_day = client.get("/api/analytics/time").json()
        assert time_of_day["hours"] and time_of_day["weekdays"]
        assert time_of_day["timezone"] == "Asia/Bangkok"
        assert "Asia/Bangkok" in time_of_day["hours"][0]["label"]


def test_the_threshold_endpoint_says_plainly_that_nothing_is_applied(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        seeded(client)
        body = client.get("/api/analytics/thresholds").json()
        assert body["appliedToLiveExecution"] is False
        assert body["researchOnly"] is True
        assert "no way to apply" in body["notice"]
        assert body["comparisonsEvaluated"] > 1
        assert "MULTIPLE_TESTING_WARNING" in body["warnings"]
        for candidate in body["candidates"]:
            assert candidate["appliedToLiveExecution"] is False
            assert candidate["operator"] == ">="


def test_the_snapshot_endpoint_is_deterministic_across_two_reads(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        seeded(client)
        first = client.get("/api/analytics/snapshot").json()["snapshot"]
        second = client.get("/api/analytics/snapshot?refresh=true").json()["snapshot"]
        assert first["snapshotId"] == second["snapshotId"]
        assert first == second


def test_the_snapshot_is_persisted_beside_the_market_record(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        seeded(client)
        identity = client.get("/api/analytics/snapshot").json()["snapshot"]["snapshotId"]
        from quant_engine.paths import app_paths
        from quant_engine.storage.analytics_repository import load_snapshots

        stored = load_snapshots(app_paths(tmp_path).market_data)
        assert [str(item.snapshotId) for item in stored] == [identity]


# --- T-AW filters ----------------------------------------------------------------------


def test_a_filter_narrows_the_population_and_the_quality_report_with_it(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        seeded(client)
        whole = client.get("/api/analytics/summary").json()
        part = client.get("/api/analytics/summary?platform=iqoption").json()
        assert part["sampleCount"] < whole["sampleCount"]
        # The data-quality report describes the same population as the metrics beside it.
        assert part["quality"]["resolved"] == part["sampleCount"]
        assert {item["key"] for item in part["platforms"]} == {"iqoption"}
        assert part["snapshotId"] != whole["snapshotId"]


def test_each_documented_filter_is_accepted_and_an_unknown_value_is_refused(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        seeded(client)
        for query in (
            "platform=capitalbear",
            "assetName=Gold%20OTC",
            "regime=NOISY",
            "direction=UP",
            f"from={fixtures.BASE_MS}",
            f"to={fixtures.BASE_MS + 60 * fixtures.STEP_MS}",
        ):
            assert client.get(f"/api/analytics/summary?{query}").status_code == 200
        for bad in ("platform=binance", "regime=SIDEWAYS", "direction=SIDEWAYS", "from=soon"):
            assert client.get(f"/api/analytics/summary?{bad}").status_code == 422


def test_a_filter_that_matches_nothing_reports_an_empty_sample_rather_than_failing(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        seeded(client)
        body = client.get("/api/analytics/summary?assetName=Nothing%20OTC").json()
        assert body["sampleCount"] == 0
        assert body["sampleLabel"] == "INSUFFICIENT_SAMPLE"
        assert "INSUFFICIENT_SAMPLE" in body["warnings"]
        assert body["overall"]["winRateExcludingDraws"] is None


# --- T-BG export -----------------------------------------------------------------------


def test_the_export_carries_market_measurements_and_nothing_else(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        seeded(client)
        body = client.get("/api/analytics/export?limit=5").json()
        assert body["returned"] == 5 and body["sampleCount"] == SAMPLE
        assert set(body["rows"][0]) == set(body["columns"])
        forbidden = {"contextId", "calibrationProfileId", "entryPrice", "cookie", "session"}
        assert forbidden.isdisjoint(set(body["columns"]))

        csv_response = client.get("/api/analytics/export?format=csv&limit=3")
        assert csv_response.status_code == 200
        assert csv_response.headers["content-type"].startswith("text/csv")
        lines = csv_response.text.strip().splitlines()
        assert len(lines) == 4
        assert lines[0].startswith("paperTradeId,platform,slotId")


def test_the_export_is_bounded(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        seeded(client)
        assert client.get("/api/analytics/export?limit=0").status_code == 422
        assert client.get("/api/analytics/export?limit=999999").status_code == 422
        assert client.get("/api/analytics/export?format=parquet").status_code == 422


# --- T-CF nothing upstream moved -------------------------------------------------------


def test_reading_every_analytics_endpoint_changes_nothing_in_the_engine(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        engine = seeded(client)
        before = (
            engine.paper.settings.model_dump(mode="json"),
            engine.guard.settings.model_dump(mode="json"),
            engine.guard.state(fixtures.BASE_MS).model_dump(mode="json"),
            engine.paper.stats().model_dump(mode="json"),
        )
        for path in PATHS:
            assert client.get(path).status_code == 200
        assert engine.paper.settings.model_dump(mode="json") == before[0]
        assert engine.guard.settings.model_dump(mode="json") == before[1]
        assert engine.guard.state(fixtures.BASE_MS).model_dump(mode="json") == before[2]
        assert engine.paper.stats().model_dump(mode="json") == before[3]
