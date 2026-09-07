"""Bounded buffered Parquet storage; retention is an explicit dry-run-first operation."""

import hashlib
import json
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol
from uuid import uuid4

import duckdb

from quant_engine.market_models import Candle, MarketObservation, PriceSample

type Category = Literal["observations", "samples", "seconds", "candles"]
type Record = MarketObservation | PriceSample | Candle


class MarketStorage(Protocol):
    def append(self, category: Category, record: Record) -> None: ...
    def flush(self) -> None: ...


class ParquetStorage:
    def __init__(self, root: Path, batch_size: int = 4096) -> None:
        if batch_size < 1:
            raise ValueError("Positive batch size required")
        self.root = root
        self.batch_size = batch_size
        self.pending: list[tuple[Category, Record]] = []

    def append(self, category: Category, record: Record) -> None:
        if len(self.pending) >= self.batch_size:
            self.flush()  # failure propagates; never grow the buffer on a failed flush
        self.pending.append((category, record))

    def flush(self) -> None:
        groups: dict[Path, list[Record]] = defaultdict(list)
        for category, record in self.pending:
            stamp = (
                record.observedAt.astimezone(UTC)
                if isinstance(record, MarketObservation)
                else datetime.fromtimestamp(
                    (record.openTime if isinstance(record, Candle) else record.timestamp) / 1000,
                    UTC,
                )
            )
            asset = re.sub(r"[^A-Za-z0-9_-]", "_", record.assetName)[:60]
            asset += "-" + hashlib.sha256(record.assetName.encode()).hexdigest()[:12]
            folder = (
                self.root
                / category
                / f"platform={record.platform}"
                / f"asset={asset}"
                / f"date={stamp:%Y-%m-%d}"
            )
            groups[folder].append(record)
        # Each group is committed independently, and removed only after atomic rename.
        for folder, rows in groups.items():
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / f"{uuid4()}.parquet"
            temporary = target.with_suffix(".tmp")
            source = target.with_suffix(".json.tmp")
            try:
                payload = []
                for r in rows:
                    value = r.model_dump(mode="json")
                    if isinstance(r, MarketObservation):
                        value["observedAt"] = r.observedAt.astimezone(UTC).isoformat()
                        value["parsedAt"] = r.parsedAt.astimezone(UTC).isoformat()
                    payload.append(value)
                source.write_text(json.dumps(payload))
                with duckdb.connect() as connection:
                    connection.read_json(
                        str(source),
                        format="array",
                        maximum_depth=-1,
                        hive_partitioning=False,
                        timestamp_format="iso",
                    ).write_parquet(str(temporary), compression="zstd")
            finally:
                source.unlink(missing_ok=True)
            temporary.replace(target)
            ids = {id(r) for r in rows}
            category_name = folder.relative_to(self.root).parts[0]
            self.pending = [
                (c, r) for c, r in self.pending if c != category_name or id(r) not in ids
            ]

    def reload(self, category: Category) -> list[Record]:
        files = list((self.root / category).rglob("*.parquet"))
        if not files:
            return []
        model = (
            MarketObservation
            if category == "observations"
            else Candle
            if category == "candles"
            else PriceSample
        )
        with duckdb.connect() as connection:
            rows = (
                connection.read_parquet(
                    [str(p) for p in files], hive_partitioning=False, union_by_name=True
                )
                .set_alias("r")
                .project("to_json(r)")
                .fetchall()
            )
        records: list[Record] = []
        for row in rows:
            values = json.loads(row[0])
            for field in ("observedAt", "parsedAt"):
                if field in values:
                    stamp = datetime.fromisoformat(values[field])
                    values[field] = stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp
            records.append(model.model_validate(values))
        return records

    def retention(self, before: datetime | None = None, *, delete: bool = False) -> list[Path]:
        """Default retains everything. Explicit cutoff, preview unless delete=True."""
        if before is None:
            return []
        candidates = [
            p
            for p in self.root.rglob("*.parquet")
            if datetime.fromtimestamp(p.stat().st_mtime, UTC) < before
        ]
        if delete:
            for path in candidates:
                path.unlink()
        return candidates
