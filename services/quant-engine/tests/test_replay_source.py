"""T-DA..T-DE: what the historical record is, and how it is read.

The source is where a backtest is most easily made dishonest: a row quietly repaired, a gap
quietly filled, an order quietly taken from the filesystem. Each of those is asserted against
here rather than described.
"""

from __future__ import annotations

from pathlib import Path

import replay_fixtures as fixtures
from quant_engine.market_storage import ParquetStorage
from quant_engine.replay import (
    REPLAY_ENTRY_LAYER,
    InMemoryObservationSource,
    ParquetObservationSource,
    ReplaySourceFilter,
    event_digest,
    market_time_of,
    ordering_key,
    replay_provenance,
)


def store(root: Path, rows: list[object]) -> ParquetStorage:
    storage = ParquetStorage(root)
    for row in rows:
        storage.append("observations", row)  # type: ignore[arg-type]
    storage.flush()
    return storage


# --- entry layer -----------------------------------------------------------------------


def test_replay_begins_at_the_raw_observation_so_phase_five_is_replayed_not_assumed() -> None:
    # The strongest honest claim available: the acceptance gate, the quality gate and the
    # parse-latency bound are all re-run, rather than being inherited from a canonical sample.
    assert REPLAY_ENTRY_LAYER == "PHASE4_MARKET_OBSERVATION"
    summary = InMemoryObservationSource(fixtures.small_history()).prepare()
    assert summary.entryLayer == "PHASE4_MARKET_OBSERVATION"


# --- ordering --------------------------------------------------------------------------


def test_canonical_order_is_market_time_first_then_identity_and_never_a_measured_time() -> None:
    rows = fixtures.small_history()
    keys = [ordering_key(row) for row in rows]
    # parsedAt is a parser's latency, not a market event, so it cannot appear in the key.
    assert all(key[0] == market_time_of(row) for key, row in zip(keys, rows, strict=True))
    assert len(keys[0]) == 6


def test_the_source_sorts_a_record_that_arrives_in_any_order() -> None:
    rows = fixtures.small_history()
    shuffled = [*rows[900:], *rows[:400], *rows[400:900]]
    source = InMemoryObservationSource(shuffled)
    stamps = [event.marketTime for batch in source.stream() for event in batch]
    assert stamps == sorted(stamps)


def test_reversed_storage_order_produces_the_identical_canonical_stream(tmp_path: Path) -> None:
    # The durable record is an explicitly unordered set: uuid-named files, read back in whatever
    # order the filesystem offers. Canonical sorting is the contract, so the answer cannot move.
    rows = fixtures.session(seconds=60, tag="order")
    store(tmp_path / "a", list(rows))
    store(tmp_path / "b", list(reversed(rows)))
    first = ParquetObservationSource(tmp_path / "a").prepare()
    second = ParquetObservationSource(tmp_path / "b").prepare()
    assert first.inputFingerprint == second.inputFingerprint
    assert first.events == second.events == len(rows)


def test_storage_order_is_reported_rather_than_silently_repaired(tmp_path: Path) -> None:
    rows = fixtures.session(seconds=40, tag="disorder")
    store(tmp_path, list(reversed(rows)))
    summary = ParquetObservationSource(tmp_path).prepare()
    assert summary.diagnostics.outOfOrder > 0


# --- fingerprint -----------------------------------------------------------------------


def test_one_changed_price_changes_the_input_fingerprint() -> None:
    rows = fixtures.small_history()
    before = InMemoryObservationSource(rows).prepare().inputFingerprint
    edited = [*rows]
    original = edited[500].price
    assert original is not None
    edited[500] = edited[500].model_copy(update={"price": original + 0.5})
    after = InMemoryObservationSource(edited).prepare().inputFingerprint
    assert before != after


def test_the_fingerprint_covers_every_semantic_field_of_a_row() -> None:
    row = fixtures.small_history()[0]
    baseline = event_digest(row)
    for field, value in (
        ("parserConfidence", 0.5),
        ("captureLatencyMs", 999.0),
        ("payout", 0.5),
        ("timerSeconds", 12),
        ("parserVersion", "other"),
    ):
        assert event_digest(row.model_copy(update={field: value})) != baseline


def test_the_same_history_fingerprints_identically_from_parquet_and_from_memory(
    tmp_path: Path,
) -> None:
    # Rendering from the validated model rather than from the stored bytes is what makes this
    # true, and it is what lets a golden fixture be compared against a real record's reader.
    rows = fixtures.session(seconds=45, tag="portable")
    store(tmp_path, list(rows))
    assert (
        ParquetObservationSource(tmp_path).prepare().inputFingerprint
        == InMemoryObservationSource(rows, mode="REPLAY").prepare().inputFingerprint
    )


# --- integrity -------------------------------------------------------------------------


def test_a_duplicated_row_is_counted_once_and_reported() -> None:
    rows = fixtures.session(seconds=30, tag="dup")
    source = InMemoryObservationSource([*rows, rows[10], rows[10]])
    summary = source.prepare()
    assert summary.diagnostics.duplicates == 2
    assert summary.events == len(rows)
    streamed = [event for batch in source.stream() for event in batch]
    assert len(streamed) == len(rows)


def test_two_ids_claiming_one_slot_at_one_instant_are_reported_as_a_collision() -> None:
    rows = fixtures.session(seconds=30, tag="collide")
    twin = rows[5].model_copy(update={"id": fixtures.identity("collision-twin")})
    summary = InMemoryObservationSource([*rows, twin]).prepare()
    assert summary.diagnostics.identityCollisions == 1


def test_a_malformed_row_is_counted_and_skipped_never_repaired(tmp_path: Path) -> None:
    import json
    from uuid import uuid4

    import duckdb

    rows = fixtures.session(seconds=20, tag="bad")
    store(tmp_path, list(rows))
    folder = next((tmp_path / "observations").rglob("*.parquet")).parent
    broken = json.loads(rows[0].model_dump_json())
    broken["price"] = "not-a-price"
    source = folder / "broken.json"
    source.write_text(json.dumps([broken]))
    with duckdb.connect() as connection:
        connection.read_json(str(source), format="array", maximum_depth=-1).write_parquet(
            str(folder / f"{uuid4()}.parquet")
        )
    source.unlink()
    summary = ParquetObservationSource(tmp_path).prepare()
    assert summary.diagnostics.malformed == 1
    # The row is absent, not zeroed. A price that cannot be read is not a price of zero.
    assert summary.events == len(rows)


def test_gaps_are_counted_and_never_filled() -> None:
    rows = fixtures.session(seconds=120, tag="gap", gap=(40, 70))
    summary = InMemoryObservationSource(rows).prepare()
    assert summary.gapSeconds >= 29
    assert summary.gapShare is not None and summary.gapShare > 0
    stamps = sorted(market_time_of(row) for row in rows if row.slotId == 1)
    replayed = [
        event.marketTime
        for batch in InMemoryObservationSource(rows).stream()
        for event in batch
        if event.observation.slotId == 1
    ]
    assert replayed == stamps  # nothing was invented to close the hole


# --- provenance ------------------------------------------------------------------------


def test_replayed_rows_carry_replay_provenance_and_never_masquerade_as_a_live_read() -> None:
    rows = fixtures.session(seconds=20, tag="prov")
    assert all(row.sourceType == "VISUAL" for row in rows)
    source = InMemoryObservationSource(rows, mode="REPLAY")
    events = [event for batch in source.stream() for event in batch]
    assert {event.observation.sourceType for event in events} == {"REPLAY"}
    assert {event.originSourceType for event in events} == {"VISUAL"}
    assert source.prepare().originSourceCounts == {"VISUAL": len(rows)}


def test_relabelling_provenance_changes_nothing_else_about_a_row() -> None:
    # Quality and degraded states are the whole point of replaying real history: putting the
    # real inputs back through the real gates. Only the transport label may move.
    row = fixtures.small_history()[0]
    replayed = replay_provenance(row, "REPLAY")
    before = row.model_dump(mode="json")
    after = replayed.model_dump(mode="json")
    assert after.pop("sourceType") == "REPLAY"
    assert before.pop("sourceType") == "VISUAL"
    assert before == after


def test_a_synthetic_fixture_stays_synthetic() -> None:
    events = [
        event
        for batch in InMemoryObservationSource(
            fixtures.session(seconds=15, tag="syn"), mode="SYNTHETIC"
        ).stream()
        for event in batch
    ]
    assert {event.observation.sourceType for event in events} == {"SYNTHETIC"}


# --- filters and windows ---------------------------------------------------------------


def test_the_source_filter_narrows_the_record_and_reports_what_it_removed() -> None:
    rows = fixtures.session(seconds=30, tag="filter")
    source = InMemoryObservationSource(rows, filters=ReplaySourceFilter(slotIds=[1]))
    summary = source.prepare()
    assert summary.slotsSeen == 1
    assert summary.diagnostics.filtered == len(rows) - summary.events


def test_a_fed_window_skips_and_counts_the_rest() -> None:
    rows = fixtures.session(seconds=60, tag="window")
    source = InMemoryObservationSource(rows)
    start = fixtures.BASE_MS + 20_000
    fed = [event for batch in source.stream(start=start) for event in batch]
    assert all(event.marketTime >= start for event in fed)
    assert source.outsideWindow == len(rows) - len(fed)


def test_platform_selection_excludes_the_other_broker_entirely() -> None:
    rows = [
        *fixtures.session(seconds=20, tag="cb"),
        *fixtures.session(platform="iqoption", seconds=20, tag="iq"),
    ]
    summary = InMemoryObservationSource(rows, platforms=("capitalbear",)).prepare()
    assert summary.platforms == ["capitalbear"]


# --- T-FA a physically damaged file ----------------------------------------------------


def corrupt(folder: Path, name: str = "damaged.parquet") -> Path:
    """A file that ends in .parquet and is not Parquet. Not a bad row — a bad file."""
    target = folder / name
    target.write_bytes(b"PAR1" + bytes(range(256)) * 8 + b"not-a-footer")
    return target


def test_one_unreadable_file_cannot_take_the_readable_history_down_with_it(
    tmp_path: Path,
) -> None:
    rows = fixtures.session(seconds=60, tag="corrupt")
    store(tmp_path, list(rows))
    folder = next((tmp_path / "observations").rglob("*.parquet")).parent
    corrupt(folder)

    summary = ParquetObservationSource(tmp_path).prepare()
    assert summary.diagnostics.unreadableFiles == 1
    # Every readable row still replays, and nothing was invented to stand in for the bad file.
    assert summary.events == len(rows)
    assert summary.diagnostics.malformed == 0
    streamed = [event for batch in ParquetObservationSource(tmp_path).stream() for event in batch]
    assert len(streamed) == len(rows)
    assert all(event.observation.price is not None for event in streamed)


def test_a_damaged_file_changes_no_price_and_fabricates_no_reading(tmp_path: Path) -> None:
    rows = fixtures.session(seconds=40, tag="intact")
    store(tmp_path / "clean", list(rows))
    store(tmp_path / "broken", list(rows))
    corrupt(next((tmp_path / "broken" / "observations").rglob("*.parquet")).parent)
    clean = ParquetObservationSource(tmp_path / "clean").prepare()
    broken = ParquetObservationSource(tmp_path / "broken").prepare()
    # The readable half of the record fingerprints identically with and without the bad file:
    # its absence is a gap in the evidence, never a zero, a default or an interpolation.
    assert broken.inputFingerprint == clean.inputFingerprint
    assert broken.events == clean.events
    assert broken.diagnostics.unreadableFiles == 1
    assert clean.diagnostics.unreadableFiles == 0


def test_a_record_of_nothing_but_damaged_files_is_empty_rather_than_invented(
    tmp_path: Path,
) -> None:
    """The deliberate choice for the worst case: an honest empty dataset, not a failure.

    Every file unreadable is indistinguishable, from here, from a record that holds nothing —
    and both are things the replay can report truthfully. It is reported as zero events with the
    damaged files counted, so the run finishes with INSUFFICIENT_HISTORY instead of throwing;
    what it must never do is continue as though some history had been recovered.
    """
    folder = tmp_path / "observations" / "platform=capitalbear" / "asset=x" / "date=2026-03-02"
    folder.mkdir(parents=True)
    corrupt(folder, "one.parquet")
    corrupt(folder, "two.parquet")
    summary = ParquetObservationSource(tmp_path).prepare()
    assert summary.events == 0
    assert summary.diagnostics.unreadableFiles == 2
    assert summary.startTime is None and summary.endTime is None
    assert [event for batch in ParquetObservationSource(tmp_path).stream() for event in batch] == []
