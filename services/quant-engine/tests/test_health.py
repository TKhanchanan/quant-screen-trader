from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quant_engine.app import create_app


def assert_healthy_message(message: dict[str, object], sequence: int) -> None:
    assert message["type"] == "health"
    assert message["service"] == "quant-engine"
    assert message["version"] == "0.1.0"
    assert message["status"] == "ok"
    assert message["database"] == "ok"
    assert message["sequence"] == sequence
    timestamp = message["timestamp"]
    assert isinstance(timestamp, str)
    assert datetime.fromisoformat(timestamp).tzinfo is not None


def test_http_health_initializes_database(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path)

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert_healthy_message(response.json(), sequence=0)
    assert (tmp_path / "db" / "quant-screen-trader.sqlite3").is_file()


def test_websocket_streams_typed_sequenced_heartbeats(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path, heartbeat_interval_seconds=0.001)

    with TestClient(app) as client, client.websocket_connect("/ws/health") as websocket:
        assert_healthy_message(websocket.receive_json(), sequence=0)
        assert_healthy_message(websocket.receive_json(), sequence=1)


def test_rejects_nonpositive_heartbeat_interval() -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        create_app(heartbeat_interval_seconds=0)
