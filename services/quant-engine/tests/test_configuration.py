from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from quant_engine.app import create_app
from quant_engine.storage.database import initialize_database


def assets(platform: str = "capitalbear") -> list[dict[str, Any]]:
    return [
        dict(id=i, platform=platform, enabled=i != 3, assetName=f"User asset {i}")
        for i in range(1, 10)
    ]


def geometry() -> list[dict[str, Any]]:
    return [
        dict(id=i + 1, bounds=dict(x=(i % 3) / 3, y=(i // 3) / 3, width=1 / 3, height=1 / 3))
        for i in range(9)
    ]


def command(
    client: TestClient, operation: str, platform: str = "capitalbear", **payload: Any
) -> dict[str, Any]:
    response = client.post(
        f"/api/workspaces/{platform}/configuration",
        json=dict(operation=operation, platform=platform, **payload),
    )
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def test_assets_presets_and_restart(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        assert len(command(client, "get")["configuration"]["slots"]) == 9
        slots = assets()
        slots[0]["assetName"] = "Gold / Bitcoin / Apple OTC"
        command(client, "slots", slots=slots)
        result = command(client, "savePreset", name="Morning", slots=slots)
        preset = result["presets"][0]
        record_id = preset["id"]
        assert preset["slots"][2]["enabled"] is False
        renamed = command(client, "savePreset", id=record_id, name="Night", slots=slots)
        assert renamed["presets"][0]["name"] == "Night"
        duplicate = command(client, "savePreset", name="Night copy", slots=slots)
        assert len(duplicate["presets"]) == 2
        slots[0]["assetName"] = "Changed"
        command(client, "slots", slots=slots)
        loaded = command(client, "loadPreset", id=record_id)
        assert loaded["configuration"]["slots"][0]["assetName"] == "Gold / Bitcoin / Apple OTC"
        assert command(client, "get", "iqoption")["presets"] == []
        forbidden = client.post(
            "/api/workspaces/iqoption/configuration",
            json=dict(operation="loadPreset", platform="iqoption", id=record_id),
        )
        assert forbidden.status_code == 404
        command(client, "deletePreset", id=record_id)
        assert len(command(client, "get")["presets"]) == 1
    with TestClient(create_app(data_dir=tmp_path)) as client:
        assert (
            command(client, "get")["configuration"]["slots"][0]["assetName"]
            == "Gold / Bitcoin / Apple OTC"
        )


def test_calibration_crud_load_cascade_and_isolation(tmp_path: Path) -> None:
    payload: dict[str, Any] = dict(
        name="Monitor",
        referenceBrowserWidth=1320,
        referenceBrowserHeight=900,
        zoomFactor=1,
        slots=geometry(),
    )
    with TestClient(create_app(data_dir=tmp_path)) as client:
        result = command(client, "saveCalibration", **payload)
        record_id = result["activeCalibrationId"]
        assert result["calibrations"][0]["slots"] == geometry()
        payload["name"] = "Renamed"
        command(client, "saveCalibration", id=record_id, **payload)
        command(client, "saveCalibration", **payload)
        loaded = command(client, "loadCalibration", id=record_id)
        assert loaded["activeCalibrationId"] == record_id
        assert len(loaded["calibrations"]) == 2
        assert command(client, "get", "iqoption")["calibrations"] == []
        response = client.post(
            "/api/workspaces/iqoption/configuration",
            json=dict(operation="deleteCalibration", platform="iqoption", id=record_id),
        )
        assert response.status_code == 404
    with TestClient(create_app(data_dir=tmp_path)) as client:
        assert command(client, "get")["activeCalibrationId"] == record_id
        deleted = command(client, "deleteCalibration", id=record_id)
        assert deleted["activeCalibrationId"] is None
    with sqlite3.connect(tmp_path / "db" / "quant-screen-trader.sqlite3") as db:
        assert (
            db.execute(
                "SELECT COUNT(*) FROM calibration_slots WHERE profile_id=?", (record_id,)
            ).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize("bad", ["missing", "duplicate", "platform", "blank", "outside"])
def test_invalid_slots_are_atomic(tmp_path: Path, bad: str) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        original = command(client, "get")
        slots = assets()
        if bad == "missing":
            slots.pop()
        if bad == "duplicate":
            slots[0]["id"] = 2
        if bad == "platform":
            slots[0]["platform"] = "iqoption"
        if bad == "blank":
            slots[0]["assetName"] = " "
        if bad == "outside":
            slots[0]["id"] = 10
        response = client.post(
            "/api/workspaces/capitalbear/configuration",
            json=dict(operation="slots", platform="capitalbear", slots=slots),
        )
        assert response.status_code == 422
        assert command(client, "get") == original


@pytest.mark.parametrize(
    "patch", [dict(x=-0.1), dict(y=-0.1), dict(width=0), dict(height=0), dict(x=0.9), dict(y=0.9)]
)
def test_invalid_calibration_bounds(tmp_path: Path, patch: dict[str, float]) -> None:
    slots = geometry()
    slots[0]["bounds"].update(patch)
    with TestClient(create_app(data_dir=tmp_path)) as client:
        response = client.post(
            "/api/workspaces/capitalbear/configuration",
            json=dict(
                operation="saveCalibration",
                platform="capitalbear",
                name="Bad",
                referenceBrowserWidth=800,
                referenceBrowserHeight=600,
                zoomFactor=1,
                slots=slots,
            ),
        )
        assert response.status_code == 422
        assert command(client, "get")["calibrations"] == []


def test_rejects_browser_origin_and_unknown_platform(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        url = "/api/workspaces/capitalbear/configuration"
        for origin in ["https://capitalbear.com", "null"]:
            assert (
                client.post(
                    url,
                    headers={"origin": origin},
                    json=dict(operation="get", platform="capitalbear"),
                ).status_code
                == 403
            )
        assert client.post(url, json=dict(operation="get", platform="invalid")).status_code == 422


def test_upgrade_phase_one_preserves_existing_data(tmp_path: Path) -> None:
    path = tmp_path / "upgrade.sqlite3"
    with sqlite3.connect(path) as db:
        sql = (
            resources.files("quant_engine.storage.migrations")
            .joinpath("0001_initial.sql")
            .read_text()
        )
        db.executescript(sql)
        db.execute(
            "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,name TEXT UNIQUE,applied_at TEXT)"
        )
        db.execute("INSERT INTO schema_migrations VALUES (1,'0001_initial.sql','2026-01-01')")
        db.execute(
            "INSERT INTO workspace_profiles(id,platform,name) VALUES (1,'capitalbear','Existing')"
        )
        db.execute(
            "INSERT INTO slot_profiles(workspace_id,slot_number,asset_name) VALUES (1,1,'Existing asset')"
        )
    initialize_database(path)
    initialize_database(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT asset_name,asset_mode FROM slot_profiles").fetchone() == (
            "Existing asset",
            "MANUAL",
        )
        assert db.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 4


def test_upgrade_migrates_only_the_legacy_full_browser_grid(tmp_path: Path) -> None:
    path = tmp_path / "grid-upgrade.sqlite3"
    initialize_database(path)
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM schema_migrations WHERE version = 4")
        db.execute("INSERT INTO calibration_profiles VALUES ('legacy','capitalbear','Legacy',900,600,1,'x','x')")
        db.execute("INSERT INTO calibration_profiles VALUES ('manual','capitalbear','Manual',900,600,1,'x','x')")
        for slot in range(1, 10):
            column, row = (slot - 1) % 3, (slot - 1) // 3
            db.execute("INSERT INTO calibration_slots VALUES (?,?,?,?,?,?)",
                       ('legacy', slot, column / 3, row / 3, 1 / 3, 1 / 3))
            db.execute("INSERT INTO calibration_slots VALUES (?,?,?,?,?,?)",
                       ('manual', slot, .06 + column * .3, .13 + row * .25, .3, .25))
        migration = resources.files("quant_engine.storage.migrations").joinpath("0004_inner_chart_grid.sql").read_text()
        db.executescript(migration)
        legacy = db.execute("SELECT x,y,width,height FROM calibration_slots WHERE profile_id='legacy' ORDER BY slot_number").fetchall()
        manual = db.execute("SELECT x,y,width,height FROM calibration_slots WHERE profile_id='manual' ORDER BY slot_number").fetchall()
        assert legacy[0] == pytest.approx((.05, .12, .95 / 3, .78 / 3))
        assert legacy[8] == pytest.approx((.05 + 2 * .95 / 3, .12 + 2 * .78 / 3, .95 / 3, .78 / 3))
        assert manual[0] == pytest.approx((.06, .13, .3, .25))


def test_asset_sync_compare_and_swap_and_manual_presets(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        slots = assets()
        slots[0]["assetMode"] = "MANUAL"
        before = command(client, "slots", slots=slots)
        expected = before["configuration"]["slots"]
        desired = [dict(s, assetName="EUR/USD OTC", enabled=True) for s in expected]
        result = command(
            client,
            "syncAssets",
            slots=desired,
            expectedSlots=expected,
            expectedCalibrationVersion=None,
        )
        assert result["configuration"]["slots"][0]["assetName"] == slots[0]["assetName"]
        assert result["configuration"]["slots"][1]["assetName"] == "EUR/USD OTC"
        stale = client.post(
            "/api/workspaces/capitalbear/configuration",
            json=dict(
                platform="capitalbear",
                operation="syncAssets",
                slots=desired,
                expectedSlots=expected,
                expectedCalibrationVersion=None,
            ),
        )
        assert stale.status_code == 404
        saved = command(client, "savePreset", name="Locks", slots=result["configuration"]["slots"])
        command(client, "slots", slots=assets())
        loaded = command(client, "loadPreset", id=saved["presets"][0]["id"])
        assert loaded["configuration"]["slots"][0]["assetMode"] == "MANUAL"
        assert loaded["configuration"]["slots"][1]["assetMode"] == "AUTO"
        assert all(
            s["assetName"] == ""
            for s in command(client, "get", "iqoption")["configuration"]["slots"]
        )


def test_legacy_slots_preserve_persisted_asset_modes(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        current = assets()
        current[0]["assetMode"] = "MANUAL"
        command(client, "slots", slots=current)

        legacy = assets()
        legacy[0]["assetName"] = "Legacy rename"
        saved = command(client, "slots", slots=legacy)["configuration"]["slots"]
        assert saved[0]["assetName"] == "Legacy rename"
        assert saved[0]["assetMode"] == "MANUAL"
        assert all(slot["assetMode"] == "AUTO" for slot in saved[1:])

        legacy[0]["assetMode"] = "AUTO"
        unlocked = command(client, "slots", slots=legacy)["configuration"]["slots"]
        assert unlocked[0]["assetMode"] == "AUTO"


def test_asset_sync_rejects_changed_calibration(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        before = command(client, "get")
        command(
            client,
            "saveCalibration",
            name="New ROI",
            referenceBrowserWidth=900,
            referenceBrowserHeight=600,
            zoomFactor=1,
            slots=geometry(),
        )
        response = client.post(
            "/api/workspaces/capitalbear/configuration",
            json=dict(
                operation="syncAssets",
                platform="capitalbear",
                slots=assets(),
                expectedSlots=before["configuration"]["slots"],
                expectedCalibrationVersion=None,
            ),
        )
        assert response.status_code == 404
        assert not any(s["enabled"] for s in command(client, "get")["configuration"]["slots"])
