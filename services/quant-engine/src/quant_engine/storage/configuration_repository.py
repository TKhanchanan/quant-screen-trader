"""Atomic, parameterized SQLite configuration operations."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from quant_engine.configuration import (
    CalibrationRequest,
    ConfigurationRequest,
    Platform,
    PresetRequest,
    RecordRequest,
    Slot,
    SlotsRequest,
    SyncSlotsRequest,
)
from quant_engine.storage.database import connect_database


def _workspace(db: sqlite3.Connection, platform: Platform) -> int:
    # Preserve any Phase 1 data: use its first workspace, create only if absent.
    row = db.execute(
        "SELECT id FROM workspace_profiles WHERE platform=? ORDER BY id LIMIT 1", (platform,)
    ).fetchone()
    if row is None:
        cursor = db.execute(
            "INSERT INTO workspace_profiles(platform,name) VALUES (?, 'Default')", (platform,)
        )
        workspace = int(cursor.lastrowid or 0)
    else:
        workspace = int(row[0])
    for number in range(1, 10):
        db.execute(
            "INSERT OR IGNORE INTO slot_profiles(workspace_id,slot_number,enabled,asset_name) "
            "VALUES (?,?,0,'')",
            (workspace, number),
        )
    return workspace


def _save_slots(db: sqlite3.Connection, workspace: int, slots: list[Slot]) -> None:
    db.executemany(
        "UPDATE slot_profiles SET asset_name=?,display_name=?,enabled=?,asset_mode=?,"
        "updated_at=CURRENT_TIMESTAMP WHERE workspace_id=? AND slot_number=?",
        [(s.assetName, s.displayName, s.enabled, s.assetMode, workspace, s.id) for s in slots],
    )


def _assets(rows: list[sqlite3.Row], platform: Platform) -> list[dict[str, Any]]:
    return [
        dict(
            id=r["slot_number"],
            platform=platform,
            assetName=r["asset_name"],
            assetMode=r["asset_mode"],
            enabled=bool(r["enabled"]),
            **({"displayName": r["display_name"]} if r["display_name"] is not None else {}),
        )
        for r in rows
    ]


def _snapshot(db: sqlite3.Connection, platform: Platform, workspace: int) -> dict[str, Any]:
    presets = []
    for row in db.execute(
        "SELECT * FROM asset_presets WHERE platform=? ORDER BY name,id", (platform,)
    ):
        slots = _assets(
            db.execute(
                "SELECT * FROM asset_preset_slots WHERE preset_id=? ORDER BY slot_number",
                (row["id"],),
            ).fetchall(),
            platform,
        )
        presets.append(
            dict(
                id=row["id"],
                platform=platform,
                name=row["name"],
                slots=slots,
                createdAt=row["created_at"],
                updatedAt=row["updated_at"],
            )
        )
    profiles = []
    for row in db.execute(
        "SELECT * FROM calibration_profiles WHERE platform=? ORDER BY name,id", (platform,)
    ):
        bounds = [
            dict(id=s["slot_number"], bounds={k: s[k] for k in ("x", "y", "width", "height")})
            for s in db.execute(
                "SELECT * FROM calibration_slots WHERE profile_id=? ORDER BY slot_number",
                (row["id"],),
            )
        ]
        profiles.append(
            dict(
                id=row["id"],
                platform=platform,
                name=row["name"],
                slots=bounds,
                referenceBrowserWidth=row["reference_width"],
                referenceBrowserHeight=row["reference_height"],
                zoomFactor=row["zoom_factor"],
                geometrySource=row["geometry_source"],
                createdAt=row["created_at"],
                updatedAt=row["updated_at"],
            )
        )
    active = db.execute(
        "SELECT profile_id FROM active_calibrations WHERE platform=?", (platform,)
    ).fetchone()
    return dict(
        configuration=dict(
            platform=platform,
            slots=_assets(
                db.execute(
                    "SELECT * FROM slot_profiles WHERE workspace_id=? ORDER BY slot_number",
                    (workspace,),
                ).fetchall(),
                platform,
            ),
        ),
        presets=presets,
        calibrations=profiles,
        activeCalibrationId=active[0] if active else None,
    )


def execute_configuration(path: Path, request: ConfigurationRequest) -> dict[str, Any]:
    with closing(connect_database(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        workspace = _workspace(db, request.platform)
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        if isinstance(request, SyncSlotsRequest):
            before = _snapshot(db, request.platform, workspace)
            current = [Slot.model_validate(s) for s in before["configuration"]["slots"]]
            profile = next(
                (p for p in before["calibrations"] if p["id"] == before["activeCalibrationId"]),
                None,
            )
            version = f"{profile['id']}:{profile['updatedAt']}" if profile else None
            if current != request.expectedSlots or version != request.expectedCalibrationVersion:
                raise LookupError("Configuration changed during asset detection")
            proposed = {s.id: s for s in request.slots}
            _save_slots(
                db,
                workspace,
                [
                    s
                    if s.assetMode == "MANUAL"
                    else proposed[s.id].model_copy(update={"assetMode": "AUTO"})
                    for s in current
                ],
            )
        elif isinstance(request, SlotsRequest):
            modes = {
                row["slot_number"]: row["asset_mode"]
                for row in db.execute(
                    "SELECT slot_number,asset_mode FROM slot_profiles WHERE workspace_id=?",
                    (workspace,),
                )
            }
            _save_slots(
                db,
                workspace,
                [
                    slot
                    if "assetMode" in slot.model_fields_set
                    else slot.model_copy(update={"assetMode": modes[slot.id]})
                    for slot in request.slots
                ],
            )
        elif isinstance(request, (PresetRequest, CalibrationRequest)):
            preset = isinstance(request, PresetRequest)
            # Table names below are internal constants, never user input.
            table = "asset_presets" if preset else "calibration_profiles"
            record_id = str(request.id or uuid4())
            if request.id:
                if not db.execute(
                    f"SELECT 1 FROM {table} WHERE id=? AND platform=?",
                    (record_id, request.platform),
                ).fetchone():
                    raise LookupError("Configuration not found for this platform")
                db.execute(
                    f"UPDATE {table} SET name=?,updated_at=? WHERE id=?",
                    (request.name, now, record_id),
                )
            elif isinstance(request, PresetRequest):
                db.execute(
                    "INSERT INTO asset_presets VALUES (?,?,?,?,?)",
                    (record_id, request.platform, request.name, now, now),
                )
            else:
                db.execute(
                    "INSERT INTO calibration_profiles VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        record_id,
                        request.platform,
                        request.name,
                        request.referenceBrowserWidth,
                        request.referenceBrowserHeight,
                        request.zoomFactor,
                        now,
                        now,
                        request.geometrySource,
                    ),
                )
            if isinstance(request, PresetRequest):
                db.execute("DELETE FROM asset_preset_slots WHERE preset_id=?", (record_id,))
                db.executemany(
                    "INSERT INTO asset_preset_slots VALUES (?,?,?,?,?,?)",
                    [
                        (record_id, s.id, s.assetName, s.displayName, s.enabled, s.assetMode)
                        for s in request.slots
                    ],
                )
            else:
                db.execute(
                    "UPDATE calibration_profiles SET reference_width=?,reference_height=?,zoom_factor=?,geometry_source=? WHERE id=?",
                    (
                        request.referenceBrowserWidth,
                        request.referenceBrowserHeight,
                        request.zoomFactor,
                        request.geometrySource,
                        record_id,
                    ),
                )
                db.execute("DELETE FROM calibration_slots WHERE profile_id=?", (record_id,))
                db.executemany(
                    "INSERT INTO calibration_slots VALUES (?,?,?,?,?,?)",
                    [
                        (record_id, s.id, s.bounds.x, s.bounds.y, s.bounds.width, s.bounds.height)
                        for s in request.slots
                    ],
                )
                db.execute(
                    "INSERT INTO active_calibrations VALUES (?,?) ON CONFLICT(platform) DO UPDATE SET profile_id=excluded.profile_id",
                    (request.platform, record_id),
                )
        elif isinstance(request, RecordRequest):
            record_id = str(request.id)
            preset = request.operation.endswith("Preset")
            table = "asset_presets" if preset else "calibration_profiles"
            if not db.execute(
                f"SELECT 1 FROM {table} WHERE id=? AND platform=?", (record_id, request.platform)
            ).fetchone():
                raise LookupError("Configuration not found for this platform")
            if request.operation.startswith("delete"):
                db.execute(f"DELETE FROM {table} WHERE id=?", (record_id,))
            elif preset:
                slots = _assets(
                    db.execute(
                        "SELECT * FROM asset_preset_slots WHERE preset_id=? ORDER BY slot_number",
                        (record_id,),
                    ).fetchall(),
                    request.platform,
                )
                _save_slots(db, workspace, [Slot.model_validate(s) for s in slots])
            else:
                db.execute(
                    "INSERT INTO active_calibrations VALUES (?,?) ON CONFLICT(platform) DO UPDATE SET profile_id=excluded.profile_id",
                    (request.platform, record_id),
                )
        return _snapshot(db, request.platform, workspace)
