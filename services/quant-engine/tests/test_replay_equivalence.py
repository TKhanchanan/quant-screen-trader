"""T-DX, T-DY: the live analytical path and the replay path are one implementation.

The most important test in Phase 11. The claim the whole layer rests on is that a replay is an
*input driver* around the production pipeline rather than a second implementation of it, and the
only way to hold that claim to account is to feed one deterministic record through both paths and
require the same Phase 6, 7, 8 and 9 outputs from each.

The golden fixture beside it freezes one small replay's whole answer on disk. It is the test that
notices a change nobody meant to make: not "did the code run", but "does this exact history still
produce this exact result".
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest
import replay_fixtures as fixtures
from pydantic import ValidationError
from quant_engine.features.models import FeatureSnapshot
from quant_engine.market_api import MarketEngine, ObservationBatch
from quant_engine.market_builder import AVAILABILITY_LAG_MS as MARKET_LAG
from quant_engine.market_models import Candle, MarketObservation, PriceSample
from quant_engine.market_storage import Category, ParquetStorage, Record
from quant_engine.opportunity.models import OpportunityBoard
from quant_engine.paper.models import PaperTrade
from quant_engine.paper.policy import PaperSettings
from quant_engine.replay import (
    AVAILABILITY_LAG_MS,
    InMemoryObservationSource,
    ReplayEngine,
    ReplayManifest,
    market_time_of,
    ordering_key,
    replay_provenance,
)
from quant_engine.session_guard.settings import SessionGuardSettings
from quant_engine.strategy.models import EnsembleSnapshot

GOLDEN = Path(__file__).parent / "data" / "replay_golden.json"
ACCOUNTING = PaperSettings(paperCurrency="THB", paperStake=50, paperPayoutRate=0.82)
LIVE_BATCH = 18
"""The live HTTP contract's maximum: nine slots on two platforms in one request."""

CAPTURE = frozenset(
    {"candles", "samples", "features", "ensembles", "opportunity_boards", "paper_trades"}
)
"""What the replay sink keeps in memory for this comparison. A replay an operator starts keeps
the evidence and not the derived series; a test that is checking the derived series has to ask
for it explicitly."""


class Recorder(ParquetStorage):
    """A storage sink that keeps records instead of writing them. Nothing is persisted."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.records: dict[str, list[Record]] = {}

    def append(self, category: Category, record: Record) -> None:
        self.records.setdefault(category, []).append(record)

    def flush(self) -> None:
        self.pending.clear()

    def load_history(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []


def live_cohort_path(rows: list[MarketObservation], root: Path) -> Recorder:
    """The record driven through the live engine one capture tick at a time.

    Grouped by the second each reading describes, which is the shape the capture layer really
    sends: one request per tick carrying every slot that reported in it. It matters here because
    the live path refuses a DOM or VISUAL reading more than three seconds away from the arrival
    time it was posted with — a freshness gate a replayed reading does not go through, and one a
    fixed-size batch would trip on its own and hide the thing this test is measuring.
    """
    storage = Recorder(root)
    engine = MarketEngine(storage, paper=ACCOUNTING, guard=SessionGuardSettings())
    ordered = sorted(rows, key=ordering_key)
    tick: list[MarketObservation] = []
    for row in ordered:
        if tick and market_time_of(row) // 1000 != market_time_of(tick[0]) // 1000:
            now = max(market_time_of(item) for item in tick)
            engine.ingest(ObservationBatch(observations=tick), now)
            engine.advance_live(now)
            tick = []
        tick.append(row)
    if tick:
        now = max(market_time_of(item) for item in tick)
        engine.ingest(ObservationBatch(observations=tick), now)
        engine.advance_live(now)
    return storage


def live_path(rows: list[MarketObservation], root: Path) -> Recorder:
    """The record driven through the engine exactly as the live HTTP path drives it.

    Batched arrivals through ``ingest``, then the maintenance pass that advances the availability
    watermark — the same two calls ``market_api`` and the app's background task make. Nothing
    replay-specific is involved: this is the production entry point.
    """
    storage = Recorder(root)
    engine = MarketEngine(storage, paper=ACCOUNTING, guard=SessionGuardSettings())
    ordered = sorted(rows, key=ordering_key)
    for start in range(0, len(ordered), LIVE_BATCH):
        chunk = ordered[start : start + LIVE_BATCH]
        now = max(market_time_of(row) for row in chunk)
        engine.ingest(ObservationBatch(observations=chunk), now)
        engine.advance_live(now)
    return storage


def replay_path(rows: list[MarketObservation], root: Path) -> Any:
    spec = ReplayManifest(
        warmupDurationMs=0,
        sourceMode="SYNTHETIC",
        includeIqOption=False,
        paperSettings=ACCOUNTING,
    )
    source = InMemoryObservationSource(rows, mode="SYNTHETIC", platforms=("capitalbear",))
    engine = ReplayEngine(spec, source, root=root, persist=False, capture=CAPTURE)
    engine.run()
    assert engine.storage is not None
    return engine.storage


def features_of(records: list[Record]) -> list[tuple[Any, ...]]:
    return [
        (
            row.platform,
            row.slotId,
            row.assetName,
            row.timeframe,
            row.featureTime,
            row.status,
            row.barCount,
            row.trend.ema20,
            row.momentum.rsi14,
            row.volatility.atr14,
        )
        for row in records
        if isinstance(row, FeatureSnapshot)
    ]


def ensembles_of(records: list[Record]) -> list[tuple[Any, ...]]:
    return [
        (
            row.platform,
            row.slotId,
            row.asOf,
            row.direction,
            row.confidence,
            row.regime.primaryRegime,
        )
        for row in records
        if isinstance(row, EnsembleSnapshot)
    ]


def boards_of(records: list[Record]) -> list[tuple[Any, ...]]:
    return [
        (
            row.platform,
            row.asOf,
            row.status,
            row.selectedSlotId,
            row.selectedDirection,
            row.leadMargin,
        )
        for row in records
        if isinstance(row, OpportunityBoard)
    ]


def outcomes_of(records: list[Record]) -> list[tuple[Any, ...]]:
    return sorted(
        (
            str(row.paperTradeId),
            row.status,
            row.outcome,
            row.entryTime,
            row.entryPrice,
            row.expiryTime,
            row.expiryPrice,
            row.priceDeltaBps,
            row.realizedPaperPnl,
        )
        for row in records
        if isinstance(row, PaperTrade)
    )


# --- T-DX live and replay agree --------------------------------------------------------


def test_the_two_paths_produce_the_same_phase_six_to_nine_outputs(tmp_path: Path) -> None:
    # The same deterministic record, once through the live entry point and once through the
    # replay driver. Everything a decision is made of has to match, snapshot for snapshot.
    rows = fixtures.session(seconds=420, tag="equiv")
    replayed = [replay_provenance(row, "SYNTHETIC") for row in rows]
    live = live_path(replayed, tmp_path / "live")
    offline = replay_path(rows, tmp_path / "replay")

    assert features_of(live.records.get("features", [])), "the fixture must produce features"
    assert features_of(live.records.get("features", [])) == features_of(
        _captured(offline, "features")
    )
    assert ensembles_of(live.records.get("ensembles", [])) == ensembles_of(
        _captured(offline, "ensembles")
    )
    assert boards_of(live.records.get("opportunity_boards", [])) == boards_of(
        _captured(offline, "opportunity_boards")
    )
    assert outcomes_of(live.records.get("paper_trades", [])) == outcomes_of(
        _captured(offline, "paper_trades")
    )
    assert outcomes_of(live.records.get("paper_trades", []))


def _captured(storage: Any, category: str) -> list[Record]:
    return list(storage.captured.get(category, []))


def test_the_same_record_gives_the_same_feature_state_to_both_paths(tmp_path: Path) -> None:
    rows = fixtures.session(seconds=300, tag="feateq")
    replayed = [replay_provenance(row, "SYNTHETIC") for row in rows]
    live = live_path(replayed, tmp_path / "live")
    spec = ReplayManifest(
        warmupDurationMs=0,
        sourceMode="SYNTHETIC",
        includeIqOption=False,
        paperSettings=ACCOUNTING,
    )
    engine = ReplayEngine(
        spec,
        InMemoryObservationSource(rows, mode="SYNTHETIC", platforms=("capitalbear",)),
        root=tmp_path / "replay",
        persist=False,
        capture=CAPTURE,
    )
    engine.run()
    assert engine.storage is not None
    assert features_of(live.records.get("features", [])) == features_of(
        _captured(engine.storage, "features")
    )


def test_the_availability_lag_the_replay_applies_is_the_one_the_live_engine_applies() -> None:
    # One constant, imported by both, rather than two literals that agree today. If they could
    # drift, a replay would close bars at different points in the series than the run it claims
    # to be reproducing — and nothing would fail to say so.
    assert AVAILABILITY_LAG_MS is MARKET_LAG
    assert AVAILABILITY_LAG_MS == 3_000
    source = ast.parse(
        (Path(__file__).parents[1] / "src" / "quant_engine" / "market_api.py").read_text()
    )
    watermarks = [
        node
        for node in ast.walk(source)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "advance"
    ]
    assert watermarks, "the live engine must still advance a watermark"
    names = {
        child.id for node in watermarks for child in ast.walk(node) if isinstance(child, ast.Name)
    }
    assert "AVAILABILITY_LAG_MS" in names


# --- T-DY golden replay ----------------------------------------------------------------


def golden_result(tmp_path: Path) -> dict[str, Any]:
    rows = fixtures.session(seconds=420, tag="golden")
    spec = ReplayManifest(
        warmupDurationMs=0,
        sourceMode="SYNTHETIC",
        includeIqOption=False,
        paperSettings=ACCOUNTING,
    )
    engine = ReplayEngine(
        spec,
        InMemoryObservationSource(rows, mode="SYNTHETIC", platforms=("capitalbear",)),
        root=tmp_path,
        persist=False,
    )
    result = engine.run()
    assert result.analysis is not None and result.snapshot is not None
    return {
        "replayVersion": result.run.replayVersion,
        "replayRunId": str(result.run.replayRunId),
        "inputFingerprint": result.run.inputFingerprint,
        "events": result.run.processedEvents,
        "accepted": result.run.acceptedEvents,
        "ensembles": result.run.ensemblesProduced,
        "boardsFinalized": result.run.boardsFinalized,
        "boardsSelected": result.run.boardsSelected,
        "datasetFingerprint": result.analysis.fingerprint,
        "analyticsSnapshotId": str(result.snapshot.snapshotId),
        "trades": [
            {
                "paperTradeId": str(trade.paperTradeId),
                "slotId": trade.slotId,
                "direction": trade.direction,
                "boardAsOf": trade.boardAsOf,
                "decisionAvailableAt": trade.decisionAvailableAt,
                "entryTime": trade.entryTime,
                "entryPrice": trade.entryPrice,
                "expiryTime": trade.expiryTime,
                "expiryPrice": trade.expiryPrice,
                "status": trade.status,
                "outcome": trade.outcome,
                "realizedPaperPnl": trade.realizedPaperPnl,
            }
            for trade in sorted(
                result.trades, key=lambda item: (str(item.paperTradeId), item.status)
            )
        ],
    }


def test_the_golden_replay_still_produces_exactly_what_it_produced(tmp_path: Path) -> None:
    expected = json.loads(GOLDEN.read_text())
    assert golden_result(tmp_path) == expected


# --- T-DZ mixed original sources -------------------------------------------------------


def candles_of(records: list[Record]) -> list[tuple[Any, ...]]:
    return [
        (
            row.platform,
            row.slotId,
            row.timeframe,
            row.openTime,
            row.closeTime,
            row.open,
            row.high,
            row.low,
            row.close,
            row.sampleCount,
            row.coverage,
            row.quality,
        )
        for row in records
        if isinstance(row, Candle) and row.state == "CLOSED"
    ]


def replay_of(rows: list[MarketObservation], root: Path) -> Any:
    """The same rows through the replay driver, relabelled REPLAY for provenance."""
    spec = ReplayManifest(
        warmupDurationMs=0,
        sourceMode="REPLAY",
        includeIqOption=False,
        paperSettings=ACCOUNTING,
    )
    engine = ReplayEngine(
        spec,
        InMemoryObservationSource(rows, mode="REPLAY", platforms=("capitalbear",)),
        root=root,
        persist=False,
        capture=CAPTURE,
    )
    engine.run()
    assert engine.storage is not None
    return engine.storage


def test_a_capture_path_hand_over_resets_the_series_in_replay_exactly_as_it_does_live(
    tmp_path: Path,
) -> None:
    """The regression this whole fix exists for.

    Phase 5 keys a series partly on which capture path produced it, so a DOM-to-OCR fallback
    ends one series and starts another. A replay relabels every recorded reading REPLAY so it
    cannot masquerade as a live one — and if that relabelling also collapsed DOM and VISUAL into
    one value, the transition would vanish and the replayed series would run straight through a
    reset the live run really performed, inheriting candle continuity, warm-up, regime context
    and ranking history it never had.

    So: identical readings, live under their recorded DOM/VISUAL labels and replayed under
    REPLAY, must produce the same canonical series and the same decisions.
    """
    rows = fixtures.mixed_source_session()
    live = live_cohort_path(rows, tmp_path / "live")
    offline = replay_of(rows, tmp_path / "replay")

    live_candles = candles_of(live.records.get("candles", []))
    assert live_candles, "the fixture must actually close bars"
    assert live_candles == candles_of(_captured(offline, "candles"))
    assert features_of(live.records.get("features", [])) == features_of(
        _captured(offline, "features")
    )
    assert ensembles_of(live.records.get("ensembles", [])) == ensembles_of(
        _captured(offline, "ensembles")
    )
    assert boards_of(live.records.get("opportunity_boards", [])) == boards_of(
        _captured(offline, "opportunity_boards")
    )
    assert outcomes_of(live.records.get("paper_trades", [])) == outcomes_of(
        _captured(offline, "paper_trades")
    )


def test_the_hand_over_really_does_reset_something_so_the_comparison_is_not_vacuous(
    tmp_path: Path,
) -> None:
    """Proof the equivalence above is measuring a reset rather than agreeing about nothing.

    The same prices and the same timestamps, once with the capture path changing hands twice and
    once pinned to a single path. A reset throws away the bar that was forming when it happened
    and starts that bar again from the hand-over, so a bar spanning one must come back with only
    the samples that arrived after it — half a minute of a minute, not the whole minute. If these
    two runs ever matched, the identity transition would be doing nothing at all and the
    equivalence test would be proving nothing.
    """
    mixed = replay_of(fixtures.mixed_source_session(), tmp_path / "mixed")
    uniform = replay_of(
        fixtures.mixed_source_session(uniform="VISUAL", tag="mixed"), tmp_path / "uniform"
    )
    assert candles_of(_captured(mixed, "candles")) != candles_of(_captured(uniform, "candles"))

    def minute_bars(storage: Any) -> dict[int, float]:
        return {
            row.openTime: row.coverage
            for row in _captured(storage, "candles")
            if isinstance(row, Candle) and row.timeframe == "M1" and row.slotId == 1
        }

    before, after = minute_bars(uniform), minute_bars(mixed)
    damaged = {key for key, coverage in after.items() if coverage < before[key]}
    assert len(damaged) == len(fixtures.SOURCE_BLOCKS) - 1, "one broken bar per hand-over"
    assert all(before[key] == 1.0 for key in damaged)
    assert all(after[key] <= 0.5 for key in damaged)


def test_a_replayed_reading_carries_replay_provenance_while_its_identity_stays_recorded(
    tmp_path: Path,
) -> None:
    """Both halves at once: the label is REPLAY, the identity is what was recorded."""
    rows = fixtures.mixed_source_session(seconds=200, tag="prov")
    offline = replay_of(rows, tmp_path)
    samples = [row for row in _captured(offline, "samples") if isinstance(row, PriceSample)]
    assert samples
    # Nothing downstream sees a reading claiming to have come off a broker surface just now.
    assert {sample.sourceType for sample in samples} == {"REPLAY"}
    # And the identity the series turned on is still the one the record actually holds.
    assert {sample.identitySource for sample in samples} == {"VISUAL", "DOM"}
    assert all(
        candle.sourceType == "REPLAY"
        for candle in _captured(offline, "candles")
        if isinstance(candle, Candle)
    )


def test_a_live_reading_may_never_carry_a_separate_identity_source() -> None:
    # Otherwise anything that could reach the local ingest endpoint could fake a series reset.
    row = fixtures.mixed_source_session(seconds=1, tag="guard")[0]
    assert row.sourceType == "VISUAL"
    assert row.identitySourceType is None
    with pytest.raises(ValidationError):
        row.model_copy(update={"identitySourceType": "DOM"}).model_validate(
            row.model_dump() | {"identitySourceType": "DOM"}
        )
    with pytest.raises(ValidationError):
        MarketObservation.model_validate(row.model_dump() | {"identitySourceType": "DOM"})


def test_the_identity_source_is_never_written_to_the_durable_record() -> None:
    # The stored contract is byte-for-byte what it always was: this is an in-flight distinction,
    # not a new column, so a replay cannot change the shape of the record it reads.
    row = fixtures.mixed_source_session(seconds=1, tag="store")[0]
    replayed = replay_provenance(row, "REPLAY")
    assert replayed.identitySourceType == "VISUAL"
    assert "identitySourceType" not in replayed.model_dump()
    assert "identitySourceType" not in replayed.model_dump(mode="json")
