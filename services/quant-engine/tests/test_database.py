from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from quant_engine.storage.database import connect_database, initialize_database


def test_migration_is_idempotent_and_enables_sqlite_safety(tmp_path: Path) -> None:
    database_path = tmp_path / "db" / "engine.sqlite3"

    initialize_database(database_path)
    initialize_database(database_path)

    connection = connect_database(database_path)
    try:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()
        busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()
        assert journal_mode is not None and journal_mode[0] == "wal"
        assert foreign_keys is not None and foreign_keys[0] == 1
        assert busy_timeout is not None and busy_timeout[0] == 5_000
        assert [
            row[0] for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
        ] == [1]

        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert {
            "app_settings",
            "health_events",
            "schema_migrations",
            "slot_profiles",
            "workspace_profiles",
        } <= tables

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO slot_profiles (workspace_id, slot_number) VALUES (999, 1)"
            )
    finally:
        connection.close()
