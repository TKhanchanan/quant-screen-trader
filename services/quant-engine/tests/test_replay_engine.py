"""T-DF..T-DR: the replay driver — order, clock, windows, identity and determinism.

Every test here drives the *real* Phase 5-9 chain. Nothing is stubbed: a broken feature engine,
a changed ranking gate or a different paper horizon breaks these tests, which is the point.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
import replay_fixtures as fixtures
from quant_engine.configuration import Platform
from quant_engine.market_models import TIMEFRAMES
from quant_engine.paper.policy import PAPER_DURATION_MS, PaperSettings
from quant_engine.replay import (
    AVAILABILITY_LAG_MS,
    WARMUP_BARS,
    InMemoryObservationSource,
    ReplayClock,
    ReplayEngine,
    ReplayManifest,
    ReplayResult,
    WindowedPaperEngine,
    derive_window,
    market_time_of,
    replay_run_id,
    settlement_tail_ms,
    slowest_timeframe,
    warmup_duration_ms,
)

ACCOUNTING = PaperSettings(paperCurrency="THB", paperStake=50, paperPayoutRate=0.82)


def manifest(**overrides: Any) -> ReplayManifest:
    values: dict[str, Any] = {
        "warmupDurationMs": 0,
        "sourceMode": "SYNTHETIC",
        "includeIqOption": False,
        "paperSettings": ACCOUNTING,
    }
    values.update(overrides)
    return ReplayManifest(**values)


def replay(
    rows: list[Any], root: Path, *, batch_size: int = 4_096, **overrides: Any
) -> ReplayEngine:
    spec = manifest(**overrides)
    source = InMemoryObservationSource(rows, mode=spec.sourceMode, platforms=spec.platforms)
    return ReplayEngine(spec, source, root=root, persist=False, batch_size=batch_size)


def signature(result: ReplayResult) -> tuple[Any, ...]:
    """Everything a replay means, and nothing about how long it took."""
    run = result.run
    return (
        str(run.replayRunId),
        run.inputFingerprint,
        run.processedEvents,
        run.acceptedEvents,
        run.ensemblesProduced,
        run.boardsFinalized,
        run.boardsSelected,
        run.paperOpened,
        run.paperResolved,
        result.analysis.fingerprint if result.analysis else "",
        str(result.snapshot.snapshotId) if result.snapshot else "",
        tuple(
            (
                str(trade.paperTradeId),
                trade.status,
                trade.outcome,
                trade.entryTime,
                trade.entryPrice,
                trade.expiryTime,
                trade.expiryPrice,
            )
            for trade in sorted(
                result.trades, key=lambda item: (str(item.paperTradeId), item.status)
            )
        ),
    )


# --- T-DF basic replay -----------------------------------------------------------------


def test_a_replay_drives_the_real_pipeline_end_to_end(tmp_path: Path) -> None:
    result = replay(fixtures.small_history(), tmp_path).run()
    assert result.run.status == "COMPLETED"
    assert result.run.ensemblesProduced > 0
    assert result.run.boardsFinalized > 0
    assert result.run.paperResolved > 0
    assert result.run.featureVersion == "qfe-v2"
    assert result.run.paperVersion == "qst-paper-v1"
    assert result.run.replayVersion == "qst-replay-v1"


def test_events_reach_the_engine_in_market_time_order(tmp_path: Path) -> None:
    rows = fixtures.small_history()
    engine = replay(rows, tmp_path)
    engine.run()
    assert engine.clock.regressions == 0
    assert engine.clock.started == min(market_time_of(row) for row in rows)
    assert engine.clock.now == max(market_time_of(row) for row in rows)


def test_replay_does_not_wait_out_the_history_it_replays(tmp_path: Path) -> None:
    # Seven days of market time. If anything slept for a historical interval this would not
    # return, and the ratio is the honest measure that it did not.
    started = time.perf_counter()
    result = replay(fixtures.small_history(), tmp_path).run()
    elapsed = time.perf_counter() - started
    market_span = (result.run.finishedMarketTime or 0) - (result.run.startedMarketTime or 0)
    assert market_span > 0
    assert market_span / 1000 > elapsed


# --- T-DG determinism ------------------------------------------------------------------


def test_the_same_manifest_over_the_same_history_replays_identically(tmp_path: Path) -> None:
    rows = fixtures.small_history()
    first = replay(rows, tmp_path / "a").run()
    second = replay(rows, tmp_path / "b").run()
    assert signature(first) == signature(second)


def test_batch_size_cannot_change_a_single_semantic_output(tmp_path: Path) -> None:
    rows = fixtures.small_history()
    one = replay(rows, tmp_path / "a", batch_size=1).run()
    many = replay(rows, tmp_path / "b", batch_size=997).run()
    assert signature(one) == signature(many)


def test_moving_the_host_clock_changes_nothing_a_replay_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = fixtures.small_history()
    baseline = replay(rows, tmp_path / "a").run()
    monkeypatch.setattr(time, "time", lambda: 4_102_444_800.0)
    shifted = replay(rows, tmp_path / "b").run()
    assert signature(baseline) == signature(shifted)
    # The only thing the host clock may touch is the stopwatch on the run metadata.
    assert shifted.run.createdAt != baseline.run.createdAt


def test_changing_one_event_changes_the_fingerprint_and_the_run_id(tmp_path: Path) -> None:
    rows = fixtures.small_history()
    before = replay(rows, tmp_path / "a").run().run
    edited = [*rows]
    original = edited[300].price
    assert original is not None
    edited[300] = edited[300].model_copy(update={"price": original + 0.25})
    after = replay(edited, tmp_path / "b").run().run
    assert before.inputFingerprint != after.inputFingerprint
    assert before.replayRunId != after.replayRunId


def test_changing_a_setting_changes_the_run_id_without_touching_the_input(tmp_path: Path) -> None:
    rows = fixtures.small_history()
    before = replay(rows, tmp_path / "a").run().run
    after = replay(rows, tmp_path / "b", payoutScenarios=[0.8]).run().run
    assert before.inputFingerprint == after.inputFingerprint
    assert before.settingsFingerprint != after.settingsFingerprint
    assert before.replayRunId != after.replayRunId


def test_the_run_id_is_content_addressed_and_never_a_filesystem_property() -> None:
    def identity(
        *,
        fingerprint: str = "abc",
        settings: str = "def",
        end: int = 2,
        platforms: tuple[Platform, ...] = ("iqoption", "capitalbear"),
    ) -> UUID:
        return replay_run_id(
            input_fingerprint=fingerprint,
            settings_hash=settings,
            evaluation_start=1,
            evaluation_end=end,
            platforms=platforms,
        )

    assert identity() == identity()
    # The platform set, not the order somebody happened to write it in.
    assert identity(platforms=("capitalbear", "iqoption")) == identity()
    assert identity(end=3) != identity()
    assert identity(fingerprint="abd") != identity()
    assert identity(settings="deg") != identity()


# --- T-DH warm-up ----------------------------------------------------------------------


def test_the_default_warm_up_is_derived_from_the_frozen_contracts_not_guessed() -> None:
    assert WARMUP_BARS == 50
    # CapitalBear decides on S5 while reading M1 and M5; IQ Option decides on M1 while reading
    # M5 and M10. Fifty bars of the slowest of those is the requirement, not a round number.
    assert slowest_timeframe("capitalbear") == "M5"
    assert slowest_timeframe("iqoption") == "M10"
    assert warmup_duration_ms(("capitalbear",)) == TIMEFRAMES["M5"] * 1_000 * 50
    assert warmup_duration_ms(("iqoption",)) == TIMEFRAMES["M10"] * 1_000 * 50
    assert warmup_duration_ms(("capitalbear", "iqoption")) == TIMEFRAMES["M10"] * 1_000 * 50
    assert warmup_duration_ms(("capitalbear", "iqoption")) == 30_000_000


def test_warm_up_matures_the_features_and_produces_no_measured_trade(tmp_path: Path) -> None:
    rows = fixtures.session(seconds=600, tag="warm")
    start = fixtures.BASE_MS
    engine = replay(rows, tmp_path, warmupDurationMs=200_000, fromTime=start + 200_000)
    result = engine.run()
    assert result.window.warmupStart == start
    assert result.run.warmupEvents > 0
    assert result.run.featuresReadyAt is not None
    # Nothing decided during warm-up may become a measured trade — and it is refused at
    # creation, so there is no warm-up trade to filter out later.
    assert all(
        trade.decisionAvailableAt >= result.window.evaluationStart for trade in result.trades
    )
    assert engine.engine is not None
    assert cast(WindowedPaperEngine, engine.engine.paper).outsideWindow > 0


def test_feature_state_survives_into_the_evaluation_window(tmp_path: Path) -> None:
    # The whole purpose of warm-up is the state it leaves behind. Resetting the feature engine
    # at the evaluation boundary would throw away exactly what was just paid for.
    rows = fixtures.session(seconds=600, tag="carry")
    engine = replay(
        rows,
        tmp_path,
        warmupDurationMs=300_000,
        fromTime=fixtures.BASE_MS + 300_000,
    )
    result = engine.run()
    assert engine.engine is not None
    bars = [
        frame.bar_count
        for state in engine.engine.features.slots.values()
        for frame in state.timeframes.values()
    ]
    assert max(bars) > WARMUP_BARS
    first = min(
        (trade.decisionAvailableAt for trade in result.trades), default=result.window.evaluationEnd
    )
    # A decision taken moments after the window opened, on indicators that were already warm.
    assert first < result.window.evaluationStart + 120_000


# --- T-DI evaluation window and settlement tail ----------------------------------------


def test_no_new_selection_is_taken_after_the_evaluation_window_closes(tmp_path: Path) -> None:
    rows = fixtures.session(seconds=600, tag="end")
    end = fixtures.BASE_MS + 420_000
    result = replay(rows, tmp_path, toTime=end).run()
    assert result.trades
    assert all(trade.decisionAvailableAt <= end for trade in result.trades)


def test_a_trade_opened_inside_the_window_still_settles_in_the_tail(tmp_path: Path) -> None:
    rows = fixtures.session(seconds=600, tag="tail")
    end = fixtures.BASE_MS + 420_000
    result = replay(rows, tmp_path, toTime=end).run()
    assert result.window.settlementEnd == end + settlement_tail_ms(("capitalbear",), ACCOUNTING)
    assert result.window.settlementTailMs == 15_000
    settled_after = [
        trade
        for trade in result.trades
        if trade.status == "RESOLVED" and (trade.expiryTime or 0) > end
    ]
    # Selected inside the window, answered outside it, and counted: the market took its time,
    # the decision did not.
    assert settled_after
    assert all(trade.decisionAvailableAt <= end for trade in settled_after)


def test_the_settlement_tail_is_derived_from_the_paper_contract() -> None:
    assert settlement_tail_ms(("capitalbear",), PaperSettings()) == 5_000 + 5_000 + 5_000
    assert settlement_tail_ms(("iqoption",), PaperSettings()) == 10_000 + 60_000 + 10_000
    assert settlement_tail_ms(("capitalbear", "iqoption"), PaperSettings()) == 80_000


# --- T-DJ horizons ---------------------------------------------------------------------


def test_capitalbear_still_measures_a_five_second_question(tmp_path: Path) -> None:
    result = replay(fixtures.small_history(), tmp_path).run()
    assert result.trades
    assert {trade.durationMs for trade in result.trades} == {5_000}
    assert PAPER_DURATION_MS["capitalbear"] == 5_000


def test_iq_option_still_measures_a_sixty_second_question(tmp_path: Path) -> None:
    result = replay(
        fixtures.iqoption_history(),
        tmp_path,
        includeCapitalBear=False,
        includeIqOption=True,
    ).run()
    assert result.trades
    assert {trade.durationMs for trade in result.trades} == {60_000}
    assert PAPER_DURATION_MS["iqoption"] == 60_000


# --- T-DK identity ---------------------------------------------------------------------


def test_a_slot_that_changes_asset_starts_again_from_nothing(tmp_path: Path) -> None:
    engine = replay(
        fixtures.iqoption_history(),
        tmp_path,
        includeCapitalBear=False,
        includeIqOption=True,
    )
    result = engine.run()
    assert engine.engine is not None
    state = engine.engine.features.slots[("iqoption", 1)]
    assert state.assetName == "USD/CHF"
    # The physical slot was reused; the series was not. Nothing from the previous asset can be
    # in a state whose whole history is shorter than the new asset's own run.
    assert state.timeframes["M1"].bar_count <= 6
    assert result.run.paperCancelled >= 1
    for trade in result.trades:
        assert (trade.assetName, trade.contextId) != ("USD/CHF", fixtures.identity("iq/0/1"))


def test_the_two_brokers_never_share_a_slot(tmp_path: Path) -> None:
    rows = [
        *fixtures.session(seconds=420, tag="cbiso"),
        *fixtures.session(platform="iqoption", seconds=420, tag="iqiso"),
    ]
    engine = replay(rows, tmp_path, includeIqOption=True)
    engine.run()
    assert engine.engine is not None
    slots = engine.engine.features.slots
    assert ("capitalbear", 3) in slots
    assert ("iqoption", 3) in slots
    assert slots[("capitalbear", 3)].contextId != slots[("iqoption", 3)].contextId
    assert slots[("capitalbear", 3)].assetName != slots[("iqoption", 3)].assetName


# --- T-DL data quality -----------------------------------------------------------------


def test_a_hole_in_the_history_stays_a_hole(tmp_path: Path) -> None:
    rows = fixtures.session(seconds=420, tag="hole", gap=(120, 180))
    engine = replay(rows, tmp_path)
    engine.run()
    assert engine.engine is not None
    builder = engine.engine.builders[("capitalbear", 1)]
    stored = {market_time_of(row) for row in rows if row.slotId == 1}
    replayed = {sample.timestamp for sample in builder.samples}
    # Every replayed sample came from the record. Nothing was interpolated into the hole.
    assert replayed <= stored
    assert builder.missingSeconds >= 59


def test_degraded_input_stays_degraded(tmp_path: Path) -> None:
    rows = fixtures.session(seconds=420, tag="degraded")
    marked = [
        row.model_copy(update={"dataQuality": fixtures.quality_block("DEGRADED")})
        if row.slotId == 2
        else row
        for row in rows
    ]
    engine = replay(marked, tmp_path)
    engine.run()
    assert engine.engine is not None
    builder = engine.engine.builders[("capitalbear", 2)]
    assert {sample.quality.state for sample in builder.samples} == {"DEGRADED"}
    assert all(candle.quality == "DEGRADED" for candle in builder.candles)


def test_an_unusable_reading_is_refused_by_phase_five_exactly_as_it_would_be_live(
    tmp_path: Path,
) -> None:
    rows = fixtures.session(seconds=180, tag="reject")
    spoiled = [
        row.model_copy(update={"dataQuality": fixtures.quality_block("INVALID")})
        if row.slotId == 3
        else row
        for row in rows
    ]
    result = replay(spoiled, tmp_path).run()
    assert result.run.rejectedEvents >= sum(1 for row in spoiled if row.slotId == 3)


# --- window derivation -----------------------------------------------------------------


def test_the_window_defaults_read_the_record_rather_than_inventing_one() -> None:
    rows = fixtures.small_history()
    dataset = InMemoryObservationSource(rows).prepare()
    window = derive_window(ReplayManifest(warmupDurationMs=60_000), dataset)
    assert dataset.startTime is not None and dataset.endTime is not None
    assert window.warmupStart == dataset.startTime
    assert window.evaluationStart == dataset.startTime + 60_000
    assert window.evaluationEnd == dataset.endTime
    assert window.settlementEnd > window.evaluationEnd


# --- the clock -------------------------------------------------------------------------


def test_the_clock_never_runs_backwards_and_has_no_other_source() -> None:
    clock = ReplayClock()
    assert clock.now is None and clock.watermark is None
    clock.advance(1_000)
    clock.advance(900)
    assert clock.now == 1_000
    assert clock.regressions == 1
    clock.advance(2_000)
    assert clock.now == 2_000
    assert clock.elapsed == 1_000
    assert clock.watermark == 2_000 - AVAILABILITY_LAG_MS
