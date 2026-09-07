"""SQLite connection setup and versioned migration runner."""

from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path

BUSY_TIMEOUT_MILLISECONDS = 5_000
MIGRATION_FILES = ("0001_initial.sql",)
MIGRATIONS_PACKAGE = "quant_engine.storage.migrations"


def connect_database(database_path: Path) -> sqlite3.Connection:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=BUSY_TIMEOUT_MILLISECONDS / 1_000)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MILLISECONDS}")
        journal_mode_row = connection.execute("PRAGMA journal_mode = WAL").fetchone()
        if journal_mode_row is None:
            raise RuntimeError("SQLite did not report a journal mode")
        journal_mode = journal_mode_row[0]
        if str(journal_mode).lower() != "wal":
            raise RuntimeError(f"SQLite refused WAL mode: {journal_mode}")
    except Exception:
        connection.close()
        raise
    return connection


def _migration_version(file_name: str) -> int:
    prefix, separator, _name = file_name.partition("_")
    if not separator or not prefix.isdigit():
        raise ValueError(f"invalid migration file name: {file_name}")
    return int(prefix)


def apply_migrations(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.commit()

    applied_versions = {
        int(row[0]) for row in connection.execute("SELECT version FROM schema_migrations")
    }
    migration_root = resources.files(MIGRATIONS_PACKAGE)

    for file_name in MIGRATION_FILES:
        version = _migration_version(file_name)
        if version in applied_versions:
            continue

        sql = migration_root.joinpath(file_name).read_text(encoding="utf-8")
        escaped_name = file_name.replace("'", "''")
        script = (
            "BEGIN IMMEDIATE;\n"
            f"{sql}\n"
            "INSERT INTO schema_migrations (version, name) "
            f"VALUES ({version}, '{escaped_name}');\n"
            "COMMIT;"
        )
        try:
            connection.executescript(script)
        except sqlite3.Error:
            if connection.in_transaction:
                connection.rollback()
            raise


def initialize_database(database_path: Path) -> None:
    connection = connect_database(database_path)
    try:
        apply_migrations(connection)
    finally:
        connection.close()


def database_is_healthy(database_path: Path) -> bool:
    if not database_path.is_file():
        return False

    connection: sqlite3.Connection | None = None
    try:
        uri = f"{database_path.resolve().as_uri()}?mode=rw"
        connection = sqlite3.connect(uri, uri=True, timeout=1)
        latest_migration = connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()
        expected_version = _migration_version(MIGRATION_FILES[-1])
        return latest_migration is not None and latest_migration[0] == expected_version
    except sqlite3.Error:
        return False
    finally:
        if connection is not None:
            connection.close()
