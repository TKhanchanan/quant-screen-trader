"""T-BH, T-BI, T-BJ, T-BK, T-BN, T-BY, T-BZ: the arithmetic, and what it refuses to invent.

Every rule this file exists to hold in place is a rule about honesty rather than accuracy. A
draw is not half a win. A zero denominator is not infinity. A resolved trade with no configured
stake has no monetary result, and its absence is not zero. A correlation over two points is not
a correlation.
"""

from __future__ import annotations

from typing import Any

import analytics_fixtures as fixtures
from quant_engine.analytics import (
    bootstrap_mean,
    correlation_for,
    money_metrics,
    outcome_metrics,
    sample_label,
    spearman,
    wilson_interval,
)
from quant_engine.analytics.dataset import build
from quant_engine.analytics.models import AnalyticsRow

FLOOR = 20


def rows(outcomes: list[str], **changes: Any) -> tuple[AnalyticsRow, ...]:
    return build(
        [
            fixtures.trade(
                f"m-{index}", outcome=value, expiry=fixtures.BASE_MS + index * 1000, **changes
            )
            for index, value in enumerate(outcomes)
        ]
    ).rows


# --- T-BH basic counts -----------------------------------------------------------------


def test_known_outcomes_produce_the_known_tally() -> None:
    metrics = outcome_metrics(rows(["WIN", "WIN", "LOSS", "DRAW"]), minimum=FLOOR)
    assert metrics.resolved == 4
    assert (metrics.wins, metrics.losses, metrics.draws) == (2, 1, 1)
    assert metrics.sampleCount == 4


# --- T-BI win rate ---------------------------------------------------------------------


def test_a_draw_is_excluded_from_the_binary_rate_and_counted_in_the_other() -> None:
    metrics = outcome_metrics(rows(["WIN", "WIN", "LOSS", "DRAW"]), minimum=FLOOR)
    assert metrics.winRateExcludingDraws == 2 / 3
    assert metrics.winRateIncludingDraws == 2 / 4
    assert metrics.drawRate == 1 / 4


def test_a_slice_with_no_binary_outcome_has_no_binary_rate() -> None:
    # Three draws is not a 0% win rate and it is not a 100% one. It is no rate at all.
    metrics = outcome_metrics(rows(["DRAW", "DRAW", "DRAW"]), minimum=FLOOR)
    assert metrics.winRateExcludingDraws is None
    assert metrics.winRateIncludingDraws == 0.0
    assert metrics.drawRate == 1.0


# --- T-BJ paper money ------------------------------------------------------------------


def test_known_simulated_results_produce_the_known_money() -> None:
    money = money_metrics(rows(["WIN", "LOSS", "WIN"], stake=50.0, payout=0.8))
    assert money.available is True
    assert money.grossProfit == 80.0
    assert money.grossLoss == 50.0
    assert money.netPaperPnl == 30.0
    assert money.profitFactor == 1.6
    assert money.averageWin == 40.0
    assert money.averageLoss == -50.0
    assert money.payoffRatio == 0.8
    assert money.expectancyPerTrade == 10.0
    assert money.monetaryTrades == 3


def test_a_slice_with_no_losses_reports_no_profit_factor_rather_than_infinity() -> None:
    money = money_metrics(rows(["WIN", "WIN"], stake=50.0, payout=0.8))
    assert money.grossLoss == 0.0
    assert money.profitFactor is None
    assert money.payoffRatio is None
    assert money.netPaperPnl == 80.0


def test_a_mixed_currency_slice_reports_no_currency_rather_than_one_of_them() -> None:
    baht = fixtures.trade("cur-thb", outcome="WIN", currency="THB", expiry=fixtures.BASE_MS)
    dollar = fixtures.trade(
        "cur-usd", outcome="WIN", currency="USD", expiry=fixtures.BASE_MS + 1_000
    )
    money = money_metrics(build([baht, dollar]).rows)
    assert money.currency is None
    assert money.monetaryTrades == 2


# --- T-BK no monetary data -------------------------------------------------------------


def test_directional_statistics_survive_a_history_that_was_never_priced() -> None:
    unpriced = rows(["WIN", "WIN", "LOSS"], stake=None, payout=None, currency=None)
    metrics = outcome_metrics(unpriced, minimum=FLOOR)
    money = money_metrics(unpriced)
    assert metrics.winRateExcludingDraws == 2 / 3
    assert money.available is False
    assert money.netPaperPnl is None
    assert money.expectancyPerTrade is None
    assert money.monetaryTrades == 0


def test_an_unpriced_trade_contributes_direction_but_not_money() -> None:
    priced = fixtures.trade("mix-a", outcome="WIN", expiry=fixtures.BASE_MS)
    bare = fixtures.trade(
        "mix-b",
        outcome="LOSS",
        stake=None,
        payout=None,
        currency=None,
        expiry=fixtures.BASE_MS + 1_000,
    )
    dataset = build([priced, bare])
    assert outcome_metrics(dataset.rows, minimum=FLOOR).resolved == 2
    money = money_metrics(dataset.rows)
    assert money.monetaryTrades == 1
    assert money.netPaperPnl == 40.0


# --- T-BN Wilson interval --------------------------------------------------------------


def test_the_wilson_interval_matches_its_published_values() -> None:
    low, high = wilson_interval(6, 10)
    assert low is not None and high is not None
    assert round(low, 4) == 0.3127
    assert round(high, 4) == 0.8318


def test_the_wilson_interval_stays_inside_zero_and_one_at_the_extremes() -> None:
    # The normal approximation returns bounds outside [0, 1] here, which is exactly the small
    # sample this layer spends most of its life reporting.
    perfect = wilson_interval(10, 10)
    empty = wilson_interval(0, 10)
    assert perfect[0] is not None and 0 < perfect[0] < 1
    assert perfect[1] is not None and 0.999 < perfect[1] <= 1.0
    assert empty[0] == 0.0 and empty[1] is not None and 0 < empty[1] < 1
    assert wilson_interval(0, 0) == (None, None)


def test_the_interval_narrows_as_the_sample_grows() -> None:
    small = wilson_interval(6, 10)
    large = wilson_interval(600, 1_000)
    assert small[0] is not None and small[1] is not None
    assert large[0] is not None and large[1] is not None
    assert (large[1] - large[0]) < (small[1] - small[0])


# --- T-BO sample labels ----------------------------------------------------------------


def test_a_thin_segment_is_labelled_rather_than_hidden() -> None:
    metrics = outcome_metrics(rows(["WIN"] * 5), minimum=FLOOR)
    assert metrics.sampleCount == 5
    assert metrics.sampleLabel == "LOW_SAMPLE"
    assert metrics.winRateExcludingDraws == 1.0
    assert sample_label(0, minimum=FLOOR) == "INSUFFICIENT_SAMPLE"
    assert sample_label(FLOOR, minimum=FLOOR) == "OK"


# --- T-BY, T-BZ correlation ------------------------------------------------------------


def test_a_perfectly_ordered_score_correlates_positively() -> None:
    ordered = [
        fixtures.trade(
            f"corr-{index}",
            outcome="WIN" if index >= 5 else "LOSS",
            rank_score=round(0.05 + index * 0.09, 4),
            expiry=fixtures.BASE_MS + index * 1_000,
        )
        for index in range(10)
    ]
    correlation = correlation_for(build(ordered).rows, "rankScore", lambda row: row.rankScore)
    # A binary outcome has only two rank values, so a perfect separation tops out below 1.
    assert correlation.coefficient is not None and correlation.coefficient > 0.85
    assert correlation.sampleCount == 10
    assert correlation.strength == "STRONG"


def test_reversing_the_ordering_reverses_the_sign() -> None:
    reversed_rows = [
        fixtures.trade(
            f"anti-{index}",
            outcome="LOSS" if index >= 5 else "WIN",
            rank_score=round(0.05 + index * 0.09, 4),
            expiry=fixtures.BASE_MS + index * 1_000,
        )
        for index in range(10)
    ]
    correlation = correlation_for(build(reversed_rows).rows, "rankScore", lambda row: row.rankScore)
    assert correlation.coefficient is not None and correlation.coefficient < -0.85
    assert "inverse" in correlation.note


def test_the_binary_correlation_excludes_draws_and_says_how_many() -> None:
    mixed = rows(["WIN", "LOSS", "DRAW", "WIN", "LOSS", "DRAW"])
    correlation = correlation_for(mixed, "rankScore", lambda row: row.rankScore)
    assert correlation.sampleCount == 4
    assert correlation.drawsExcluded == 2


def test_a_constant_score_or_a_tiny_sample_has_no_correlation_to_report() -> None:
    assert spearman([1.0, 1.0, 1.0], [0.0, 1.0, 0.0]) is None
    assert spearman([1.0, 2.0], [0.0, 1.0]) is None
    flat = correlation_for(rows(["WIN", "LOSS", "WIN", "LOSS"]), "rankScore", lambda r: r.rankScore)
    assert flat.coefficient is None
    assert flat.strength == "NONE"
    assert "Insufficient" in flat.note


def test_tied_scores_share_a_rank_rather_than_inventing_an_order() -> None:
    assert spearman([1.0, 1.0, 2.0, 3.0], [1.0, 1.0, 2.0, 3.0]) == 1.0


# --- T-AE bootstrap --------------------------------------------------------------------


def test_the_bootstrap_is_seeded_so_the_same_data_gives_the_same_interval() -> None:
    values = [40.0, -50.0, 40.0, 40.0, -50.0, 40.0, 40.0, -50.0]
    first = bootstrap_mean(values, iterations=200, seed=7)
    second = bootstrap_mean(values, iterations=200, seed=7)
    assert first == second
    assert first[0] is not None and first[1] is not None and first[0] < first[1]
    assert bootstrap_mean([1.0], iterations=200, seed=7) == (None, None)
    assert bootstrap_mean(values, iterations=0, seed=7) == (None, None)
