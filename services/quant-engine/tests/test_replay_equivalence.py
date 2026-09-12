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

import json
from pathlib import Path
from typing import Any

import replay_fixtures as fixtures
from quant_engine.features.models import FeatureSnapshot
from quant_engine.market_api import MarketEngine, ObservationBatch
from quant_engine.market_models import MarketObservation
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

CAPTURE = frozenset({"features", "ensembles", "opportunity_boards", "paper_trades"})
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
    # The live engine holds it as a literal inside ``advance_live``. If that ever moves, the two
    # paths would close bars at different points in the series and this replay would quietly
    # stop being a replay.
    source = (Path(__file__).parents[1] / "src" / "quant_engine" / "market_api.py").read_text()
    assert f"now - {AVAILABILITY_LAG_MS}" in source.replace("_", "")
    assert AVAILABILITY_LAG_MS == 3_000


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
