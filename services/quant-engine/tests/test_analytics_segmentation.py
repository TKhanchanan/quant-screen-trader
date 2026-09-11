"""T-BO, T-BP, T-BQ, T-BR, T-BS, T-BT, T-BU, T-BV, T-BW, T-BX: every slice, and its size.

The rule this file holds in place is that a slice is never allowed to look larger, cleaner or
more certain than it is. Two brokers are never pooled, the same asset string on two brokers is
never merged, an hour bucket follows the configured timezone rather than the host's, and a
segment below the display floor is labelled rather than quietly dropped.
"""

from __future__ import annotations

import analytics_fixtures as fixtures
from quant_engine.analytics import (
    AnalyticsEngine,
    AnalyticsSettings,
    build,
    by_asset,
    by_hour,
    by_platform,
    by_quality,
    by_regime,
    by_weekday,
    rank_confidence_matrix,
    regime_direction_matrix,
    strategy_contributions,
    strategy_regime_matrix,
)
from quant_engine.analytics.models import Matrix, MatrixCell
from quant_engine.analytics.segmentation import agreement_bins, lead_margin_bins

SETTINGS = AnalyticsSettings()


def cell(matrix: Matrix, row: str, column: str) -> MatrixCell:
    return next(item for item in matrix.cells if item.row == row and item.column == column)


# --- T-BP platform isolation -----------------------------------------------------------


def test_the_two_brokers_are_measured_separately_and_never_pooled() -> None:
    rows = build(
        [
            *[
                fixtures.trade(
                    f"cb-{i}", outcome="WIN", platform="capitalbear", expiry=fixtures.BASE_MS + i
                )
                for i in range(30)
            ],
            *[
                fixtures.trade(
                    f"iq-{i}",
                    outcome="LOSS",
                    platform="iqoption",
                    expiry=fixtures.BASE_MS + 100 + i,
                )
                for i in range(30)
            ],
        ]
    ).rows
    platforms = by_platform(rows, settings=SETTINGS)
    assert [item.key for item in platforms] == ["capitalbear", "iqoption"]
    assert platforms[0].outcomes.winRateExcludingDraws == 1.0
    assert platforms[1].outcomes.winRateExcludingDraws == 0.0
    assert platforms[0].label == "CapitalBear S5" and platforms[1].label == "IQ Option M1"
    # The horizons differ, so every platform row names which broker it describes.
    assert all(item.platform is not None for item in platforms)


# --- T-BQ asset isolation --------------------------------------------------------------


def test_the_same_asset_name_on_two_brokers_stays_two_assets() -> None:
    rows = build(
        [
            fixtures.trade("a", platform="capitalbear", asset="Gold OTC", expiry=fixtures.BASE_MS),
            fixtures.trade(
                "b", platform="iqoption", asset="Gold OTC", expiry=fixtures.BASE_MS + 1_000
            ),
        ]
    ).rows
    assets = by_asset(rows, settings=SETTINGS)
    assert len(assets) == 2
    assert {item.key for item in assets} == {"capitalbear|Gold OTC", "iqoption|Gold OTC"}
    assert all(item.outcomes.resolved == 1 for item in assets)


# --- T-BO low sample -------------------------------------------------------------------


def test_an_asset_below_the_floor_is_reported_but_never_marked_rankable() -> None:
    rows = build(
        [fixtures.trade(f"thin-{i}", outcome="WIN", expiry=fixtures.BASE_MS + i) for i in range(5)]
    ).rows
    asset = by_asset(rows, settings=SETTINGS)[0]
    assert asset.outcomes.resolved == 5
    assert asset.outcomes.winRateExcludingDraws == 1.0
    assert asset.outcomes.sampleLabel == "LOW_SAMPLE"
    assert asset.rankable is False


def test_an_asset_needs_more_sample_than_an_ordinary_segment_to_be_ranked() -> None:
    rows = build(
        [
            fixtures.trade(f"mid-{i}", outcome="WIN", expiry=fixtures.BASE_MS + i)
            for i in range(SETTINGS.minAssetSample)
        ]
    ).rows
    assert by_asset(rows, settings=SETTINGS)[0].rankable is True
    fewer = build(
        [
            fixtures.trade(f"few-{i}", outcome="WIN", expiry=fixtures.BASE_MS + i)
            for i in range(SETTINGS.minAssetSample - 1)
        ]
    ).rows
    assert by_asset(fewer, settings=SETTINGS)[0].rankable is False


# --- T-BS regime grouping --------------------------------------------------------------


def test_regimes_are_counted_separately_and_ordered_the_same_way_every_time() -> None:
    rows = build(
        [
            fixtures.trade("r1", regime="TREND_UP", outcome="WIN", expiry=fixtures.BASE_MS),
            fixtures.trade("r2", regime="TREND_UP", outcome="LOSS", expiry=fixtures.BASE_MS + 1),
            fixtures.trade("r3", regime="NOISY", outcome="LOSS", expiry=fixtures.BASE_MS + 2),
            fixtures.trade("r4", regime="RANGE", outcome="WIN", expiry=fixtures.BASE_MS + 3),
        ]
    ).rows
    regimes = by_regime(rows, settings=SETTINGS)
    assert [item.key for item in regimes] == ["TREND_UP", "RANGE", "NOISY"]
    assert regimes[0].outcomes.resolved == 2
    assert regimes[0].outcomes.winRateExcludingDraws == 0.5


# --- T-BT direction grouping -----------------------------------------------------------


def test_a_regime_is_crossed_with_the_direction_that_was_actually_selected() -> None:
    rows = build(
        [
            fixtures.trade(
                "d1", regime="TREND_UP", direction="UP", outcome="WIN", expiry=fixtures.BASE_MS
            ),
            fixtures.trade(
                "d2", regime="TREND_UP", direction="UP", outcome="WIN", expiry=fixtures.BASE_MS + 1
            ),
            fixtures.trade(
                "d3",
                regime="TREND_UP",
                direction="DOWN",
                outcome="LOSS",
                expiry=fixtures.BASE_MS + 2,
            ),
        ]
    ).rows
    matrix = regime_direction_matrix(rows, settings=SETTINGS)
    assert matrix.rows == ["TREND_UP"] and matrix.columns == ["UP", "DOWN"]
    assert cell(matrix, "TREND_UP", "UP").outcomes.wins == 2
    assert cell(matrix, "TREND_UP", "DOWN").outcomes.losses == 1
    assert matrix.sampleCount == 3


# --- T-BR timezone ---------------------------------------------------------------------


def test_the_hour_bucket_follows_the_configured_timezone_and_not_the_host() -> None:
    # 2026-09-01T03:00Z is 10:00 on a Tuesday in Bangkok and 23:00 on a Monday in Los Angeles.
    # Reading the host's zone would silently move both the hour and the weekday.
    entry = fixtures.BASE_MS + 5_000
    trade = fixtures.trade("tz", expiry=entry)
    bangkok = build([trade], settings=AnalyticsSettings(timezone="Asia/Bangkok")).rows[0]
    assert (bangkok.hourOfDay, bangkok.dayOfWeek, bangkok.localDate) == (10, 1, "2026-09-01")
    pacific = build([trade], settings=AnalyticsSettings(timezone="America/Los_Angeles")).rows[0]
    assert (pacific.hourOfDay, pacific.dayOfWeek, pacific.localDate) == (20, 0, "2026-08-31")
    hours = by_hour([bangkok], settings=AnalyticsSettings(timezone="Asia/Bangkok"))
    assert hours[0].key == "10" and "Asia/Bangkok" in hours[0].label


def test_an_unknown_timezone_is_refused_rather_than_quietly_replaced() -> None:
    import pytest

    with pytest.raises(ValueError, match="Unknown timezone"):
        AnalyticsSettings(timezone="Mars/Olympus")


# --- T-BU weekday ----------------------------------------------------------------------


def test_weekdays_are_named_and_carry_their_own_sample_size() -> None:
    rows = build(
        [fixtures.trade(f"wd-{i}", expiry=fixtures.BASE_MS + i * 86_400_000) for i in range(9)]
    ).rows
    weekdays = by_weekday(rows, settings=SETTINGS)
    assert [item.key for item in weekdays][:3] == ["Monday", "Tuesday", "Wednesday"]
    assert sum(item.outcomes.resolved for item in weekdays) == 9
    assert all(item.outcomes.sampleLabel == "LOW_SAMPLE" for item in weekdays)


# --- T-BU strategy contribution and T-BU strategy x regime ------------------------------


def test_a_strategy_is_scored_on_the_outcomes_it_actually_agreed_with() -> None:
    up = fixtures.trade("s1", direction="UP", outcome="WIN", expiry=fixtures.BASE_MS)
    down = fixtures.trade("s2", direction="UP", outcome="LOSS", expiry=fixtures.BASE_MS + 1)
    evaluations = [
        fixtures.evaluation(up, "trend_follow_v1", "UP"),
        fixtures.evaluation(down, "trend_follow_v1", "DOWN"),
        fixtures.evaluation(up, "breakout_v1", "SKIP"),
        fixtures.evaluation(down, "breakout_v1", "NEUTRAL"),
    ]
    rows = build([up, down], evaluations).rows
    contributions = {
        item.strategyId: item for item in strategy_contributions(rows, settings=SETTINGS)
    }
    trend = contributions["trend_follow_v1"]
    assert (trend.agreed, trend.disagreed, trend.abstained) == (1, 1, 0)
    assert trend.whenAgreed.wins == 1 and trend.whenDisagreed.losses == 1
    breakout = contributions["breakout_v1"]
    assert breakout.abstained == 2 and breakout.skippedVotes == 1 and breakout.neutralVotes == 1
    assert breakout.agreementRate == 0.0


def test_the_strategy_regime_matrix_places_agreement_in_the_right_cell() -> None:
    rows_trend = [
        fixtures.trade(
            f"m-t-{i}",
            regime="TREND_UP",
            direction="UP",
            outcome="WIN",
            expiry=fixtures.BASE_MS + i,
        )
        for i in range(3)
    ]
    rows_range = [
        fixtures.trade(
            f"m-r-{i}",
            regime="RANGE",
            direction="UP",
            outcome="LOSS",
            expiry=fixtures.BASE_MS + 10 + i,
        )
        for i in range(2)
    ]
    evaluations = [
        *[fixtures.evaluation(row, "trend_follow_v1", "UP") for row in rows_trend],
        *[fixtures.evaluation(row, "trend_follow_v1", "NEUTRAL") for row in rows_range],
    ]
    matrix = strategy_regime_matrix(
        build([*rows_trend, *rows_range], evaluations).rows, settings=SETTINGS
    )
    assert matrix.rows == ["trend_follow_v1"]
    assert matrix.columns == ["TREND_UP", "RANGE"]
    trend_cell = cell(matrix, "trend_follow_v1", "TREND_UP")
    range_cell = cell(matrix, "trend_follow_v1", "RANGE")
    assert (trend_cell.samples, trend_cell.agreed) == (3, 3)
    assert trend_cell.outcomes.winRateExcludingDraws == 1.0
    # Present but abstaining: counted as a vote seen, never credited with the outcome.
    assert (range_cell.samples, range_cell.agreed) == (2, 0)
    assert range_cell.outcomes.resolved == 0


def test_a_vote_cannot_attach_to_a_decision_it_did_not_belong_to() -> None:
    from uuid import UUID

    other = UUID("44444444-4444-4444-8444-444444444444")
    row = fixtures.trade("join", expiry=fixtures.BASE_MS)
    stray = fixtures.evaluation(row, "trend_follow_v1", "UP").model_copy(
        update={"contextId": other}
    )
    dataset = build([row], [stray])
    assert dataset.rows[0].strategyVotes == ()
    assert dataset.rows[0].directionalBreadth is None
    assert dataset.quality.strategyJoinedTrades == 0


# --- T-BV agreement, T-BW lead margin, T-BX the grid ------------------------------------


def test_agreement_is_grouped_into_its_own_bands() -> None:
    rows = build(
        [
            fixtures.trade(f"ag-{i}", agreement=value, expiry=fixtures.BASE_MS + i)
            for i, value in enumerate([0.05, 0.25, 0.45, 0.65, 0.85, 0.95])
        ]
    ).rows
    bins = agreement_bins(rows, settings=SETTINGS)
    assert len(bins) == SETTINGS.agreementBinCount
    assert [item.outcomes.resolved for item in bins] == [1, 1, 1, 1, 2]


def test_lead_margin_is_grouped_and_a_sole_candidate_is_left_out() -> None:
    rows = build(
        [
            fixtures.trade("lm-1", lead_margin=0.05, expiry=fixtures.BASE_MS),
            fixtures.trade("lm-2", lead_margin=0.45, expiry=fixtures.BASE_MS + 1),
            fixtures.trade("lm-3", lead_margin=None, expiry=fixtures.BASE_MS + 2),
        ]
    ).rows
    bins = lead_margin_bins(rows, settings=SETTINGS)
    assert [item.outcomes.resolved for item in bins] == [1, 0, 1, 0, 0]


def test_the_rank_by_confidence_grid_places_each_pair_in_one_cell() -> None:
    rows = build(
        [
            fixtures.trade("g-hh", rank_score=0.9, confidence=0.9, expiry=fixtures.BASE_MS),
            fixtures.trade("g-hl", rank_score=0.9, confidence=0.1, expiry=fixtures.BASE_MS + 1),
            fixtures.trade("g-lh", rank_score=0.1, confidence=0.9, expiry=fixtures.BASE_MS + 2),
        ]
    ).rows
    matrix = rank_confidence_matrix(rows, settings=SETTINGS)
    side = SETTINGS.gridBinCount
    assert len(matrix.cells) == side * side
    assert matrix.sampleCount == 3
    assert sum(item.samples for item in matrix.cells) == 3
    top = matrix.rows[-1]
    bottom = matrix.rows[0]
    assert cell(matrix, top, matrix.columns[-1]).samples == 1
    assert cell(matrix, top, matrix.columns[0]).samples == 1
    assert cell(matrix, bottom, matrix.columns[-1]).samples == 1


# --- T-AB quality dimensions -----------------------------------------------------------


def test_upstream_input_quality_is_reported_without_being_re_evaluated() -> None:
    rows = build(
        [
            fixtures.trade("q-good", outcome="WIN", expiry=fixtures.BASE_MS),
            fixtures.trade(
                "q-degraded", outcome="LOSS", entryQuality="DEGRADED", expiry=fixtures.BASE_MS + 1
            ),
        ]
    ).rows
    keys = {item.key for item in by_quality(rows, settings=SETTINGS)}
    assert "entryQuality=GOOD" in keys
    assert "entryQuality=DEGRADED" in keys
    assert "boardStatus=READY" in keys


def test_every_segment_table_in_a_snapshot_carries_a_sample_count() -> None:
    snapshot = AnalyticsEngine().analyze(build(*fixtures.corpus()))
    tables = (
        snapshot.platformMetrics,
        snapshot.regimeMetrics,
        snapshot.assetMetrics,
        snapshot.hourMetrics,
        snapshot.weekdayMetrics,
        snapshot.qualityMetrics,
    )
    for table in tables:
        assert table, "a populated corpus must produce every segment table"
        for item in table:
            assert item.outcomes.sampleCount == item.outcomes.resolved
            assert item.outcomes.sampleLabel in ("OK", "LOW_SAMPLE", "INSUFFICIENT_SAMPLE")
    for contribution in snapshot.strategyMetrics:
        assert contribution.votesPresent >= 0
        assert contribution.sampleLabel in ("OK", "LOW_SAMPLE", "INSUFFICIENT_SAMPLE")
    for matrix_cell in snapshot.strategyRegimeMatrix.cells:
        assert matrix_cell.sampleLabel in ("OK", "LOW_SAMPLE", "INSUFFICIENT_SAMPLE")
