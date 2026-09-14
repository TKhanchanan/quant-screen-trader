"""Explicit read-only storage verification, outside the live ingestion hot path."""

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from quant_engine.market_storage import MODELS
from quant_engine.shadow_live import ShadowLiveRecorder


def verify_storage(recorder: ShadowLiveRecorder, root: Path) -> dict[str, Any]:
    result: dict[str, Any] = dict(
        sqlite=[], parquetFiles=0, parquetRows=0, parquetBytes=0, corruption=False, complete=True
    )
    try:
        for path in (
            root / "db" / "quant-screen-trader.sqlite3",
            root / "market-data" / "policy" / "journal.sqlite3",
        ):
            with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=2)) as db:
                okay = db.execute("PRAGMA quick_check").fetchall() == [("ok",)]
                result["sqlite"].append({"database": path.name, "healthy": okay})
                result["corruption"] |= not okay
        # One file and 256 rows at a time. Never concatenate historical Parquet into memory.
        with duckdb.connect() as db:
            for category, model in MODELS.items():
                for path in (root / "market-data" / category).rglob("*.parquet"):
                    if path.stat().st_mtime_ns // 1_000_000 < recorder.data["startedAt"]:
                        continue
                    if result["parquetFiles"] >= 10000:
                        result["complete"] = False
                        break
                    result["parquetFiles"] += 1
                    result["parquetBytes"] += path.stat().st_size
                    cursor = (
                        db.read_parquet(str(path), hive_partitioning=False)
                        .set_alias("r")
                        .project("to_json(r)")
                        .execute()
                    )
                    while rows := cursor.fetchmany(256):
                        for row in rows:
                            value = json.loads(row[0])
                            # Parquet stores naïve UTC timestamps; reuse the canonical reload convention.
                            for field in ("observedAt", "parsedAt"):
                                if field in value:
                                    stamp = datetime.fromisoformat(value[field])
                                    value[field] = (
                                        stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp
                                    )
                            model.model_validate(value)
                            result["parquetRows"] += 1
    except (OSError, ValueError, sqlite3.Error, duckdb.Error):
        result["complete"] = False
        result["corruption"] = None
        recorder.error("STORAGE_VERIFICATION_FAILED")
    with recorder.lock:
        recorder.data["storageAudit"] = result
        recorder.data["storageVerified"] = result["complete"] and result["corruption"] is False
        recorder.data["storageCorruption"] = (
            True if recorder.data["storageCorruption"] is True else result["corruption"]
        )
    return result
