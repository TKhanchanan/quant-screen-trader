"""T-BL, T-BM, T-CU, T-CV: the binning convention, and the diagnostics that must not be hidden.

The convention is tested at the boundaries rather than in the middle of a band, because the
middle of a band is where every implementation agrees. A score of exactly 0.3 belongs in
``[0.30, 0.40)`` and a score of exactly 1.0 has to belong somewhere, and both of those are
places a plausible implementation silently gets wrong.

The second half of this file exists for one reason: a calibration layer that could only describe
a score that works would be a layer that quietly reinterprets its metric until the metric looks
good. A score with no relationship to outcomes, and a score inversely related to them, both have
to be reported as such.
"""

from __future__ import annotations

import analytics_fixtures as fixtures
from quant_engine.analytics import (
    AnalyticsEngine,
    AnalyticsSettings,
    bin_index,
    build,
    calibration_report,
    monotonicity,
    score_bins,
)
from quant_engine.analytics.models import AnalyticsRow

SETTINGS = AnalyticsSettings()


def scored(values: list[float], outcomes: list[str] | None = None) -> tuple[AnalyticsRow, ...]:
    results = outcomes if outcomes is not None else ["WIN"] * len(values)
    return build(
        [
            fixtures.trade(
                f"bin-{index}",
                outcome=results[index],
                rank_score=value,
                confidence=value,
                expiry=fixtures.BASE_MS + index * 1_000,
            )
            for index, value in enumerate(values)
        ]
    ).rows


# --- T-BL the binning convention -------------------------------------------------------


def test_a_score_on_a_boundary_belongs_to_the_band_it_opens() -> None:
    assert bin_index(0.0, 10) == 0
    assert bin_index(0.1, 10) == 1
    assert bin_index(0.2, 10) == 2
    assert bin_index(0.7, 10) == 7
    assert bin_index(0.9, 10) == 9


def test_a_score_that_binary_cannot_represent_still_lands_where_the_documentation_says() -> None:
    # 0.3 * 10 is 2.9999999999999996 in IEEE 754. An implementation that multiplied would put
    # this score one band below the one the convention names.
    for value, expected in ((0.3, 3), (0.29, 2), (0.7, 7), (0.56, 5), (0.07, 0)):
        assert bin_index(value, 10) == expected


def test_the_top_band_is_closed_so_a_perfect_score_is_never_dropped() -> None:
    assert bin_index(1.0, 10) == 9
    bins = score_bins(scored([1.0]), lambda row: row.rankScore, count=10, settings=SETTINGS)
    assert bins[9].outcomes.resolved == 1
    assert bins[9].inclusiveUpper is True
    assert bins[9].label == "[0.90, 1.00]"
    assert bins[0].label == "[0.00, 0.10)"
    assert all(not item.inclusiveUpper for item in bins[:9])


def test_every_band_is_returned_even_when_nothing_ever_scored_in_it() -> None:
    bins = score_bins(scored([0.55]), lambda row: row.rankScore, count=10, settings=SETTINGS)
    assert len(bins) == 10
    assert sum(item.outcomes.resolved for item in bins) == 1
    assert [item.outcomes.sampleLabel for item in bins].count("INSUFFICIENT_SAMPLE") == 9


def test_the_bin_count_is_configurable_and_the_edges_follow_it() -> None:
    bins = score_bins(
        scored([0.1, 0.4, 0.9]), lambda row: row.rankScore, count=4, settings=SETTINGS
    )
    assert len(bins) == 4
    assert bins[0].lowerBound == 0.0 and bins[0].upperBound == 0.25
    assert [item.outcomes.resolved for item in bins] == [1, 1, 0, 1]
    assert bin_index(0.25, 4) == 1


def test_a_bin_reports_the_mean_score_inside_it_rather_than_its_midpoint() -> None:
    bins = score_bins(scored([0.71, 0.79]), lambda row: row.rankScore, count=10, settings=SETTINGS)
    assert bins[7].scoreMean == 0.75
    assert bins[0].scoreMean is None


# --- T-BM the same convention for confidence -------------------------------------------


def test_confidence_uses_the_same_bands_as_rank_score() -> None:
    rows = scored([0.0, 0.3, 0.7, 1.0])
    rank = score_bins(rows, lambda row: row.rankScore, count=10, settings=SETTINGS)
    confidence = score_bins(rows, lambda row: row.ensembleConfidence, count=10, settings=SETTINGS)
    assert [item.outcomes.resolved for item in rank] == [
        item.outcomes.resolved for item in confidence
    ]


def test_a_row_with_no_value_for_the_metric_is_left_out_of_every_band() -> None:
    # A board with one directional candidate has no lead margin. It did not win by nothing; it
    # had nobody to win against, and a zero band would be a measurement nobody took.
    rows = build(
        [
            fixtures.trade("lm-a", lead_margin=None, expiry=fixtures.BASE_MS),
            fixtures.trade("lm-b", lead_margin=0.4, expiry=fixtures.BASE_MS + 1_000),
        ]
    ).rows
    bins = score_bins(rows, lambda row: row.leadMargin, count=5, settings=SETTINGS)
    assert sum(item.outcomes.resolved for item in bins) == 1


# --- monotonicity ----------------------------------------------------------------------


def test_a_curve_that_rises_is_reported_as_monotonic() -> None:
    report = calibration_report(
        build(fixtures.monotone()).rows,
        "rankScore",
        lambda row: row.rankScore,
        settings=SETTINGS,
        non_monotonic_code="NON_MONOTONIC_RANK_SCORE",
    )
    assert report.monotonic is True
    assert report.monotonicityCoefficient == 1.0
    assert report.warnings == []
    assert report.correlation.coefficient is not None and report.correlation.coefficient > 0


def test_a_thin_band_cannot_decide_whether_a_curve_is_monotonic() -> None:
    # One trade in a band is noise. Letting it break the ordering would make the flag fire on
    # every real dataset, and a warning that is always on is a warning nobody reads.
    rows = build(
        [
            *[
                fixtures.trade(
                    f"lo-{i}", outcome="LOSS", rank_score=0.15, expiry=fixtures.BASE_MS + i
                )
                for i in range(40)
            ],
            fixtures.trade("spike", outcome="WIN", rank_score=0.45, expiry=fixtures.BASE_MS + 100),
            *[
                fixtures.trade(
                    f"hi-{i}", outcome="WIN", rank_score=0.85, expiry=fixtures.BASE_MS + 200 + i
                )
                for i in range(40)
            ],
        ]
    ).rows
    ordered, coefficient = monotonicity(
        score_bins(rows, lambda row: row.rankScore, count=10, settings=SETTINGS), minimum=20
    )
    assert ordered is True
    assert coefficient is not None


def test_a_single_populated_band_answers_neither_question() -> None:
    ordered, coefficient = monotonicity(
        score_bins(scored([0.55] * 30), lambda row: row.rankScore, count=10, settings=SETTINGS),
        minimum=20,
    )
    assert ordered is False
    assert coefficient is None


# --- T-CU, T-CV the diagnostics that must be surfaced ----------------------------------


def test_a_score_with_no_relationship_to_outcomes_is_reported_as_unhelpful() -> None:
    snapshot = AnalyticsEngine().analyze(build(*fixtures.corpus()))
    # The fixture deliberately builds confidence with no relationship to outcomes at all.
    assert "NON_MONOTONIC_CONFIDENCE" in snapshot.warnings
    assert "NON_MONOTONIC_CONFIDENCE" in snapshot.confidenceCalibration.warnings
    # And rank score, which does work in the same fixture, is not warned about.
    assert "NON_MONOTONIC_RANK_SCORE" not in snapshot.warnings
    assert snapshot.rankCalibration.correlation.coefficient is not None
    assert snapshot.rankCalibration.correlation.coefficient > 0.2


def test_an_inverted_score_is_surfaced_rather_than_reinterpreted() -> None:
    inverted = [
        fixtures.trade(
            f"inv-{index}",
            outcome="LOSS" if index % 5 < 3 else "WIN",
            rank_score=round(0.05 + (index % 10) * 0.1, 4)
            if index % 5 < 3
            else round(0.05 + (index % 4) * 0.05, 4),
            expiry=fixtures.BASE_MS + index * 1_000,
        )
        for index in range(200)
    ]
    report = calibration_report(
        build(inverted).rows,
        "rankScore",
        lambda row: row.rankScore,
        settings=SETTINGS,
        non_monotonic_code="NON_MONOTONIC_RANK_SCORE",
        inverse_code="INVERSE_RANK_SCORE",
    )
    assert report.correlation.coefficient is not None and report.correlation.coefficient < 0
    assert "INVERSE_RANK_SCORE" in report.warnings
    assert "NON_MONOTONIC_RANK_SCORE" in report.warnings
    assert "inverse" in report.correlation.note


def test_the_report_says_in_words_that_it_is_not_a_probability_calibration() -> None:
    report = calibration_report(
        build(fixtures.monotone()).rows, "rankScore", lambda row: row.rankScore, settings=SETTINGS
    )
    assert "not a probability calibration" in report.note
    assert "relative ordering" in report.note
