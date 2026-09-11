"""Durable operator settings for the daily session guard.

One row for the whole installation, stored through the same local SQLite database and the same
parameterized access pattern as every other piece of configuration. There is no other write
path: the HTTP surface that reaches this sits behind the project's local-user trust boundary
and refuses browser origins, exactly as the workspace configuration endpoint does.
"""

from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path

from quant_engine.session_guard.settings import SessionGuardSettings
from quant_engine.storage.database import connect_database


def load_settings(database_path: Path) -> tuple[SessionGuardSettings, str | None]:
    """Stored settings, or safe defaults plus the reason they could not be read.

    Unreadable settings must not stop the engine, and they must not silently enforce a limit
    nobody can see either. The guard falls back to disabled defaults — which refuse nothing —
    and the reason is reported on the state endpoint so the operator can fix it.
    """
    try:
        with closing(connect_database(database_path)) as database:
            row = database.execute(
                "SELECT settings_json FROM session_guard_settings WHERE id = 1"
            ).fetchone()
    except Exception as error:  # a corrupt or missing table is reported, never guessed at
        return SessionGuardSettings(), f"Session guard settings unreadable: {error}"[:200]
    if row is None:
        return SessionGuardSettings(), None
    try:
        return SessionGuardSettings.model_validate(json.loads(row[0])), None
    except ValueError as error:
        return SessionGuardSettings(), f"Stored session guard settings are invalid: {error}"[:200]


def save_settings(database_path: Path, settings: SessionGuardSettings) -> SessionGuardSettings:
    """Replace the single settings row. Validation has already happened in the model."""
    payload = json.dumps(settings.model_dump(mode="json"))
    with closing(connect_database(database_path)) as database:
        with database:
            database.execute(
                "INSERT INTO session_guard_settings(id, settings_json, updated_at) "
                "VALUES (1, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(id) DO UPDATE SET settings_json=excluded.settings_json, "
                "updated_at=CURRENT_TIMESTAMP",
                (payload,),
            )
    return settings
