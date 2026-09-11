"""T-CG, T-AC, T-AD, T-AX: the synthetic acceptance case, driven through the real pipeline.

Nothing here injects a board, a feature or an ensemble. Broker observations go in at one end
and resolved paper outcomes come out at the other, through Phase 5's builder, Phase 6's
features, Phase 7's ensemble and Phase 8's ranking exactly as live capture drives them.

The two slots are deliberately out of step. Slot 2 reports 1.2 seconds after slot 1, so its
bar for a shared close is only processed after that close has passed — which is the whole
reason ``decisionAvailableAt`` exists. A build that quietly entered at ``board.asOf`` would
produce different prices here and fail.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import paper_fixtures as fixtures
from features_fixtures import BASE_MS, CONTEXT_A, GOOD
from quant_engine.market_api import MarketEngine, ObservationBatch
from quant_engine.market_models import MarketObservation, SourceType
from quant_engine.market_storage import ParquetStorage
from quant_engine.paper import PaperSettings

LEADER, LAGGARD = 1, 2
CONTEXTS = {LEADER: CONTEXT_A, LAGGARD: UUID("33333333-3333-4333-8333-333333333333")}
ASSETS = {LEADER: "EUR/USD OTC", LAGGARD: "GBP/JPY OTC"}
LAG_MS = 1_200
"""How far behind slot 2's capture cadence runs. Real nine-slot capture is round-robin, so
slots never close their bars at the same instant."""
SECONDS = 330


def observation(
    slot: int, stamp: int, price: float, source: SourceType = "REPLAY"
) -> MarketObservation:
    at = datetime.fromtimestamp(stamp / 1000, UTC)
    return MarketObservation(
        id=uuid4(),
        platform="capitalbear",
        slotId=slot,
        assetName=ASSETS[slot],
        contextId=CONTEXTS[slot],
        observedAt=at,
        parsedAt=at,
        sourceType=source,
        price=price,
        payout=None,
        timerSeconds=None,
        parserConfidence=1.0,
        dataQuality=GOOD,
        captureLatencyMs=0,
        parseLatencyMs=0,
        calibrationProfileId=None,
        parserVersion="paper-acceptance-1",
    )


def rising(second: int) -> float:
    return 1.08 * (1 + 0.00004 * second)


def falling(second: int) -> float:
    return 1.08 * (1 - 0.00004 * second)


def choppy(second: int) -> float:
    """A market with no direction, so the cohort completes without a second contender."""
    return 1.30 + (0.0002 if second % 2 else -0.0002)


def run(
    tmp_path: Path,
    leader: Callable[[int], float] = rising,
    *,
    settings: PaperSettings | None = None,
    seconds: int = SECONDS,
) -> MarketEngine:
    engine = MarketEngine(ParquetStorage(tmp_path), settings)
    rows = []
    for second in range(seconds):
        rows.append(observation(LEADER, BASE_MS + second * 1000, leader(second)))
        rows.append(observation(LAGGARD, BASE_MS + second * 1000 + LAG_MS, choppy(second)))
    for start in range(0, len(rows), 18):
        engine.ingest(ObservationBatch(observations=rows[start : start + 18]), BASE_MS)
    return engine


# --- T-CG the acceptance case ----------------------------------------------------------


def test_a_rising_market_selected_up_resolves_as_a_win_end_to_end(tmp_path: Path) -> None:
    engine = run(tmp_path)
    resolved = [t for t in engine.paper.history if t.status == "RESOLVED"]
    assert resolved, "the real chain must produce at least one resolved paper trade"
    trade = resolved[0]
    assert trade.direction == "UP"
    assert trade.outcome == "WIN"
    assert trade.entryPrice is not None and trade.expiryPrice is not None
    assert trade.expiryPrice > trade.entryPrice
    assert engine.paper.stats("capitalbear").wins == len(resolved)
    assert engine.paper.stats("capitalbear").losses == 0


def test_the_mirrored_falling_market_selected_down_also_wins(tmp_path: Path) -> None:
    engine = run(tmp_path, falling)
    resolved = [t for t in engine.paper.history if t.status == "RESOLVED"]
    assert resolved
    assert {t.direction for t in resolved} == {"DOWN"}
    assert {t.outcome for t in resolved} == {"WIN"}


def test_the_same_series_labelled_the_other_way_loses(tmp_path: Path) -> None:
    # The layer is not scoring the market, it is scoring the *call*. Same prices, opposite
    # selection, opposite result — which is what makes a WIN mean something.
    engine = run(tmp_path)
    trade = next(t for t in engine.paper.history if t.status == "RESOLVED")
    assert trade.entryPrice is not None and trade.expiryPrice is not None
    from quant_engine.paper import outcome_for

    assert outcome_for("UP", trade.entryPrice, trade.expiryPrice) == "WIN"
    assert outcome_for("DOWN", trade.entryPrice, trade.expiryPrice) == "LOSS"


# --- T-AX, T-H the decision is not available at the close ------------------------------


def test_the_decision_time_trails_the_board_close_and_the_entry_trails_the_decision(
    tmp_path: Path,
) -> None:
    engine = run(tmp_path)
    resolved = [t for t in engine.paper.history if t.status == "RESOLVED"]
    assert resolved
    for trade in resolved:
        assert trade.decisionAvailableAt > trade.boardAsOf, "the laggard closes its bar later"
        assert trade.entryTime is not None
        assert trade.entryTime >= trade.decisionAvailableAt
        assert trade.expiryTargetTime == trade.entryTime + 5_000
        assert trade.expiryTime is not None and trade.expiryTime >= trade.expiryTargetTime


def test_no_entry_price_is_ever_the_price_at_the_board_close(tmp_path: Path) -> None:
    engine = run(tmp_path)
    for trade in engine.paper.history:
        if trade.entryTime is None:
            continue
        assert trade.entryTime != trade.boardAsOf


# --- T-AD, T-BQ ordering and replay ----------------------------------------------------


def test_the_same_observations_replay_to_the_same_outcomes(tmp_path: Path) -> None:
    first = run(tmp_path / "a").paper.history
    second = run(tmp_path / "b").paper.history
    assert [t.model_dump(mode="json") for t in first] == [t.model_dump(mode="json") for t in second]
    assert len(first) > 0


def test_one_phase_eight_selection_produces_at_most_one_paper_trade(tmp_path: Path) -> None:
    engine = run(tmp_path)
    keys = [(t.platform, t.boardAsOf) for t in engine.paper.history]
    keys.extend((t.platform, t.boardAsOf) for t in engine.paper.live.values())
    assert len(keys) == len(set(keys))
    # Every repeat delivery of a decided board is counted rather than silently dropped.
    assert engine.paper.duplicateSelections > 0


def test_paper_money_is_recorded_when_and_only_when_it_is_configured(tmp_path: Path) -> None:
    without = run(tmp_path / "a")
    assert all(t.realizedPaperPnl is None for t in without.paper.history)
    assert without.paper.stats().netPaperPnl is None

    configured = run(
        tmp_path / "b",
        settings=PaperSettings(paperCurrency="THB", paperStake=50, paperPayoutRate=0.82),
    )
    resolved = [t for t in configured.paper.history if t.status == "RESOLVED"]
    assert resolved and all(t.realizedPaperPnl == 41.0 for t in resolved)
    stats = configured.paper.stats()
    assert stats.netPaperPnl == 41.0 * len(resolved)
    assert stats.grossPaperLoss == 0


# --- T-Y the reset chain reaches Phase 9 -----------------------------------------------


def test_resetting_a_slot_cancels_its_live_paper_trade_through_the_market_engine(
    tmp_path: Path,
) -> None:
    engine = run(tmp_path)
    # Place one intent at the platform's current market time, so there is definitely something
    # live for the reset to cancel rather than a run that happened to stop between trades.
    clock = engine.paper._clock["capitalbear"]
    board = fixtures.board(slot=LEADER, as_of=clock)
    engine.persist_paper(engine.paper.on_board(board, clock))
    live = len(engine.paper.live)
    assert live > 0
    before = len([t for t in engine.paper.history if t.status == "RESOLVED"])
    engine.reset_slots("capitalbear", [LEADER])
    assert engine.paper.live == {}
    cancelled = [t for t in engine.paper.history if t.status == "CANCELLED"]
    assert len(cancelled) == live
    assert all("SLOT_RESET" in t.reasons for t in cancelled)
    assert len([t for t in engine.paper.history if t.status == "RESOLVED"]) == before
