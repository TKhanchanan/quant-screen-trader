"""Append-only SQLite journal. Transactions publish whole records and monotonic events."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import RLock
from typing import TypeVar

from quant_engine.policy.models import Frozen, PolicyEvent

T = TypeVar("T", bound=Frozen)


class PolicyRepository:
    def __init__(self, path: Path | None = None) -> None:
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.db = sqlite3.connect(str(path) if path else ":memory:", check_same_thread=False)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS journal (seq INTEGER PRIMARY KEY, "
            "kind TEXT NOT NULL, id TEXT NOT NULL, at INTEGER NOT NULL, "
            "payload TEXT NOT NULL, UNIQUE(kind,id))"
        )
        self.db.execute(
            "CREATE TRIGGER IF NOT EXISTS no_update BEFORE UPDATE ON journal "
            "BEGIN SELECT RAISE(ABORT, 'append-only journal'); END"
        )
        self.db.execute(
            "CREATE TRIGGER IF NOT EXISTS no_delete BEFORE DELETE ON journal "
            "BEGIN SELECT RAISE(ABORT, 'append-only journal'); END"
        )
        self.db.commit()

    def append(self, kind: str, identifier: object, at: int, value: Frozen) -> None:
        payload = value.model_dump_json()
        with self.lock, self.db:
            existing = self.db.execute(
                "SELECT payload FROM journal WHERE kind=? AND id=?", (kind, str(identifier))
            ).fetchone()
            if existing is not None:
                if existing[0] != payload:
                    raise ValueError("Append-only identity collision")
                return
            if isinstance(value, PolicyEvent):
                last = self.db.execute("SELECT MAX(at) FROM journal WHERE kind='event'").fetchone()
                if last is not None and last[0] is not None and at < last[0]:
                    raise ValueError("Cannot backdate policy history")
            self.db.execute(
                "INSERT INTO journal(kind,id,at,payload) VALUES(?,?,?,?)",
                (kind, str(identifier), at, payload),
            )

    def records(
        self, kind: str, model: type[T], *, at: int | None = None, limit: int | None = None
    ) -> list[T]:
        with self.lock:
            query = "SELECT payload FROM journal WHERE kind=?"
            args: list[object] = [kind]
            if at is not None:
                query += " AND at<=?"
                args.append(at)
            query += " ORDER BY seq DESC"
            if limit is not None:
                query += " LIMIT ?"
                args.append(limit)
            rows = self.db.execute(query, args).fetchall()
        return [model.model_validate_json(row[0]) for row in reversed(rows)]

    def get(self, kind: str, identifier: object, model: type[T]) -> T | None:
        with self.lock:
            row = self.db.execute(
                "SELECT payload FROM journal WHERE kind=? AND id=?", (kind, str(identifier))
            ).fetchone()
        return None if row is None else model.model_validate_json(row[0])

    def close(self) -> None:
        self.db.close()
