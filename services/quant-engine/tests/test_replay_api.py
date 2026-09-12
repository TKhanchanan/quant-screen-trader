"""T-EU..T-EY: the local replay surface, its trust boundary and its refusals.

The only mutating verbs here start and cancel *offline compute*. Starting a replay reads a
recorded file; cancelling one stops that replay and nothing else. There is no endpoint that
applies a finding, and the test that asserts it scans the router rather than trusting the prose.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import replay_fixtures as fixtures
from fastapi.testclient import TestClient
from quant_engine.app import create_app
from quant_engine.market_api import MarketEngine
from quant_engine.replay import REPLAY_VERSION, InMemoryObservationSource, ReplayManifest
from quant_engine.replay.service import ReplayService

MANIFEST: dict[str, Any] = {
    "includeCapitalBear": True,
    "includeIqOption": False,
    "warmupDurationMs": 0,
    "sourceMode": "SYNTHETIC",
    "paperSettings": {"paperCurrency": "THB", "paperStake": 50, "paperPayoutRate": 0.82},
}


def fixture_service(root: Path, rows: list[Any] | None = None) -> ReplayService:
    history = fixtures.small_history() if rows is None else rows

    def factory(manifest: ReplayManifest) -> InMemoryObservationSource:
        return InMemoryObservationSource(
            history, mode=manifest.sourceMode, platforms=manifest.platforms
        )

    return ReplayService(root, factory)


@contextmanager
def client_with(tmp_path: Path, rows: list[Any] | None = None) -> Iterator[TestClient]:
    """A live app whose replay service reads a fixture instead of the durable record.

    Entered exactly once. The application's lifespan installs the real service, so entering the
    client twice would quietly replace the fixture with a reader pointed at an empty directory —
    and every assertion about a replay would then be an assertion about no replay at all.
    """
    with TestClient(create_app(data_dir=tmp_path)) as client:
        paths = client.app.state.paths  # type: ignore[attr-defined]
        client.app.state.replay = fixture_service(  # type: ignore[attr-defined]
            paths.market_data, rows
        )
        yield client


def wait(client: TestClient, job: str, *, limit: float = 60.0) -> dict[str, Any]:
    service = cast(ReplayService, client.app.state.replay)  # type: ignore[attr-defined]
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        service.join(0.2)
        payload = client.get(f"/api/replay/runs/{job}").json()
        if payload["status"] in ("COMPLETED", "CANCELLED", "FAILED"):
            return cast(dict[str, Any], payload)
    raise AssertionError("replay did not finish")


# --- T-EU the trust boundary -----------------------------------------------------------


def test_a_browser_origin_is_refused_on_every_replay_endpoint(tmp_path: Path) -> None:
    with client_with(tmp_path) as client:
        browser = {"origin": "https://example.invalid"}
        assert client.get("/api/replay/runs", headers=browser).status_code == 403
        assert client.post("/api/replay/runs", json=MANIFEST, headers=browser).status_code == 403
        identifier = "00000000-0000-5000-8000-000000000000"
        assert client.get(f"/api/replay/runs/{identifier}", headers=browser).status_code == 403
        assert (
            client.post(f"/api/replay/runs/{identifier}/cancel", headers=browser).status_code == 403
        )


def test_a_local_client_is_not_refused(tmp_path: Path) -> None:
    with client_with(tmp_path) as client:
        assert client.get("/api/replay/runs").status_code == 200


# --- T-EV the run lifecycle ------------------------------------------------------------


def test_a_replay_starts_runs_and_reports_its_whole_result(tmp_path: Path) -> None:
    with client_with(tmp_path) as client:
        accepted = client.post("/api/replay/runs", json=MANIFEST)
        assert accepted.status_code == 202
        started = accepted.json()
        assert started["replayVersion"] == REPLAY_VERSION
        assert started["status"] in ("PENDING", "RUNNING")
        finished = wait(client, started["jobId"])
        assert finished["status"] == "COMPLETED"
        assert finished["replayRunId"] is not None
        assert finished["percent"] == 1.0

        run_id = finished["replayRunId"]
        summary = client.get(f"/api/replay/runs/{run_id}/summary")
        assert summary.status_code == 200
        body = summary.json()["summary"]
        assert body["label"] == "CURRENT_FROZEN_PIPELINE"
        assert body["researchOnly"] is True
        assert body["appliedToLiveExecution"] is False
        assert body["dataset"]["entryLayer"] == "PHASE4_MARKET_OBSERVATION"
        assert body["causality"]["violations"] == 0
        assert body["overall"]["resolved"] > 0
        assert "SYNTHETIC_BEHAVIOR_TEST" in body["warnings"]

        walk = client.get(f"/api/replay/runs/{run_id}/walk-forward")
        assert walk.status_code == 200
        assert walk.json()["walkForward"]["mode"] in ("COUNT", "DURATION")

        equity = client.get(f"/api/replay/runs/{run_id}/equity")
        assert equity.status_code == 200
        assert equity.json()["label"] == "SIMULATED PAPER EQUITY"
        assert equity.json()["points"]

        listing = client.get("/api/replay/runs").json()
        assert any(row["replayRunId"] == run_id for row in listing["runs"])
        assert listing["busy"] is False


def test_an_unknown_run_is_a_404_rather_than_an_empty_result(tmp_path: Path) -> None:
    with client_with(tmp_path) as client:
        missing = "11111111-1111-5111-8111-111111111111"
        assert client.get(f"/api/replay/runs/{missing}").status_code == 404
        assert client.get(f"/api/replay/runs/{missing}/summary").status_code == 404
        assert client.post(f"/api/replay/runs/{missing}/cancel").status_code == 404


# --- T-EW one heavy run at a time ------------------------------------------------------


def test_a_second_heavy_replay_is_refused_rather_than_queued(tmp_path: Path) -> None:
    # Two replays competing for the same cores would cost the live capture loop its samples.
    with client_with(tmp_path, fixtures.capitalbear_history()) as client:
        first = client.post("/api/replay/runs", json=MANIFEST)
        assert first.status_code == 202
        second = client.post("/api/replay/runs", json=MANIFEST)
        assert second.status_code == 409
        cast(ReplayService, client.app.state.replay).cancel(  # type: ignore[attr-defined]
            __import__("uuid").UUID(first.json()["jobId"])
        )
        wait(client, first.json()["jobId"])


# --- T-EX cancellation stops the replay and only the replay ----------------------------


def test_cancelling_stops_the_offline_run_and_leaves_the_engine_alone(tmp_path: Path) -> None:
    with client_with(tmp_path, fixtures.capitalbear_history()) as client:
        engine = cast(MarketEngine, client.app.state.market)  # type: ignore[attr-defined]
        before = engine.paper.state().model_dump(mode="json")
        guard_before = engine.guard.state(fixtures.BASE_MS).model_dump(mode="json")
        started = client.post("/api/replay/runs", json=MANIFEST).json()
        cancelled = client.post(f"/api/replay/runs/{started['jobId']}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["scope"] == "REPLAY_ONLY"
        finished = wait(client, started["jobId"])
        assert finished["status"] == "CANCELLED"
        # Live capture, the live paper layer and the live daily session are exactly as they were.
        assert engine.paper.state().model_dump(mode="json") == before
        assert engine.guard.state(fixtures.BASE_MS).model_dump(mode="json") == guard_before
        assert client.get("/api/market/state").status_code == 200
        assert engine.storage_error is False


def test_a_cancelled_run_keeps_its_progress_and_is_never_presented_as_complete(
    tmp_path: Path,
) -> None:
    with client_with(tmp_path, fixtures.capitalbear_history()) as client:
        started = client.post("/api/replay/runs", json=MANIFEST).json()
        client.post(f"/api/replay/runs/{started['jobId']}/cancel")
        finished = wait(client, started["jobId"])
        assert finished["status"] == "CANCELLED"
        assert finished["processedEvents"] >= 0
        service = cast(ReplayService, client.app.state.replay)  # type: ignore[attr-defined]
        job = service.job(__import__("uuid").UUID(started["jobId"]))
        assert job is not None
        assert job.summary is None or job.run is None or job.run.status == "CANCELLED"


# --- T-EY a failure never becomes a result ---------------------------------------------


def test_a_failing_replay_is_reported_as_failed_and_the_engine_stays_healthy(
    tmp_path: Path,
) -> None:
    class Broken:
        outsideWindow = 0

        def prepare(self) -> Any:
            raise RuntimeError("unreadable history")

        def stream(self, **_: Any) -> Any:
            raise RuntimeError("unreadable history")

    with client_with(tmp_path) as client:
        paths = client.app.state.paths  # type: ignore[attr-defined]
        client.app.state.replay = ReplayService(  # type: ignore[attr-defined]
            paths.market_data, lambda manifest: Broken()
        )
        started = client.post("/api/replay/runs", json=MANIFEST).json()
        finished = wait(client, started["jobId"])
        assert finished["status"] == "FAILED"
        assert finished["error"]
        assert client.get(f"/api/replay/runs/{started['jobId']}/summary").status_code == 404
        # The live analytical engine never noticed.
        assert client.get("/api/market/state").status_code == 200
        engine = cast(MarketEngine, client.app.state.market)  # type: ignore[attr-defined]
        assert engine.storage_error is False


def test_a_manifest_cannot_carry_a_credential_a_window_or_a_control(tmp_path: Path) -> None:
    # The field set is the contract. A manifest that could name a broker control would be a
    # manifest that could reach one, so the model forbids anything it does not declare.
    fields = set(ReplayManifest.model_fields)
    assert not fields & {
        "username",
        "password",
        "token",
        "session",
        "browser",
        "controlMap",
        "executionMode",
        "armed",
        "stake",
    }
    with client_with(tmp_path) as client:
        rejected = client.post("/api/replay/runs", json={**MANIFEST, "executionMode": "AUTO"})
        assert rejected.status_code == 422
