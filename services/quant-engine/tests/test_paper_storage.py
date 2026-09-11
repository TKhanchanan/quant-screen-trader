"""T-BZ, T-CA, T-AG, T-AH: the durable record, and what a restart is allowed to forget.

A paper outcome is the only evidence this system produces that could not be re-derived later,
so the round trip is asserted field by field rather than by spot check. The restart rule is
asserted as behaviour: an open trade may disappear from memory, but never without a persisted
cancellation saying so.
"""

from __future__ import annotations

from pathlib import Path

import paper_fixtures as fixtures
import test_paper_pipeline as pipeline
from quant_engine.market_api import MarketEngine
from quant_engine.market_storage import ParquetStorage
from quant_engine.paper import PaperEngine, PaperSettings, PaperTrade
from quant_engine.paper.models import PaperTradeEvent


def with_pending_trade(engine: MarketEngine) -> int:
    """Leave one intent unfinished at the moment of the "crash".

    Whether a capture run happens to stop mid-trade is an accident of where it was cut, and a
    restart test that only sometimes has an open trade to lose would only sometimes be testing
    anything. The board is placed at the platform's current market time, through the same
    public entry point the pipeline uses.
    """
    clock = engine.paper._clock["capitalbear"]
    board = fixtures.board(slot=1, as_of=clock)
    engine.persist_paper(engine.paper.on_board(board, clock))
    assert len(engine.paper.live) == 1
    return clock


def resolved_trade(settings: PaperSettings | None = None) -> PaperEngine:
    engine = PaperEngine(settings if settings is not None else PaperSettings())
    board = fixtures.board(direction="UP")
    available = fixtures.decision_time(board)
    engine.on_board(board, available)
    engine.on_market_sample(fixtures.sample(available + 40, 1.08420))
    engine.on_market_sample(fixtures.sample(available + 5_090, 1.08451))
    return engine


# --- T-BZ the round trip ---------------------------------------------------------------


def test_a_resolved_trade_survives_parquet_with_every_semantic_field_intact(
    tmp_path: Path,
) -> None:
    storage = ParquetStorage(tmp_path, batch_size=1)
    engine = resolved_trade(PaperSettings(paperCurrency="THB", paperStake=50, paperPayoutRate=0.82))
    original = engine.history[0]
    assert original.outcome == "WIN"
    storage.append("paper_trades", original)
    storage.flush()

    rows = [row for row in storage.reload("paper_trades") if isinstance(row, PaperTrade)]
    assert len(rows) == 1
    assert rows[0].model_dump(mode="json") == original.model_dump(mode="json")


def test_lifecycle_events_survive_parquet(tmp_path: Path) -> None:
    storage = ParquetStorage(tmp_path, batch_size=1)
    engine = PaperEngine()
    board = fixtures.board()
    available = fixtures.decision_time(board)
    update = engine.on_board(board, available)
    for event in update.events:
        storage.append("paper_trade_events", event)
    storage.flush()

    rows = [row for row in storage.reload("paper_trade_events") if isinstance(row, PaperTradeEvent)]
    assert [row.eventType for row in rows] == ["PENDING_CREATED"]
    assert rows[0].eventTime == available
    assert rows[0].paperVersion == "qst-paper-v1"


def test_the_market_engine_persists_every_transition_of_a_real_trade(tmp_path: Path) -> None:
    engine = pipeline.run(tmp_path)
    engine.storage.flush()
    trades = [row for row in engine.storage.reload("paper_trades") if isinstance(row, PaperTrade)]
    events = [
        row
        for row in engine.storage.reload("paper_trade_events")
        if isinstance(row, PaperTradeEvent)
    ]
    resolved = [t for t in trades if t.status == "RESOLVED"]
    assert resolved, "resolved outcomes must reach the durable record"
    identity = resolved[0].paperTradeId
    stages = [e.eventType for e in events if e.paperTradeId == identity]
    assert stages == ["PENDING_CREATED", "OPENED", "RESOLVED"]
    # Versions travel with the row, so a later contract can never silently pool with it.
    assert resolved[0].paperVersion == "qst-paper-v1"
    assert resolved[0].rankingVersion == "qst-ranking-v1"


# --- T-CA restart ----------------------------------------------------------------------


def test_a_restart_keeps_resolved_history_and_cancels_what_it_cannot_continue(
    tmp_path: Path,
) -> None:
    first = pipeline.run(tmp_path)
    with_pending_trade(first)
    first.storage.flush()
    resolved_before = len([t for t in first.paper.history if t.status == "RESOLVED"])
    live_before = len(first.paper.live)
    assert resolved_before and live_before

    second = MarketEngine(ParquetStorage(tmp_path))
    restored = second.restore_paper()
    assert restored >= resolved_before + live_before

    # Resolved stays resolved: the market really did move that way, and a restart is not
    # evidence about it.
    assert second.paper.stats("capitalbear").resolved == resolved_before
    # Nothing live survives, and nothing vanishes silently either.
    assert second.paper.live == {}
    unresolvable = [t for t in second.paper.history if "RESTART_UNRESOLVABLE" in t.reasons]
    assert len(unresolvable) == live_before
    assert all(t.status == "CANCELLED" for t in unresolvable)
    assert all(t.outcome == "UNRESOLVED" for t in unresolvable)


def test_a_restart_cancellation_is_itself_persisted(tmp_path: Path) -> None:
    first = pipeline.run(tmp_path)
    with_pending_trade(first)
    first.storage.flush()
    second = MarketEngine(ParquetStorage(tmp_path))
    second.restore_paper()
    second.storage.flush()

    events = [
        row
        for row in second.storage.reload("paper_trade_events")
        if isinstance(row, PaperTradeEvent) and row.reason == "RESTART_UNRESOLVABLE"
    ]
    assert events
    assert all(event.eventType == "CANCELLED" for event in events)


def test_a_restart_does_not_re_emit_a_settlement_for_an_already_resolved_trade(
    tmp_path: Path,
) -> None:
    first = pipeline.run(tmp_path)
    first.storage.flush()
    second = MarketEngine(ParquetStorage(tmp_path))
    update = second.paper.restore([t for t in first.paper.history if t.status == "RESOLVED"])
    assert update.settlements == ()


def test_restoring_the_same_trade_twice_keeps_the_terminal_row(tmp_path: Path) -> None:
    engine = resolved_trade()
    trade = engine.history[0]
    pending = trade.model_copy(
        update={"status": "PENDING_ENTRY", "outcome": "UNRESOLVED", "entryPrice": None}
    )
    storage = ParquetStorage(tmp_path, batch_size=8)
    for row in (pending, trade):
        storage.append("paper_trades", row)
    storage.flush()

    market = MarketEngine(storage)
    assert market.restore_paper() == 1
    assert market.paper.live == {}
    assert market.paper.stats().resolved == 1


def test_an_unreadable_paper_history_never_stops_the_engine_from_starting(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "paper_trades" / "platform=capitalbear"
    folder.mkdir(parents=True)
    (folder / "broken.parquet").write_bytes(b"not a parquet file")
    market = MarketEngine(ParquetStorage(tmp_path))
    assert market.restore_paper() == 0
    assert market.paper.live == {}
