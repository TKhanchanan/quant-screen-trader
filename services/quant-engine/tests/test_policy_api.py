"""Local-only lifecycle and durable SHADOW default."""

from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient
from quant_engine.app import create_app
from quant_engine.market_api import MarketEngine

GETS = ("state", "active", "history", "decisions", "watchdog")


def test_api_rebuild_activation_and_restart(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/policy/state").json()["mode"] == "SHADOW"
        response = client.post("/api/policy/rebuild", json={})
        assert response.status_code == 200, response.text
        snapshot = response.json()
        assert snapshot["status"] == "NO_EVIDENCE" and not snapshot["rules"]
        assert client.get("/api/policy/active").json() is None
        body = {"snapshotId": snapshot["snapshotId"]}
        assert client.post("/api/policy/activate-paper", json=body).status_code == 422
        assert client.post("/api/policy/activate-shadow", json=body).status_code == 200
        assert client.get("/api/policy/state").json()["snapshotId"] == snapshot["snapshotId"]
        assert client.post("/api/policy/deactivate").status_code == 200
        for path in ("apply-live", "execute", "auto-execute"):
            assert client.post("/api/policy/" + path).status_code == 404
    with TestClient(create_app(data_dir=tmp_path)) as client:
        assert client.get("/api/policy/state").json()["mode"] == "OFF"
        assert len(client.get("/api/policy/history").json()) == 3


@pytest.mark.parametrize(
    "headers", [{"origin": "https://example.test"}, {"sec-fetch-site": "same-origin"}]
)
def test_api_browser_forbidden(tmp_path: Path, headers: dict[str, str]) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        for suffix in GETS:
            assert client.get("/api/policy/" + suffix, headers=headers).status_code == 403
        assert client.post("/api/policy/rebuild", json={}, headers=headers).status_code == 403


def test_remote_and_busy_requests_refused(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path)
    with TestClient(app, client=("192.0.2.1", 1234)) as client:
        assert client.get("/api/policy/state").status_code == 403
    with TestClient(create_app(data_dir=tmp_path)) as client:
        market = cast(MarketEngine, client.app.state.market)  # type: ignore[attr-defined]
        market.policy.busy = True
        assert client.post("/api/policy/rebuild", json={}).status_code == 409
        assert client.get("/api/policy/state").status_code == 429
        market.policy.busy = False
        assert (
            client.post("/api/policy/rebuild", json={"settings": {"mode": "LIVE_REAL"}}).status_code
            == 422
        )
