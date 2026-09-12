"""The historical event reader and its canonical ordering (Phase 11).

**Where a replay begins.** The earliest durable canonical market representation this repository
persists is the Phase 4 ``MarketObservation``: the raw, provenance-carrying, quality-carrying
reading that Phase 5 either accepts into the canonical series or rejects. Replaying from there
means Phase 5's own selection rules — the confidence floor, the quality gate, the parse-latency
bound, the one-sample-per-instant rule — are re-tested by the replay rather than assumed, which
is the strongest honest claim available here. ``REPLAY_ENTRY_LAYER`` is on every run's record so
a weaker one could never be mistaken for it.

**Nothing is fabricated.** A row that cannot be validated is counted and skipped, never repaired:
a price that cannot be read is not a price of zero. A gap in the record stays a gap — no
interpolation, no forward fill, no manufactured candle — and the quality and degraded states
travel through exactly as recorded.

**Provenance is rewritten, and the original is kept.** Replayed rows carry REPLAY (or SYNTHETIC
for a constructed fixture) so a recorded reading can never masquerade as a live DOM read further
down the pipeline; the source types as they were stored are reported on the dataset summary. The
quality block itself is untouched.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID

import duckdb

from quant_engine.configuration import Platform
from quant_engine.market_models import MarketObservation, QualityState, SourceType
from quant_engine.replay.models import (
    ReplayDatasetSummary,
    ReplayDiagnostics,
    ReplayEntryLayer,
    ReplaySourceFilter,
    ReplaySourceMode,
)

REPLAY_ENTRY_LAYER: ReplayEntryLayer = "PHASE4_MARKET_OBSERVATION"
"""Replay starts at the raw observation, so Phase 5 acceptance is replayed rather than assumed."""

OBSERVATIONS = "observations"
DEFAULT_BATCH = 4_096
SCAN_BATCH = 8_192
MAX_ASSET_NAMES = 64

LIVE_SOURCES: frozenset[str] = frozenset({"DOM", "VISUAL"})


@dataclass(frozen=True, slots=True)
class ReplayEvent:
    """One historical observation, ready to be fed to the pipeline.

    ``observation`` already carries the replay provenance; ``originSourceType`` is what the
    record actually said, kept so the summary can report it without the pipeline ever seeing a
    row that claims to be a live read.
    """

    observation: MarketObservation
    marketTime: int
    originSourceType: SourceType

    @property
    def identity(self) -> tuple[Platform, int, str, UUID]:
        row = self.observation
        return (row.platform, row.slotId, row.assetName, row.contextId)


def market_time_of(observation: MarketObservation) -> int:
    return int(observation.observedAt.timestamp() * 1000)


def ordering_key(observation: MarketObservation) -> tuple[int, str, int, str, str, str]:
    """The canonical replay order, and the reason it looks like this.

    Market time first, because that is the only ordering the market itself has. Then a fixed
    walk through the row's own identity, and finally the observation id.

    Nothing in the key is measured. ``parsedAt`` is deliberately absent: it records how long a
    parser took, so ordering by it would impose a capture-latency order on events whose true
    causal order is unknowable, and two slots that reported the same instant would be sequenced
    by whichever screenshot decoded faster. The file a row sits in, the row group it sits in and
    the modification time of either are absent for the same reason — the durable record is an
    explicitly unordered set, and an ordering derived from storage would not survive a copy.

    Same-instant events are never given knowledge of each other. Each slot owns its own series
    builder, so a row can only reach the slot it belongs to; a second row at the same instant on
    the same slot is refused by Phase 5 rather than ordered; and the one thing shared across
    slots — the availability watermark — is a function of the instant itself, so every ordering
    of one instant's rows produces the same watermark.
    """
    return (
        market_time_of(observation),
        observation.platform,
        observation.slotId,
        observation.assetName,
        str(observation.contextId),
        str(observation.id),
    )


def event_digest(observation: MarketObservation) -> str:
    """One row, rendered the same way every run, from the validated model.

    Over every semantic field rather than a hand-picked list of the ones that happen to feed a
    calculation today, for the reason Phase 10 hashes its rows the same way: a curated list
    drifts, and two materially different records sharing a fingerprint would make the run id
    worthless. Rendering from the model rather than from the stored bytes also means the same
    logical history fingerprints identically whether it arrived from Parquet or from a fixture.
    """
    quality = observation.dataQuality
    return "|".join(
        (
            str(observation.id),
            observation.platform,
            str(observation.slotId),
            observation.assetName,
            str(observation.contextId),
            str(market_time_of(observation)),
            str(int(observation.parsedAt.timestamp() * 1000)),
            observation.sourceType,
            "null" if observation.price is None else repr(observation.price),
            "null" if observation.payout is None else repr(observation.payout),
            "null" if observation.timerSeconds is None else str(observation.timerSeconds),
            repr(observation.parserConfidence),
            quality.state,
            repr(quality.confidence),
            repr(quality.freshness),
            repr(quality.completeness),
            repr(quality.sourceReliability),
            repr(quality.latencyMs),
            repr(observation.captureLatencyMs),
            repr(observation.parseLatencyMs),
            "null"
            if observation.calibrationProfileId is None
            else str(observation.calibrationProfileId),
            observation.parserVersion,
        )
    )


def replay_provenance(observation: MarketObservation, mode: ReplaySourceMode) -> MarketObservation:
    """The same reading, honestly relabelled as a replay of itself — identity intact.

    Two labels, deliberately. ``sourceType`` becomes REPLAY (or SYNTHETIC) so a recorded reading
    can never masquerade as something a broker surface produced just now.
    ``identitySourceType`` keeps the source that was actually recorded, because Phase 5 keys a
    series partly on it: the capture layer falls back from DOM to OCR mid-series on purpose, and
    that fallback ends one series and starts another.

    Collapsing both DOM and VISUAL to one label would erase that transition, and a replayed
    series would run straight through a reset the live run really performed — carrying candle
    continuity, warm-up, regime context and ranking history it never had. The replay would then
    be reporting what the pipeline *would* have done on a record the pipeline never saw.

    Everything else is untouched: the quality block, the parser confidence, the latencies and
    the timestamps are exactly what was recorded, because the whole point of replaying real
    history is to put the real degraded states back through the same gates.
    """
    if observation.sourceType == mode and observation.identitySourceType is None:
        return observation
    return observation.model_copy(
        update={"sourceType": mode, "identitySourceType": observation.identitySource}
    )


class ReplaySource(Protocol):
    """What a replay needs from a historical record, and nothing more."""

    outsideWindow: int
    """Rows the last ``stream`` skipped because they fell outside the fed window. Reported so a
    progress figure can account for every row of the record rather than only the fed ones."""

    def prepare(self) -> ReplayDatasetSummary: ...

    def stream(
        self, *, start: int | None = None, end: int | None = None, batch_size: int = DEFAULT_BATCH
    ) -> Iterator[list[ReplayEvent]]: ...


class _Accumulator:
    """Bounded running description of a record, folded row by row in canonical order."""

    __slots__ = (
        "assets",
        "contexts",
        "covered",
        "digest",
        "duplicates",
        "end",
        "events",
        "gaps",
        "identity_collisions",
        "last_second",
        "malformed",
        "origins",
        "platforms",
        "previous",
        "qualities",
        "slots",
        "start",
    )

    def __init__(self) -> None:
        self.digest = hashlib.sha256()
        self.events = 0
        self.malformed = 0
        self.duplicates = 0
        self.identity_collisions = 0
        self.start: int | None = None
        self.end: int | None = None
        self.assets: set[str] = set()
        self.contexts: set[UUID] = set()
        self.slots: set[tuple[str, int]] = set()
        self.platforms: set[Platform] = set()
        self.qualities: dict[QualityState, int] = defaultdict(int)
        self.origins: dict[SourceType, int] = defaultdict(int)
        self.last_second: dict[tuple[Platform, int, str, UUID], int] = {}
        self.gaps = 0
        self.covered = 0
        self.previous: tuple[int, str, int, str, str, str] | None = None

    def add(self, observation: MarketObservation) -> None:
        key = ordering_key(observation)
        if self.previous is not None:
            if key[5] == self.previous[5]:
                self.duplicates += 1
                return
            if key[:5] == self.previous[:5]:
                # Two different ids claiming one slot at one instant. Both are kept — Phase 5
                # refuses the second itself — and the collision is reported.
                self.identity_collisions += 1
        self.previous = key
        self.digest.update(event_digest(observation).encode())
        self.digest.update(b"\n")
        self.events += 1
        stamp = key[0]
        self.start = stamp if self.start is None else min(self.start, stamp)
        self.end = stamp if self.end is None else max(self.end, stamp)
        self.platforms.add(observation.platform)
        self.assets.add(observation.assetName)
        self.contexts.add(observation.contextId)
        self.slots.add((observation.platform, observation.slotId))
        self.qualities[observation.dataQuality.state] += 1
        self.origins[observation.sourceType] += 1
        identity = (
            observation.platform,
            observation.slotId,
            observation.assetName,
            observation.contextId,
        )
        second = stamp // 1000
        previous_second = self.last_second.get(identity)
        if previous_second is None or second > previous_second:
            self.covered += 1
        if previous_second is not None and second > previous_second:
            # Whole seconds this identity was live and said nothing. Reported, never filled in.
            self.gaps += max(0, second - previous_second - 1)
        if previous_second is None or second > previous_second:
            self.last_second[identity] = second

    def summary(
        self,
        *,
        source_type: str,
        mode: ReplaySourceMode,
        label: str,
        rows_read: int,
        out_of_order: int,
        filtered: int,
        unreadable: int = 0,
    ) -> ReplayDatasetSummary:
        total = self.covered + self.gaps
        return ReplayDatasetSummary(
            entryLayer=REPLAY_ENTRY_LAYER,
            sourceType="IN_MEMORY_OBSERVATIONS"
            if source_type == "memory"
            else "PARQUET_OBSERVATIONS",
            sourceMode=mode,
            sourcePathLabel=label,
            inputFingerprint=self.digest.hexdigest(),
            events=self.events,
            startTime=self.start,
            endTime=self.end,
            durationMs=0 if self.start is None or self.end is None else self.end - self.start,
            platforms=sorted(self.platforms),
            assetsSeen=len(self.assets),
            contextsSeen=len(self.contexts),
            slotsSeen=len(self.slots),
            assetNames=sorted(self.assets)[:MAX_ASSET_NAMES],
            qualityCounts=dict(sorted(self.qualities.items())),
            originSourceCounts=dict(sorted(self.origins.items())),
            gapSeconds=self.gaps,
            coveredSeconds=self.covered,
            gapShare=self.gaps / total if total else None,
            diagnostics=ReplayDiagnostics(
                rowsRead=rows_read,
                accepted=self.events,
                duplicates=self.duplicates,
                identityCollisions=self.identity_collisions,
                outOfOrder=out_of_order,
                malformed=self.malformed,
                filtered=filtered,
                outsideWindow=0,
                unreadableFiles=unreadable,
            ),
        )


def _selected(observation: MarketObservation, filters: ReplaySourceFilter) -> bool:
    if filters.assetNames and observation.assetName not in filters.assetNames:
        return False
    if filters.slotIds and observation.slotId not in filters.slotIds:
        return False
    return not (filters.contextIds and observation.contextId not in filters.contextIds)


class InMemoryObservationSource:
    """A record held as validated observations. The path fixtures and benchmarks use.

    Ordering is applied here exactly as it is for a stored record, so a fixture handed to this
    source in any order replays identically — which is what makes a fixture a test of the replay
    rather than a test of the order somebody happened to write it in.
    """

    def __init__(
        self,
        observations: Sequence[MarketObservation],
        *,
        platforms: Sequence[Platform] = ("capitalbear", "iqoption"),
        mode: ReplaySourceMode = "SYNTHETIC",
        filters: ReplaySourceFilter | None = None,
        label: str = "in-memory",
    ) -> None:
        self.mode = mode
        self.label = label
        self.filters = filters if filters is not None else ReplaySourceFilter()
        self.rowsRead = len(observations)
        allowed = set(platforms)
        kept = [
            row for row in observations if row.platform in allowed and _selected(row, self.filters)
        ]
        self.filtered = len(observations) - len(kept)
        self.outOfOrder = sum(
            1
            for previous, current in zip(observations, observations[1:], strict=False)
            if market_time_of(current) < market_time_of(previous)
        )
        self.rows = sorted(kept, key=ordering_key)
        self.outsideWindow = 0

    def prepare(self) -> ReplayDatasetSummary:
        accumulator = _Accumulator()
        for row in self.rows:
            accumulator.add(row)
        return accumulator.summary(
            source_type="memory",
            mode=self.mode,
            label=self.label,
            rows_read=self.rowsRead,
            out_of_order=self.outOfOrder,
            filtered=self.filtered,
        )

    def stream(
        self, *, start: int | None = None, end: int | None = None, batch_size: int = DEFAULT_BATCH
    ) -> Iterator[list[ReplayEvent]]:
        batch: list[ReplayEvent] = []
        seen: str | None = None
        self.outsideWindow = 0
        for row in self.rows:
            key = ordering_key(row)
            if seen is not None and key[5] == seen:
                continue
            seen = key[5]
            stamp = key[0]
            if (start is not None and stamp < start) or (end is not None and stamp > end):
                self.outsideWindow += 1
                continue
            batch.append(ReplayEvent(replay_provenance(row, self.mode), stamp, row.sourceType))
            if len(batch) >= batch_size:
                yield batch
                batch = []
        if batch:
            yield batch


class ParquetObservationSource:
    """The durable Phase 4 record, read in canonical order and never in file order.

    Rows are sorted by DuckDB and streamed in bounded batches, so a record far larger than
    memory replays without ever being materialized as one list. Two passes are made — one to
    describe and fingerprint the record, one to feed it — because the run id has to exist before
    the run does, and a run id derived from anything cheaper than the content would not identify
    the content.
    """

    def __init__(
        self,
        root: Path,
        *,
        platforms: Sequence[Platform] = ("capitalbear", "iqoption"),
        mode: ReplaySourceMode = "REPLAY",
        filters: ReplaySourceFilter | None = None,
        label: str | None = None,
    ) -> None:
        self.root = root
        self.platforms = tuple(platforms)
        self.mode = mode
        self.filters = filters if filters is not None else ReplaySourceFilter()
        self.label = label if label is not None else f"{OBSERVATIONS}/{'+'.join(self.platforms)}"
        self.files = sorted(str(path) for path in (root / OBSERVATIONS).rglob("*.parquet"))
        self.outsideWindow = 0

    # --- reading -----------------------------------------------------------------------

    def _probe(
        self, connection: duckdb.DuckDBPyConnection, path: str
    ) -> tuple[tuple[tuple[str, str], ...], bool]:
        """One file's column layout, and whether it can actually be read at all.

        A layout alone is not enough. Describing a file reads its footer, so a file whose footer
        survives but whose data pages are damaged would describe cleanly and then fail halfway
        through the replay — with the whole union behind it. Pulling one real row proves a data
        page decodes, which is the difference between isolating a bad file and discovering it
        after an hour of work.
        """
        try:
            relation = connection.sql(f"SELECT * FROM read_parquet({path!r}) LIMIT 1")
            layout = tuple(
                (str(name), str(kind))
                for name, kind in zip(relation.columns, relation.types, strict=True)
            )
            relation.fetchall()
        except duckdb.Error:
            return ((), False)
        return (layout, True)

    def _groups(self, connection: duckdb.DuckDBPyConnection) -> tuple[list[list[str]], list[str]]:
        """Readable files grouped by their exact column layout, and the ones that would not open.

        Two separate protections, both learned from the same failure mode.

        Files are only read together when their schemas are identical. Reading a whole record
        with ``union_by_name`` unifies mismatched column types instead — one file whose price
        column came back as text would retype *every* file's price as text, and a single damaged
        file would make the entire record unreadable.

        And a file that cannot be opened at all is **left out of the query entirely** rather than
        given a group of its own. A group that cannot be read is still a branch of the union, so
        keeping it would take the readable history down with it — which is exactly the outage
        the grouping exists to prevent. It is counted and reported instead, never repaired and
        never replaced with zeroes.
        """
        grouped: dict[tuple[tuple[str, str], ...], list[str]] = {}
        unreadable: list[str] = []
        for path in self.files:
            layout, readable = self._probe(connection, path)
            if not readable:
                unreadable.append(path)
                continue
            grouped.setdefault(layout, []).append(path)
        ordered = [files for _, files in sorted(grouped.items(), key=lambda item: item[1][0])]
        return (ordered, sorted(unreadable))

    def _ordered(self, connection: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyRelation:
        """Every stored row as JSON text, in canonical order, sorted by DuckDB rather than here.

        Each schema group is projected to a JSON string first, so the union is over text and no
        two files can retype one another. The sort runs inside DuckDB, which spills to disk when
        it has to, so a record larger than memory still replays in order — this reader never
        holds more than one fetch batch of rows at a time.
        """
        groups, _ = self._groups(connection)
        selects = [
            f"SELECT to_json(r) AS row FROM read_parquet({files!r}, hive_partitioning := false) r"
            for files in groups
        ]
        if not selects:
            return connection.sql("SELECT NULL AS row WHERE false")
        union = " UNION ALL ".join(selects)
        return connection.sql(
            "SELECT row FROM ("
            "SELECT row, epoch_ms(TRY_CAST(json_extract_string(row, '$.observedAt') "
            "AS TIMESTAMPTZ)) AS stamp FROM (" + union + ")) "
            "ORDER BY stamp, json_extract_string(row, '$.platform'), "
            "TRY_CAST(json_extract_string(row, '$.slotId') AS BIGINT), "
            "json_extract_string(row, '$.assetName'), "
            "json_extract_string(row, '$.contextId'), "
            "json_extract_string(row, '$.id')"
        )

    def _rows(self, connection: duckdb.DuckDBPyConnection) -> Iterator[MarketObservation | None]:
        """Every stored row in canonical order, or ``None`` where one could not be validated.

        Fetched in bounded batches, so the reader's memory is a fetch batch rather than the
        record. A row that will not validate is yielded as ``None`` and counted by the caller;
        it is never repaired, defaulted or dropped silently.
        """
        relation = self._ordered(connection)
        while True:
            chunk = relation.fetchmany(SCAN_BATCH)
            if not chunk:
                return
            for row in chunk:
                yield _validate(str(row[0]))

    def _out_of_order(self, connection: duckdb.DuckDBPyConnection) -> int:
        """How many rows sit later in storage than a row describing an earlier instant.

        A diagnostic, not a fault. ``ParquetStorage`` writes uuid-named files per partition and
        reads them back in whatever order the filesystem offers, so the record is an explicitly
        unordered durable set; canonical sorting is the contract rather than a repair, and this
        count is the evidence that it was needed.
        """
        groups, _ = self._groups(connection)
        selects = [
            "SELECT r.filename AS file, r.file_row_number AS position, "
            "json_extract_string(to_json(r), '$.observedAt') AS stamp "
            f"FROM read_parquet({files!r}, hive_partitioning := false, "
            "filename := true, file_row_number := true) r"
            for files in groups
        ]
        if not selects:
            return 0
        try:
            result = connection.sql(
                "SELECT count(*) FROM (SELECT stamp, lag(stamp) OVER "
                "(ORDER BY file, position) AS previous FROM ("
                + " UNION ALL ".join(selects)
                + ")) WHERE previous IS NOT NULL AND stamp < previous"
            ).fetchone()
        except duckdb.Error:
            return 0
        return 0 if result is None else int(result[0])

    # --- contract ----------------------------------------------------------------------

    def prepare(self) -> ReplayDatasetSummary:
        accumulator = _Accumulator()
        rows_read = 0
        filtered = 0
        out_of_order = 0
        unreadable = 0
        if self.files:
            with duckdb.connect() as connection:
                unreadable = len(self._groups(connection)[1])
                out_of_order = self._out_of_order(connection)
                allowed = set(self.platforms)
                for observation in self._rows(connection):
                    rows_read += 1
                    if observation is None:
                        accumulator.malformed += 1
                        continue
                    if observation.platform not in allowed or not _selected(
                        observation, self.filters
                    ):
                        filtered += 1
                        continue
                    accumulator.add(observation)
        return accumulator.summary(
            source_type="parquet",
            mode=self.mode,
            label=self.label,
            rows_read=rows_read,
            out_of_order=out_of_order,
            filtered=filtered,
            unreadable=unreadable,
        )

    def stream(
        self, *, start: int | None = None, end: int | None = None, batch_size: int = DEFAULT_BATCH
    ) -> Iterator[list[ReplayEvent]]:
        self.outsideWindow = 0
        if not self.files:
            return
        allowed = set(self.platforms)
        batch: list[ReplayEvent] = []
        seen: str | None = None
        with duckdb.connect() as connection:
            for observation in self._rows(connection):
                if observation is None:
                    continue
                if observation.platform not in allowed or not _selected(observation, self.filters):
                    continue
                key = ordering_key(observation)
                if seen is not None and key[5] == seen:
                    continue
                seen = key[5]
                stamp = key[0]
                if (start is not None and stamp < start) or (end is not None and stamp > end):
                    self.outsideWindow += 1
                    continue
                batch.append(
                    ReplayEvent(
                        replay_provenance(observation, self.mode), stamp, observation.sourceType
                    )
                )
                if len(batch) >= batch_size:
                    yield batch
                    batch = []
        if batch:
            yield batch


def _validate(payload: str) -> MarketObservation | None:
    """One stored row as a model, or ``None``. A row that cannot be read is never guessed at."""

    try:
        values = json.loads(payload)
        for field in ("observedAt", "parsedAt"):
            if field in values and isinstance(values[field], str):
                stamp = datetime.fromisoformat(values[field])
                values[field] = stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp
        return MarketObservation.model_validate(values)
    except (ValueError, TypeError):
        return None
