"""Bounded buffered Parquet storage; retention is an explicit dry-run-first operation."""

import hashlib
import json
import re
from collections import defaultdict, deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol
from uuid import uuid4

import duckdb

from quant_engine.features.models import FeatureSnapshot
from quant_engine.market_models import TIMEFRAMES, Candle, MarketObservation, PriceSample, Timeframe
from quant_engine.opportunity.models import OpportunityBoard, OpportunityCandidate
from quant_engine.strategy.models import EnsembleSnapshot, RegimeSnapshot, StrategyEvaluation

type Category = Literal[
    "observations",
    "samples",
    "seconds",
    "candles",
    "features",
    "regimes",
    "strategy_evaluations",
    "ensembles",
    "opportunity_candidates",
    "opportunity_boards",
]
type Record = (
    MarketObservation
    | PriceSample
    | Candle
    | FeatureSnapshot
    | RegimeSnapshot
    | StrategyEvaluation
    | EnsembleSnapshot
    | OpportunityCandidate
    | OpportunityBoard
)

MODELS: dict[Category, type[Record]] = {
    "observations": MarketObservation,
    "samples": PriceSample,
    "seconds": PriceSample,
    "candles": Candle,
    "features": FeatureSnapshot,
    "regimes": RegimeSnapshot,
    "strategy_evaluations": StrategyEvaluation,
    "ensembles": EnsembleSnapshot,
    "opportunity_candidates": OpportunityCandidate,
    "opportunity_boards": OpportunityBoard,
}
"""Which model owns each category. Reloading a category through the wrong model would accept
some rows and silently reshape others, so the mapping is explicit rather than inferred."""

LIVE_SOURCES = ("DOM", "VISUAL")
HISTORY_LIMIT = 256

BOARD_PARTITION = "_cross-asset"
"""Where a record that is about several assets at once lives.

An opportunity board ranks a whole platform cohort, so no single asset owns it and forcing
one into the path would be a lie about what the row contains. Every real asset partition
carries a hash suffix, so this name cannot collide with one."""


def asset_partition(asset_name: str) -> str:
    """Sanitized, collision-resistant folder name. Raw asset text never reaches the path."""
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", asset_name)[:60]
    return safe + "-" + hashlib.sha256(asset_name.encode()).hexdigest()[:12]


def partition_asset(record: Record) -> str:
    """The asset folder a record belongs in. Cross-asset records get their own."""
    if isinstance(record, OpportunityBoard):
        return BOARD_PARTITION
    return asset_partition(record.assetName)


def record_stamp(record: Record) -> datetime:
    if isinstance(record, MarketObservation):
        return record.observedAt.astimezone(UTC)
    if isinstance(record, Candle):
        millis = record.openTime
    elif isinstance(record, FeatureSnapshot):
        millis = record.featureTime
    elif isinstance(
        record,
        RegimeSnapshot
        | StrategyEvaluation
        | EnsembleSnapshot
        | OpportunityCandidate
        | OpportunityBoard,
    ):
        millis = record.asOf
    else:
        millis = record.timestamp
    return datetime.fromtimestamp(millis / 1000, UTC)


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
            stamp = record_stamp(record)
            asset = partition_asset(record)
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
        """Every stored row of one category, back through the model that owns it.

        Files are read one at a time rather than as a set. A list column whose rows all
        happened to be empty in one file is typed differently from the same column in a file
        where it was populated — an empty ``list[str]`` infers as JSON, a populated one as
        VARCHAR — and no cast reconciles the two. The model is the schema of record, so the
        merge belongs after validation rather than inside the reader. This is a diagnostic and
        replay path, not the ingestion hot path, so the extra scans cost nothing that matters.
        """
        files = sorted((self.root / category).rglob("*.parquet"))
        if not files:
            return []
        model = MODELS[category]
        rows: list[tuple[str]] = []
        with duckdb.connect() as connection:
            for path in files:
                rows.extend(
                    connection.read_parquet(str(path), hive_partitioning=False)
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

    def load_history(
        self,
        platform: str,
        asset_name: str,
        timeframe: Timeframe,
        *,
        before: int,
        limit: int = HISTORY_LIMIT,
    ) -> list[Candle]:
        """Trusted earlier candles for one exact series, for indicator warm-up only.

        Only CLOSED candles that already closed at or before ``before`` are returned, only from
        live capture sources, and only the contiguous tail: a gap ends the history rather than
        being bridged. The exact asset name is matched, so OTC and non-OTC never mix.
        """
        folder = (
            self.root / "candles" / f"platform={platform}" / f"asset={asset_partition(asset_name)}"
        )
        files = sorted(str(path) for path in folder.rglob("*.parquet"))
        if not files:
            return []
        with duckdb.connect() as connection:
            rows = (
                connection.read_parquet(files, hive_partitioning=False, union_by_name=True)
                .set_alias("r")
                .project("to_json(r)")
                .fetchall()
            )
        best: dict[int, Candle] = {}
        for row in rows:
            try:
                candle = Candle.model_validate(json.loads(row[0]))
            except ValueError:
                continue
            if (
                candle.platform != platform
                or candle.assetName != asset_name
                or candle.timeframe != timeframe
                or candle.state != "CLOSED"
                or candle.sourceType not in LIVE_SOURCES
                or candle.closeTime > before
            ):
                continue
            previous = best.get(candle.openTime)
            if previous is None or _preference(candle) > _preference(previous):
                best[candle.openTime] = candle
        ordered = [best[key] for key in sorted(best)]
        return _contiguous_tail(ordered, TIMEFRAMES[timeframe] * 1000)[-limit:]

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


def _preference(candle: Candle) -> tuple[int, float, int]:
    """Deterministic winner for duplicate open times: quality, then coverage, then samples."""
    return (1 if candle.quality == "GOOD" else 0, candle.coverage, candle.sampleCount)


def _contiguous_tail(candles: list[Candle], duration_ms: int) -> list[Candle]:
    if not candles:
        return []
    tail: deque[Candle] = deque([candles[-1]])
    for candle in reversed(candles[:-1]):
        if tail[0].openTime - candle.openTime != duration_ms:
            break
        tail.appendleft(candle)
    return list(tail)
