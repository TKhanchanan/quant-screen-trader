"""T-DS..T-DW: nothing at replay index N ever saw index N+1.

This is the file that decides whether a backtest is worth anything at all. A lookahead bug does
not announce itself — it shows up as an excellent win rate — so the guarantees are asserted
directly against the replayed record rather than described in a docstring.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import replay_fixtures as fixtures
from quant_engine.market_models import MarketObservation, PriceSample, price_sample
from quant_engine.paper.policy import PaperSettings
from quant_engine.paper.resolver import usable
from quant_engine.replay import (
    InMemoryObservationSource,
    ReplayClock,
    ReplayEngine,
    ReplayManifest,
    ReplayResult,
    ReplayStorage,
    market_time_of,
    replay_provenance,
)

ACCOUNTING = PaperSettings(paperCurrency="THB", paperStake=50, paperPayoutRate=0.82)


def run(rows: list[MarketObservation], root: Path, **overrides: Any) -> ReplayResult:
    values: dict[str, Any] = {
        "warmupDurationMs": 0,
        "sourceMode": "SYNTHETIC",
        "includeIqOption": False,
        "paperSettings": ACCOUNTING,
    }
    values.update(overrides)
    spec = ReplayManifest(**values)
    source = InMemoryObservationSource(rows, mode=spec.sourceMode, platforms=spec.platforms)
    return ReplayEngine(spec, source, root=root, persist=False).run()


def canonical(rows: list[MarketObservation], trade: Any) -> list[PriceSample]:
    """The canonical samples the paper layer could legitimately have seen for one trade.

    Built by putting the source rows through Phase 5's own ``price_sample`` gate rather than a
    restatement of it, so this check cannot drift away from what the pipeline actually accepts.
    """
    samples = []
    for row in rows:
        if (row.platform, row.slotId, row.assetName, row.contextId) != (
            trade.platform,
            trade.slotId,
            trade.assetName,
            trade.contextId,
        ):
            continue
        sample = price_sample(replay_provenance(row, "SYNTHETIC"))
        if sample is not None and usable(sample):
            samples.append(sample)
    return sorted(samples, key=lambda item: item.timestamp)


# --- T-DS the audit --------------------------------------------------------------------


def test_a_clean_replay_produces_a_clean_causality_audit(tmp_path: Path) -> None:
    result = run(fixtures.small_history(), tmp_path)
    audit = result.causality
    assert audit.checks > 0
    assert audit.clean
    assert audit.violations == 0
    assert audit.featureAheadOfClock == 0
    assert audit.ensembleAheadOfClock == 0
    assert audit.boardAheadOfClock == 0
    assert audit.entryBeforeDecision == 0
    assert audit.expiryBeforeTarget == 0
    assert audit.currentMarketTime is not None
    for stamp in (audit.latestFeatureAsOf, audit.latestEnsembleAsOf, audit.latestBoardAsOf):
        assert stamp is not None and stamp <= audit.currentMarketTime


def test_the_audit_is_a_real_check_and_not_a_tautology(tmp_path: Path) -> None:
    # Fed records that claim things the clock has not reached, the audit has to say so. An audit
    # that could only ever pass would be decoration rather than evidence.
    import opportunity_fixtures as ranking

    clock = ReplayClock()
    clock.advance(ranking.EPOCH)
    storage = ReplayStorage(tmp_path, clock, persist=False)
    ahead = ranking.ensemble(as_of=ranking.EPOCH + 60_000)
    storage.audit.observe("ensembles", ahead)
    assert storage.audit.ensembleAhead == 1
    assert storage.audit.violations == 1

    result = run(fixtures.small_history(), tmp_path / "run")
    trade = next(item for item in result.trades if item.status == "RESOLVED")
    later = ReplayClock()
    later.advance(trade.expiryTime or 0)
    checks = ReplayStorage(tmp_path / "checks", later, persist=False)
    checks.audit.observe(
        "paper_trades", trade.model_copy(update={"entryTime": trade.decisionAvailableAt - 1})
    )
    assert checks.audit.entryBefore == 1
    checks.audit.observe(
        "paper_trades",
        trade.model_copy(update={"expiryTime": (trade.expiryTargetTime or 0) - 1}),
    )
    assert checks.audit.expiryBefore == 1
    assert checks.audit.violations >= 2


# --- T-DT no future event --------------------------------------------------------------


def test_a_violent_move_in_the_future_cannot_change_an_earlier_decision(tmp_path: Path) -> None:
    rows = fixtures.session(seconds=420, tag="future")
    cut = fixtures.BASE_MS + 240_000
    shocked = [
        row
        if market_time_of(row) <= cut
        else row.model_copy(update={"price": round((row.price or 1.0) * 4.0, 6)})
        for row in rows
    ]
    baseline = run(rows, tmp_path / "a")
    shock = run(shocked, tmp_path / "b")

    def settled(result: ReplayResult) -> dict[str, tuple[Any, ...]]:
        return {
            str(trade.paperTradeId): (
                trade.status,
                trade.outcome,
                trade.entryTime,
                trade.entryPrice,
                trade.expiryTime,
                trade.expiryPrice,
                trade.rankScore,
                trade.primaryRegime,
            )
            for trade in result.trades
            if trade.expiryTime is not None and trade.expiryTime <= cut
        }

    before, after = settled(baseline), settled(shock)
    assert before, "the fixture must actually settle trades before the cut"
    assert before == after


def test_appending_more_history_leaves_every_earlier_outcome_untouched(tmp_path: Path) -> None:
    short = fixtures.session(seconds=300, tag="grow")
    long = fixtures.session(seconds=600, tag="grow")
    first = run(short, tmp_path / "a")
    second = run(long, tmp_path / "b")
    horizon = fixtures.BASE_MS + 280_000
    early = {
        str(trade.paperTradeId): (trade.outcome, trade.entryPrice, trade.expiryPrice)
        for trade in first.trades
        if trade.status == "RESOLVED" and (trade.expiryTime or 0) <= horizon
    }
    later = {
        str(trade.paperTradeId): (trade.outcome, trade.entryPrice, trade.expiryPrice)
        for trade in second.trades
        if trade.status == "RESOLVED" and (trade.expiryTime or 0) <= horizon
    }
    assert early
    assert all(later[key] == value for key, value in early.items())


# --- T-DU entry has no lookahead -------------------------------------------------------


def test_every_entry_is_the_first_canonical_price_after_the_decision_existed(
    tmp_path: Path,
) -> None:
    rows = fixtures.session(seconds=420, tag="entry")
    result = run(rows, tmp_path)
    opened = [trade for trade in result.trades if trade.entryTime is not None]
    assert opened
    for trade in opened:
        samples = canonical(rows, trade)
        eligible = [sample for sample in samples if sample.timestamp >= trade.decisionAvailableAt]
        assert eligible, "a filled trade must have had a price to fill on"
        # Not the pre-signal price, not a better one found later, not the bar's close: the
        # first canonical price that existed at or after the decision did.
        assert trade.entryTime == eligible[0].timestamp
        assert trade.entryPrice == eligible[0].price
        assert trade.entryTime >= trade.decisionAvailableAt


def test_no_entry_is_ever_priced_before_its_board_closed(tmp_path: Path) -> None:
    result = run(fixtures.session(seconds=420, tag="chrono"), tmp_path)
    for trade in result.trades:
        assert trade.decisionAvailableAt >= trade.boardAsOf
        if trade.entryTime is not None:
            assert trade.entryTime >= trade.boardAsOf


# --- T-DV expiry has no lookahead ------------------------------------------------------


def test_every_expiry_is_the_first_canonical_price_at_or_after_the_horizon(
    tmp_path: Path,
) -> None:
    rows = fixtures.session(seconds=420, tag="expiry")
    result = run(rows, tmp_path)
    resolved = [trade for trade in result.trades if trade.status == "RESOLVED"]
    assert resolved
    for trade in resolved:
        assert trade.expiryTargetTime == (trade.entryTime or 0) + trade.durationMs
        samples = canonical(rows, trade)
        eligible = [
            sample
            for sample in samples
            if trade.expiryTargetTime is not None and sample.timestamp >= trade.expiryTargetTime
        ]
        assert eligible
        # A price a millisecond before the horizon never settles the trade, however favourable.
        assert trade.expiryTime == eligible[0].timestamp
        assert trade.expiryPrice == eligible[0].price


def test_the_recorded_lags_are_never_negative(tmp_path: Path) -> None:
    audit = run(fixtures.small_history(), tmp_path).causality
    assert audit.maxEntryLagMs is not None and audit.maxEntryLagMs >= 0
    assert audit.maxExpiryLagMs is not None and audit.maxExpiryLagMs >= 0
