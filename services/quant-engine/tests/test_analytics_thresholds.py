"""T-CA, T-CB, T-CC, T-CD, T-CE: the split, the search, and what it refuses to recommend.

Three properties are asserted here rather than described. The split is chronological and the
module contains nothing that could shuffle it. A threshold that really does separate outcomes is
found. And a threshold that works on the earliest history and stops working afterwards is marked
UNSTABLE rather than recommended — which is the only reason to hold back a third of the data at
all.
"""

from __future__ import annotations

import ast
from pathlib import Path

import analytics_fixtures as fixtures
from quant_engine.analytics import (
    AnalyticsEngine,
    AnalyticsSettings,
    build,
    chronological_split,
    discover,
    evaluate,
    folds,
    split_report,
)
from quant_engine.analytics.models import AnalyticsRow
from quant_engine.analytics.thresholds import METRICS
from quant_engine.paper.models import PaperTrade

SETTINGS = AnalyticsSettings()
MODULE = (
    Path(__file__).resolve().parents[1] / "src" / "quant_engine" / "analytics" / "thresholds.py"
)


def rank_score(row: AnalyticsRow) -> float:
    return row.rankScore


# --- T-CA the temporal split -----------------------------------------------------------


def test_one_hundred_chronological_rows_split_sixty_twenty_twenty() -> None:
    rows = build(fixtures.sequence(fixtures.outcomes("WLWW", 100))).rows
    split = chronological_split(rows, settings=SETTINGS)
    assert (len(split.train), len(split.validation), len(split.test)) == (60, 20, 20)
    report = split_report(split, settings=SETTINGS)
    assert (report.train, report.validation, report.test) == (60, 20, 20)
    assert report.total == 100
    assert report.ordering == "CHRONOLOGICAL"


def test_the_split_ratios_are_configurable_and_honoured_exactly() -> None:
    rows = build(fixtures.sequence(fixtures.outcomes("WL", 200))).rows
    settings = AnalyticsSettings(trainRatio=0.5, validationRatio=0.25)
    split = chronological_split(rows, settings=settings)
    assert (len(split.train), len(split.validation), len(split.test)) == (100, 50, 50)
    assert split_report(split, settings=settings).testRatio == 0.25


# --- T-CB never shuffled ---------------------------------------------------------------


def test_the_earliest_outcomes_train_and_the_most_recent_are_held_back() -> None:
    rows = build(fixtures.sequence(fixtures.outcomes("W", 100))).rows
    split = chronological_split(rows, settings=SETTINGS)
    assert split.train[0].expiryTime == rows[0].expiryTime
    assert split.train[-1].expiryTime < split.validation[0].expiryTime
    assert split.validation[-1].expiryTime < split.test[0].expiryTime
    assert split.test[-1].expiryTime == rows[-1].expiryTime
    # Every row lands in exactly one split, in order, with nothing lost or duplicated.
    rejoined = [*split.train, *split.validation, *split.test]
    assert [row.paperTradeId for row in rejoined] == [row.paperTradeId for row in rows]


def test_the_threshold_module_binds_no_way_to_shuffle_or_sample() -> None:
    # Market history is a sequence. A shuffled split lets a threshold learn from trades that had
    # not happened yet, and the resulting number would look like evidence.
    tree = ast.parse(MODULE.read_text())
    names = {node.attr.casefold() for node in ast.walk(tree) if isinstance(node, ast.Attribute)} | {
        node.id.casefold() for node in ast.walk(tree) if isinstance(node, ast.Name)
    }
    assert names.isdisjoint({"shuffle", "sample", "choice", "random", "randrange", "seed"})
    imported = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert "random" not in imported


def test_chronological_folds_never_reorder_the_history_they_cut() -> None:
    rows = build(fixtures.sequence(fixtures.outcomes("WL", 50))).rows
    windows = folds(rows, 5)
    assert [len(window) for window in windows] == [10, 10, 10, 10, 10]
    flattened = [row.paperTradeId for window in windows for row in window]
    assert flattened == [row.paperTradeId for row in rows]
    assert folds(rows, 0) == []
    assert folds((), 5) == []


# --- T-CE coverage ---------------------------------------------------------------------


def test_coverage_is_the_share_of_resolved_trades_a_threshold_would_have_taken() -> None:
    rows = build(
        [
            fixtures.trade(
                f"cov-{index}",
                rank_score=0.8 if index < 30 else 0.2,
                expiry=fixtures.BASE_MS + index * 1_000,
            )
            for index in range(100)
        ]
    ).rows
    metrics = evaluate(rows, rank_score, 0.7, split="TRAIN", settings=SETTINGS)
    assert metrics.total == 100
    assert metrics.count == 30
    assert metrics.coverage == 0.30


def test_a_threshold_nothing_clears_has_a_coverage_of_zero_and_no_rate() -> None:
    rows = build(fixtures.sequence(fixtures.outcomes("W", 40), rank_score=0.2)).rows
    metrics = evaluate(rows, rank_score, 0.9, split="TEST", settings=SETTINGS)
    assert metrics.count == 0 and metrics.coverage == 0.0
    assert metrics.outcomes.winRateExcludingDraws is None
    assert metrics.lift is None


# --- T-CC discovery --------------------------------------------------------------------


def test_a_genuinely_stronger_score_band_is_discovered_and_holds_out_of_sample() -> None:
    trades, evaluations = fixtures.corpus()
    split = chronological_split(build(trades, evaluations).rows, settings=SETTINGS)
    candidates, comparisons = discover(split, settings=SETTINGS)
    rank = next(item for item in candidates if item.metric == "rankScore")
    assert 0.5 <= rank.threshold <= 0.75
    assert rank.stability == "STABLE" and rank.stable is True
    assert "CONSISTENT_ACROSS_SPLITS" in rank.reasons
    for period in (rank.train, rank.validation, rank.test):
        assert period.lift is not None and period.lift > 0
        assert period.outcomes.winRateExcludingDraws is not None
        assert period.outcomes.winRateExcludingDraws > 0.55
    assert rank.train.coverage is not None and rank.train.coverage >= SETTINGS.minCoverage
    assert comparisons == len(METRICS) * 19


def test_a_candidate_is_never_produced_from_too_few_trades() -> None:
    rows = build(fixtures.sequence(fixtures.outcomes("W", 30), rank_score=0.9)).rows
    candidates, _ = discover(chronological_split(rows, settings=SETTINGS), settings=SETTINGS)
    assert candidates == []


# --- T-CD out-of-sample failure --------------------------------------------------------


def test_a_threshold_that_stops_working_after_the_training_period_is_unstable() -> None:
    # Strong for the first 60% of history and no better than chance afterwards. Exactly the
    # pattern a search over pooled all-time data would report as a discovery.
    rows = []
    for index in range(300):
        high = index % 2 == 0
        if index < 180:
            chance = 80 if high else 40
        else:
            chance = 45
        rows.append(
            fixtures.trade(
                f"decay-{index}",
                outcome="WIN" if fixtures.scatter(index) < chance else "LOSS",
                rank_score=0.85 if high else 0.25,
                expiry=fixtures.BASE_MS + index * 1_000,
            )
        )
    split = chronological_split(build(rows).rows, settings=SETTINGS)
    candidates, _ = discover(split, settings=SETTINGS)
    rank = next(item for item in candidates if item.metric == "rankScore")
    assert rank.stable is False
    assert rank.stability == "UNSTABLE"
    assert "DIRECTION_NOT_CONSISTENT" in rank.reasons
    assert rank.train.lift is not None and rank.train.lift > 0
    assert any(reason.startswith("WEAK_IN_") for reason in rank.reasons)


def test_a_candidate_with_no_out_of_sample_evidence_is_untested_not_recommended() -> None:
    settings = AnalyticsSettings(trainRatio=0.9, validationRatio=0.05)
    rows = build(
        [
            fixtures.trade(
                f"short-{index}",
                outcome="WIN" if index % 2 == 0 else "LOSS",
                rank_score=0.9 if index % 2 == 0 else 0.1,
                expiry=fixtures.BASE_MS + index * 1_000,
            )
            for index in range(120)
        ]
    ).rows
    candidates, _ = discover(chronological_split(rows, settings=settings), settings=settings)
    rank = next(item for item in candidates if item.metric == "rankScore")
    assert rank.stability == "UNTESTED"
    assert rank.stable is False
    assert "OUT_OF_SAMPLE_TOO_SMALL" in rank.reasons


# --- T-AJ, T-AK the objective ----------------------------------------------------------


def test_a_perfect_rate_on_a_handful_of_trades_never_becomes_a_candidate() -> None:
    # 100% of four trades is four trades. Coverage and sample size both have to hold.
    rows = build(
        [
            *[
                fixtures.trade(
                    f"tiny-{i}", outcome="WIN", rank_score=0.99, expiry=fixtures.BASE_MS + i
                )
                for i in range(4)
            ],
            *[
                fixtures.trade(
                    f"bulk-{i}",
                    outcome="WIN" if i % 2 else "LOSS",
                    rank_score=0.2,
                    expiry=fixtures.BASE_MS + 100 + i,
                )
                for i in range(400)
            ],
        ]
    ).rows
    candidates, _ = discover(chronological_split(rows, settings=SETTINGS), settings=SETTINGS)
    assert all(item.threshold < 0.9 for item in candidates)
    assert all(item.train.count >= SETTINGS.minRecommendationSample for item in candidates)
    assert all(
        item.train.coverage is not None and item.train.coverage >= SETTINGS.minCoverage
        for item in candidates
    )


def test_no_objective_in_the_module_knows_about_a_daily_target_or_limit() -> None:
    # Phase 9.5 owns session risk. A threshold tuned toward a money goal is the exact failure
    # the separation between these two layers exists to prevent.
    tree = ast.parse(MODULE.read_text())
    names = {node.attr.casefold() for node in ast.walk(tree) if isinstance(node, ast.Attribute)} | {
        node.id.casefold() for node in ast.walk(tree) if isinstance(node, ast.Name)
    }
    assert names.isdisjoint(
        {
            "dailyprofittarget",
            "dailylosslimit",
            "canopennewentry",
            "target",
            "martingale",
            "stake",
            "positionsize",
        }
    )


def test_every_candidate_says_on_its_own_record_that_it_is_not_applied() -> None:
    snapshot = AnalyticsEngine().analyze(build(*fixtures.corpus()))
    assert snapshot.thresholdCandidates
    for candidate in snapshot.thresholdCandidates:
        assert candidate.appliedToLiveExecution is False
        assert candidate.operator == ">="
    assert snapshot.researchOnly is True
    assert snapshot.appliedToLiveExecution is False


# --- directional stability is not monetary stability ------------------------------------


def winning_but_unprofitable() -> list[PaperTrade]:
    """A threshold that lifts the win rate in every period and loses money in every period.

    At a 0.8 payout a rule needs roughly 55.6% to break even, so 54% is a real directional
    improvement over a 46% baseline and still bleeds. This is the case a single stability flag
    would report as a finding.
    """
    rows: list[PaperTrade] = []
    for index in range(400):
        high = index % 2 == 0
        outcome = "WIN" if fixtures.scatter(index) < (54 if high else 38) else "LOSS"
        rows.append(
            fixtures.trade(
                f"thin-edge-{index}",
                outcome=outcome,
                rank_score=0.85 if high else 0.25,
                stake=50.0,
                payout=0.8,
                expiry=fixtures.BASE_MS + index * 1_000,
            )
        )
    return rows


def test_a_threshold_that_wins_more_and_still_loses_money_is_not_recommended() -> None:
    split = chronological_split(build(winning_but_unprofitable()).rows, settings=SETTINGS)
    candidates, _ = discover(split, settings=SETTINGS)
    rank = next(item for item in candidates if item.metric == "rankScore")
    assert rank.directionalStability == "STABLE"
    for period in (rank.train, rank.validation, rank.test):
        assert period.lift is not None and period.lift > 0
        assert period.money.expectancyPerTrade is not None
        assert period.money.expectancyPerTrade < 0
    assert rank.monetaryStability == "UNSTABLE"
    assert rank.stability == "UNSTABLE"
    assert rank.stable is False
    assert "EXPECTANCY_NOT_CONSISTENT" in rank.reasons
    assert any(reason.startswith("UNPROFITABLE_IN_") for reason in rank.reasons)


def test_a_threshold_that_wins_more_and_earns_more_is_stable_on_both_counts() -> None:
    trades, evaluations = fixtures.corpus()
    split = chronological_split(build(trades, evaluations).rows, settings=SETTINGS)
    candidates, _ = discover(split, settings=SETTINGS)
    rank = next(item for item in candidates if item.metric == "rankScore")
    assert rank.directionalStability == "STABLE"
    assert rank.monetaryStability == "STABLE"
    assert rank.stable is True


def test_an_unpriced_history_leaves_expectancy_unverified_rather_than_assumed() -> None:
    # Directional evidence is still evidence. It is not silently promoted to monetary evidence,
    # and the candidate carries the gap on its own record.
    trades, _ = fixtures.corpus()
    bare = [
        trade.model_copy(
            update={
                "paperStake": None,
                "paperPayoutRate": None,
                "paperCurrency": None,
                "realizedPaperPnl": None,
            }
        )
        for trade in trades
    ]
    split = chronological_split(build(bare).rows, settings=SETTINGS)
    candidates, _ = discover(split, settings=SETTINGS)
    rank = next(item for item in candidates if item.metric == "rankScore")
    assert rank.directionalStability == "STABLE"
    assert rank.monetaryStability == "UNTESTED"
    assert "MONETARY_UNVERIFIED" in rank.reasons
    assert rank.stable is True
