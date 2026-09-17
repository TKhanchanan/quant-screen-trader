"""Explicit read-only storage verification, outside the live ingestion hot path."""

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from quant_engine.market_storage import MODELS, Category
from quant_engine.shadow_live import ShadowLiveRecorder

FILES_PER_CALL = 10_000
"""The bound on one audit call. A day of live capture writes far more Parquet files than this,
so the audit resumes where the previous call stopped instead of declaring the rest verified."""


def verify_storage(recorder: ShadowLiveRecorder, root: Path) -> dict[str, Any]:
    previous = recorder.data.get("storageAudit") or {}
    cursor = previous.get("cursor") if previous.get("resumable") else None
    # A read failure keeps its cursor, so the failing file is tried again; corruption never resets.
    carried = cursor is not None and previous.get("corruption") is not True
    result: dict[str, Any] = dict(
        sqlite=[],
        parquetFiles=previous.get("parquetFiles", 0) if carried else 0,
        parquetRows=previous.get("parquetRows", 0) if carried else 0,
        parquetBytes=previous.get("parquetBytes", 0) if carried else 0,
        corruption=False,
        complete=False,
        resumable=True,
        callFiles=0,
        filesInScope=0,
        remainingFiles=None,
        cursor=cursor if carried else None,
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
        # Files written since the run started, in a stable (modification time, path) order so
        # a later call can continue exactly after the last file this one verified.
        scope: list[tuple[int, str, Category]] = []
        for category in MODELS:
            for path in (root / "market-data" / category).rglob("*.parquet"):
                modified = path.stat().st_mtime_ns
                if modified // 1_000_000 >= recorder.data["startedAt"]:
                    scope.append((modified, path.relative_to(root).as_posix(), category))
        scope.sort()
        result["filesInScope"] = len(scope)
        start = 0
        if result["cursor"] is not None:
            after = (int(result["cursor"][0]), str(result["cursor"][1]))
            start = next((i for i, (m, p, _) in enumerate(scope) if (m, p) > after), len(scope))
        verified = start
        # One file and 256 rows at a time. Never concatenate historical Parquet into memory.
        with duckdb.connect() as db:
            for modified, relative, category in scope[start:]:
                if result["callFiles"] >= FILES_PER_CALL:
                    break
                path = root / relative
                reader = (
                    db.read_parquet(str(path), hive_partitioning=False)
                    .set_alias("r")
                    .project("to_json(r)")
                    .execute()
                )
                rows = 0
                while batch := reader.fetchmany(256):
                    for row in batch:
                        value = json.loads(row[0])
                        # Parquet stores naïve UTC timestamps, as the canonical reload expects.
                        for field in ("observedAt", "parsedAt"):
                            if field in value:
                                stamp = datetime.fromisoformat(value[field])
                                value[field] = (
                                    stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp
                                )
                        MODELS[category].model_validate(value)
                        rows += 1
                result["callFiles"] += 1
                result["parquetFiles"] += 1
                result["parquetRows"] += rows
                result["parquetBytes"] += path.stat().st_size
                result["cursor"] = [modified, relative]
                verified += 1
        result["remainingFiles"] = len(scope) - verified
        result["complete"] = result["remainingFiles"] == 0
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
