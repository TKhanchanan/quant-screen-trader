"""T-CQ, T-CR, T-AT, T-AU: the analysis over history the real pipeline actually produced.

The unit tests build outcomes directly, because a calibration property cannot be steered out of
a live market on demand. This file closes the other half: broker-shaped observations go in at
one end, and the analytics layer reads what came out of Parquet at the other — through Phase 5's
builder, Phase 6's features, Phase 7's ensemble, Phase 8's ranking and Phase 9's resolution,
with nothing injected anywhere in between.

It is also where the honest small-sample behaviour is asserted. A real run produces a handful of
outcomes, and the correct report over a handful of outcomes says so rather than producing a
confident-looking win rate.
"""

from __future__ import annotations

from pathlib import Path

import analytics_fixtures as fixtures
import test_paper_pipeline as pipeline
from quant_engine.analytics import AnalyticsService, build
from quant_engine.market_api import MarketEngine
from quant_engine.market_storage import ParquetStorage
from quant_engine.paper.models import PaperTrade
from quant_engine.storage.analytics_repository import (
    MAX_RETAINED,
    load_snapshots,
    save_snapshot,
    snapshot_dir,
)
from quant_engine.strategy.models import StrategyEvaluation


def seeded(tmp_path: Path) -> MarketEngine:
    engine = pipeline.run(tmp_path)
    engine.storage.flush()
    return engine


# --- T-CQ the real pipeline ------------------------------------------------------------


def test_the_analytics_layer_reads_what_the_real_pipeline_wrote(tmp_path: Path) -> None:
    engine = seeded(tmp_path)
    snapshot = engine.analytics.refresh()
    assert snapshot is not None
    resolved = [t for t in engine.paper.history if t.status == "RESOLVED"]
    assert resolved, "the real chain must produce outcomes for this test to mean anything"
    assert snapshot.totalResolved == len(resolved)
    assert snapshot.quality.resolvedRate is not None
    assert snapshot.overallMetrics.wins + snapshot.overallMetrics.losses > 0
    assert snapshot.generatedFrom == "paper_trades"


def test_the_phase_seven_votes_are_joined_to_the_outcomes_they_produced(tmp_path: Path) -> None:
    engine = seeded(tmp_path)
    snapshot = engine.analytics.refresh()
    assert snapshot is not None
    assert snapshot.quality.strategyJoinedTrades > 0
    assert snapshot.strategyRegimeMatrix.rows, "real evaluations must reach the matrix"
    assert snapshot.strategyMetrics
    assert all(item.votesPresent > 0 for item in snapshot.strategyMetrics)


def test_a_small_real_sample_is_reported_as_a_small_sample(tmp_path: Path) -> None:
    # This is the expected outcome of a short acceptance run, and it is the right one. A layer
    # that produced a confident win rate over nine trades would be worse than one that produced
    # nothing at all.
    engine = seeded(tmp_path)
    snapshot = engine.analytics.refresh()
    assert snapshot is not None
    assert snapshot.totalResolved < snapshot.settings.minDisplaySample
    assert "INSUFFICIENT_SAMPLE" in snapshot.warnings
    assert snapshot.overallMetrics.sampleLabel in ("LOW_SAMPLE", "INSUFFICIENT_SAMPLE")
    assert snapshot.thresholdCandidates == []


def test_the_analysis_is_read_through_parquet_rather_than_off_the_live_engine(
    tmp_path: Path,
) -> None:
    seeded(tmp_path)
    # A completely separate engine over the same folder sees the same outcomes, which is only
    # true if the analysis is built from the durable record.
    reader = MarketEngine(ParquetStorage(tmp_path))
    trades, evaluations = reader.analysis_history()
    assert all(isinstance(row, PaperTrade) for row in trades)
    assert all(isinstance(row, StrategyEvaluation) for row in evaluations)
    resolved = [row for row in trades if row.status == "RESOLVED"]
    assert resolved
    assert len(build(trades, evaluations).rows) == len({row.paperTradeId for row in resolved})


# --- T-CR the synthetic acceptance corpus ----------------------------------------------


def test_the_synthetic_corpus_exercises_every_output_this_layer_produces() -> None:
    service = AnalyticsService()
    snapshot = service.adopt(*fixtures.corpus())
    assert snapshot.totalResolved == 600
    assert len(snapshot.platformMetrics) == 2
    assert len(snapshot.regimeMetrics) == 4
    assert len(snapshot.assetMetrics) == 6
    assert snapshot.hourMetrics and snapshot.weekdayMetrics
    assert len(snapshot.strategyMetrics) == len(fixtures.STRATEGIES)
    assert snapshot.strategyRegimeMatrix.cells
    assert snapshot.rankCalibration.populatedBins >= 4
    assert snapshot.confidenceCalibration.populatedBins >= 4
    assert snapshot.thresholdCandidates
    assert snapshot.research
    assert snapshot.overallMoney.available is True
    assert snapshot.temporalSplit.train == 360


def test_the_corpus_finds_the_band_that_works_and_reports_the_score_that_does_not() -> None:
    snapshot = AnalyticsService().adopt(*fixtures.corpus())
    rank = next(item for item in snapshot.thresholdCandidates if item.metric == "rankScore")
    assert rank.stable is True
    assert "NON_MONOTONIC_RANK_SCORE" not in snapshot.warnings
    # The same corpus builds confidence with no relationship to outcomes at all, and that has
    # to be reported just as plainly as the one that works.
    assert "NON_MONOTONIC_CONFIDENCE" in snapshot.warnings
    assert "MULTIPLE_TESTING_WARNING" in snapshot.warnings


# --- T-AT, T-AU caching and storage ----------------------------------------------------


def test_a_rebuild_is_only_marked_busy_while_it_is_running(tmp_path: Path) -> None:
    engine = seeded(tmp_path)
    assert engine.analytics.rebuilding is False
    assert engine.analytics.loaded is False
    engine.analytics.refresh()
    assert engine.analytics.rebuilding is False
    assert engine.analytics.loaded is True
    assert engine.analytics.rebuilds == 1


def test_an_unreadable_history_keeps_the_previous_analysis_and_names_the_reason(
    tmp_path: Path,
) -> None:
    service = AnalyticsService()
    service.adopt(*fixtures.corpus())
    before = service.snapshot

    def broken() -> tuple[list[PaperTrade], list[StrategyEvaluation]]:
        raise OSError("parquet is not readable")

    service.loader = broken
    assert service.refresh() is before
    assert service.loadError is not None and "unreadable" in service.loadError
    assert service.rebuilding is False


def test_a_filtered_view_never_replaces_the_cached_unfiltered_one() -> None:
    from quant_engine.analytics import AnalyticsFilters

    service = AnalyticsService()
    whole = service.adopt(*fixtures.corpus())
    part = service.view(AnalyticsFilters(platform="iqoption"))
    assert part is not None and part.totalResolved < whole.totalResolved
    assert service.snapshot is whole
    assert service.view() is whole


def test_a_snapshot_round_trips_through_its_own_storage(tmp_path: Path) -> None:
    snapshot = AnalyticsService().adopt(*fixtures.corpus())
    save_snapshot(tmp_path, snapshot)
    stored = load_snapshots(tmp_path)
    assert len(stored) == 1
    assert stored[0].model_dump(mode="json") == snapshot.model_dump(mode="json")


def test_writing_the_same_analysis_twice_leaves_one_file(tmp_path: Path) -> None:
    snapshot = AnalyticsService().adopt(*fixtures.corpus())
    save_snapshot(tmp_path, snapshot)
    save_snapshot(tmp_path, snapshot)
    assert len(list(snapshot_dir(tmp_path).glob("*.json"))) == 1


def test_snapshot_storage_is_bounded_and_never_touches_the_market_record(
    tmp_path: Path,
) -> None:
    base = AnalyticsService().adopt(*fixtures.corpus())
    for index in range(MAX_RETAINED + 5):
        save_snapshot(tmp_path, base.model_copy(update={"sampleEnd": fixtures.BASE_MS + index}))
    assert len(list(snapshot_dir(tmp_path).glob("*.json"))) == MAX_RETAINED
    assert {path.name for path in tmp_path.iterdir()} == {"analytics_snapshots"}


def test_an_unreadable_snapshot_file_is_skipped_rather_than_guessed_at(tmp_path: Path) -> None:
    snapshot = AnalyticsService().adopt(*fixtures.corpus())
    save_snapshot(tmp_path, snapshot)
    (snapshot_dir(tmp_path) / "0000000000001-broken.json").write_text("{not json")
    assert len(load_snapshots(tmp_path)) == 1
