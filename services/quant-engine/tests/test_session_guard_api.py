"""T-BJ, T-BK, T-AC: the session API sits behind the same local-only boundary as the rest.

The reads are reads. The two writes — an operator's own limits, and stopping the day — are
reachable only from the local trust boundary, and the stop is deliberately the one call in the
engine that a busy ingestion thread cannot refuse.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import cast

import session_guard_fixtures as fixtures
from fastapi.testclient import TestClient
from quant_engine.app import create_app
from quant_engine.market_api import MarketEngine
from quant_engine.session_guard import SESSION_GUARD_VERSION

READS = (
    "/api/session-guard/state",
    "/api/session-guard/settings",
    "/api/session-guard/history",
    "/api/session-guard/events",
)


def seeded(client: TestClient, **changes: object) -> MarketEngine:
    """A day with 120 realized, stamped at the real present moment.

    The API decides which trading day is current from the wall clock, so a fixture anchored to a
    fixed date would roll over the moment the calendar moved past it and leave these tests
    asserting against tomorrow. Only the guard's *accounting* is clock-free; "which day is now"
    genuinely is not.
    """
    engine = cast(MarketEngine, client.app.state.market)  # type: ignore[attr-defined]
    now = int(time.time() * 1000)
    engine.guard.settings = fixtures.settings(**changes)
    engine.persist_session(engine.guard.tick(now))
    engine.persist_session(engine.guard.apply_settlement(fixtures.settlement(120, settled_at=now)))
    return engine


def test_a_browser_origin_is_refused(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        seeded(client)
        for path in READS:
            assert client.get(path, headers={"origin": "https://x.test"}).status_code == 403
            assert client.get(path, headers={"sec-fetch-site": "cross-site"}).status_code == 403
            assert client.get(path).status_code == 200
        refused = client.post(
            "/api/session-guard/command",
            json={"operation": "stop"},
            headers={"origin": "https://x.test"},
        )
        assert refused.status_code == 403


def test_a_busy_engine_is_retried_rather_than_read_half_updated(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        engine = seeded(client)
        engine.busy = True
        try:
            for path in READS:
                assert client.get(path).status_code == 429
            settings = client.post(
                "/api/session-guard/command",
                json={"operation": "settings", "enabled": True, "dailyProfitTarget": 600},
            )
            assert settings.status_code == 429
        finally:
            engine.busy = False
        assert client.get(READS[0]).status_code == 200


def test_state_reports_the_permission_and_the_version_contract(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        seeded(client, dailyProfitTarget=600)
        state = client.get("/api/session-guard/state").json()
        assert state["sessionGuardVersion"] == SESSION_GUARD_VERSION
        assert state["paperVersion"] == "qst-paper-v1"
        assert state["accountingSource"] == "PAPER"
        assert state["canOpenNewEntry"] is True
        assert state["blockReason"] is None
        assert state["shutdownRequested"] is False
        assert state["session"]["realizedPnl"] == 120
        assert state["targetProgress"] == 0.2
        assert state["remainingToTarget"] == 480
        assert state["paperAccountingConfigured"] is False


def test_history_and_events_are_bounded(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        seeded(client)
        assert client.get("/api/session-guard/history", params={"limit": 0}).status_code == 422
        assert client.get("/api/session-guard/history", params={"limit": 366}).status_code == 422
        assert client.get("/api/session-guard/events", params={"limit": 501}).status_code == 422
        history = client.get("/api/session-guard/history", params={"limit": 1}).json()
        assert len(history["sessions"]) == 1
        assert history["sessions"][0]["winRateIncludingDraws"] == 1.0
        events = client.get("/api/session-guard/events").json()
        assert {row["type"] for row in events["events"]} >= {
            "SESSION_CREATED",
            "SETTLEMENT_APPLIED",
        }


def test_a_single_session_can_be_looked_up_and_a_missing_one_is_a_404(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        engine = seeded(client)
        assert engine.guard.current is not None
        identity = engine.guard.current.sessionId
        found = client.get(f"/api/session-guard/sessions/{identity}").json()
        assert found["session"]["sessionId"] == str(identity)
        assert found["summary"]["realizedPnl"] == 120
        missing = client.get("/api/session-guard/sessions/11111111-1111-4111-8111-111111111111")
        assert missing.status_code == 404


def test_settings_are_written_through_the_local_trusted_path_and_survive(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        seeded(client)
        saved = client.post(
            "/api/session-guard/command",
            json={
                "operation": "settings",
                "enabled": True,
                "dailyProfitTarget": 100,
                "dailyLossLimit": 300,
                "currency": "THB",
                "timezone": "Asia/Bangkok",
                "resetHour": 0,
            },
        )
        assert saved.status_code == 200
        # The day already has 120 realized, so a 100 target is met the moment it is set.
        assert saved.json()["session"]["status"] == "TARGET_REACHED"
        assert saved.json()["canOpenNewEntry"] is False
        stored = client.get("/api/session-guard/settings").json()
        assert stored["settings"]["dailyProfitTarget"] == 100
        assert stored["enforcing"] is True

    with TestClient(create_app(data_dir=tmp_path)) as reopened:
        again = reopened.get("/api/session-guard/settings").json()
        assert again["settings"]["dailyProfitTarget"] == 100
        assert again["settings"]["dailyLossLimit"] == 300
        assert again["settingsError"] is None


def test_invalid_settings_are_refused_rather_than_corrected(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        seeded(client)
        for payload in (
            {"operation": "settings", "dailyProfitTarget": 0},
            {"operation": "settings", "dailyLossLimit": -1},
            {"operation": "settings", "resetHour": 24},
            {"operation": "settings", "timezone": "Mars/Olympus"},
        ):
            assert client.post("/api/session-guard/command", json=payload).status_code == 422


def test_a_stop_succeeds_even_while_the_engine_is_busy(tmp_path: Path) -> None:
    # A risk control an operator cannot reach when the engine is mid-batch is not a risk
    # control. The stop waits for a clean moment and then takes the one it has.
    with TestClient(create_app(data_dir=tmp_path)) as client:
        engine = seeded(client)
        engine.busy = True
        try:
            stopped = client.post("/api/session-guard/command", json={"operation": "stop"})
        finally:
            engine.busy = False
        assert stopped.status_code == 200
        assert stopped.json()["session"]["status"] == "STOPPED_MANUALLY"
        assert stopped.json()["canOpenNewEntry"] is False
        assert stopped.json()["blockReason"] == "MANUAL_STOP"
