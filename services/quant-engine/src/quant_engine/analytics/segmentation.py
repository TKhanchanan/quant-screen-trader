"""Slicing the resolved outcomes by every dimension that could explain them.

Two rules govern every table in this module. Every slice reports its own size, because a
performance number without a sample size is not a measurement; and a slice too thin to reason
about is labelled rather than hidden, because the reader needs to see how thin it is.

Nothing here whitelists, blacklists, promotes or suppresses anything. A "best hour" table is a
description of recorded history, and this layer has no mechanism to act on one.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence

from quant_engine.analytics.calibration import bin_index, bounds, label_for, score_bins
from quant_engine.analytics.metrics import (
    EMPTY_MONEY,
    EMPTY_OUTCOMES,
    average,
    money_metrics,
    outcome_metrics,
    sample_label,
)
from quant_engine.analytics.models import (
    AnalyticsRow,
    AnalyticsSettings,
    Matrix,
    MatrixCell,
    ScoreBin,
    SegmentMetrics,
    StrategyContribution,
    VoteStance,
)
from quant_engine.configuration import Platform
from quant_engine.strategy.models import Regime

REGIMES: tuple[Regime, ...] = (
    "TREND_UP",
    "TREND_DOWN",
    "RANGE",
    "BREAKOUT_UP",
    "BREAKOUT_DOWN",
    "VOLATILITY_EXPANSION",
    "VOLATILITY_COMPRESSION",
    "NOISY",
    "UNCERTAIN",
)
"""Fixed order, so two snapshots put the same regime in the same row of the same table."""

PLATFORMS: tuple[Platform, ...] = ("capitalbear", "iqoption")
DIRECTIONS = ("UP", "DOWN")
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def segment(
    rows: Sequence[AnalyticsRow],
    *,
    key: str,
    label: str,
    settings: AnalyticsSettings,
    platform: Platform | None = None,
    minimum: int | None = None,
) -> SegmentMetrics:
    floor = settings.minDisplaySample if minimum is None else minimum
    return SegmentMetrics(
        key=key,
        label=label,
        platform=platform,
        outcomes=outcome_metrics(rows, minimum=floor),
        money=money_metrics(
            rows, bootstrap_iterations=settings.bootstrapIterations, seed=settings.bootstrapSeed
        ),
        averageRankScore=average(rows, lambda row: row.rankScore),
        averageConfidence=average(rows, lambda row: row.ensembleConfidence),
        averageRegimeConfidence=average(rows, lambda row: row.regimeConfidence),
        averageAgreement=average(rows, lambda row: row.agreement),
        rankable=len(rows) >= floor,
    )


def group(
    rows: Sequence[AnalyticsRow], key: Callable[[AnalyticsRow], str]
) -> dict[str, list[AnalyticsRow]]:
    buckets: dict[str, list[AnalyticsRow]] = defaultdict(list)
    for row in rows:
        buckets[key(row)].append(row)
    return buckets


def by_platform(
    rows: Sequence[AnalyticsRow], *, settings: AnalyticsSettings
) -> list[SegmentMetrics]:
    """Per platform, never pooled into one unlabelled number.

    CapitalBear measures a five-second horizon and IQ Option a sixty-second one, over different
    capture cadences and different microstructure. A single combined win rate would be the
    average of two answers to two different questions.
    """
    buckets = group(rows, lambda row: row.platform)
    return [
        segment(
            buckets.get(platform, []),
            key=platform,
            label=_platform_label(platform),
            settings=settings,
            platform=platform,
        )
        for platform in PLATFORMS
        if platform in buckets
    ]


def _platform_label(platform: Platform) -> str:
    return "CapitalBear S5" if platform == "capitalbear" else "IQ Option M1"


def by_regime(rows: Sequence[AnalyticsRow], *, settings: AnalyticsSettings) -> list[SegmentMetrics]:
    buckets = group(rows, lambda row: row.primaryRegime)
    return [
        segment(buckets[regime], key=regime, label=regime, settings=settings)
        for regime in REGIMES
        if regime in buckets
    ]


def regime_direction_matrix(rows: Sequence[AnalyticsRow], *, settings: AnalyticsSettings) -> Matrix:
    """Regime against the direction that was actually selected in it.

    Worth its own table because the two need not behave symmetrically: a layer that is good at
    reading an uptrend is not automatically good at reading a downtrend, and a pooled regime
    row would hide the difference completely.
    """
    buckets = group(rows, lambda row: f"{row.primaryRegime}|{row.direction}")
    present: list[str] = [
        regime for regime in REGIMES if any(row.primaryRegime == regime for row in rows)
    ]
    cells = [
        _cell(
            buckets.get(f"{regime}|{direction}", []),
            row=regime,
            column=direction,
            settings=settings,
        )
        for regime in present
        for direction in DIRECTIONS
    ]
    return Matrix(
        name="regimeDirection",
        rowLabel="regime",
        columnLabel="direction",
        rows=list(present),
        columns=list(DIRECTIONS),
        cells=cells,
        sampleCount=len(rows),
        note="Descriptive cross-tab. No direction is enabled or disabled anywhere by this.",
    )


def _cell(
    rows: Sequence[AnalyticsRow],
    *,
    row: str,
    column: str,
    settings: AnalyticsSettings,
    agreed: int = 0,
    samples: int | None = None,
) -> MatrixCell:
    count = len(rows) if samples is None else samples
    return MatrixCell(
        row=row,
        column=column,
        samples=count,
        agreed=agreed,
        outcomes=outcome_metrics(rows, minimum=settings.minDisplaySample),
        money=money_metrics(rows),
        sampleLabel=sample_label(count, minimum=settings.minDisplaySample),
    )


def by_asset(rows: Sequence[AnalyticsRow], *, settings: AnalyticsSettings) -> list[SegmentMetrics]:
    """Per platform *and* asset name.

    The same string names two different instruments on two brokers, captured at two cadences
    over two horizons. Keying on the name alone would merge them into an asset that does not
    exist.
    """
    buckets = group(rows, lambda row: f"{row.platform}|{row.assetName}")
    return [
        segment(
            buckets[key],
            key=key,
            label=f"{_platform_label(buckets[key][0].platform)} · {buckets[key][0].assetName}",
            settings=settings,
            platform=buckets[key][0].platform,
            minimum=settings.minAssetSample,
        )
        for key in sorted(buckets)
    ]


def by_hour(rows: Sequence[AnalyticsRow], *, settings: AnalyticsSettings) -> list[SegmentMetrics]:
    """Hour of the local trading day, in the configured timezone and never the host's."""
    buckets = group(rows, lambda row: f"{row.hourOfDay:02d}")
    return [
        segment(
            buckets[key],
            key=key,
            label=f"{key}:00–{key}:59 {settings.timezone}",
            settings=settings,
        )
        for key in sorted(buckets)
    ]


def by_weekday(
    rows: Sequence[AnalyticsRow], *, settings: AnalyticsSettings
) -> list[SegmentMetrics]:
    buckets = group(rows, lambda row: str(row.dayOfWeek))
    return [
        segment(
            buckets[str(day)],
            key=WEEKDAYS[day],
            label=f"{WEEKDAYS[day]} ({settings.timezone})",
            settings=settings,
        )
        for day in range(7)
        if str(day) in buckets
    ]


def by_quality(
    rows: Sequence[AnalyticsRow], *, settings: AnalyticsSettings
) -> list[SegmentMetrics]:
    """Upstream input quality, read exactly as Phase 5 and Phase 8 recorded it.

    Reported because a difference here is the most actionable thing a diagnostic can find: if
    degraded capture is where the losses live, the fix is in the capture layer and not in a
    score. No quality rule is re-evaluated or changed by looking at it.
    """
    entry = group(rows, lambda row: f"entryQuality={row.entryQuality or 'UNKNOWN'}")
    board = group(rows, lambda row: f"boardStatus={row.boardStatus}")
    return [segment(entry[key], key=key, label=key, settings=settings) for key in sorted(entry)] + [
        segment(board[key], key=key, label=key, settings=settings) for key in sorted(board)
    ]


def stance_of(vote_direction: str, selected: str) -> VoteStance:
    """How one strategy's vote stood relative to the selection that was actually taken.

    ABSTAINED covers NEUTRAL and SKIP, which are different statements — "no edge here" and "I
    could not form an opinion" — and are counted separately as well. Neither is a disagreement,
    and scoring them as one would punish a strategy for being honest about its coverage.
    """
    if vote_direction == selected:
        return "AGREED"
    if vote_direction in DIRECTIONS:
        return "DISAGREED"
    return "ABSTAINED"


def strategy_contributions(
    rows: Sequence[AnalyticsRow], *, settings: AnalyticsSettings
) -> list[StrategyContribution]:
    """What each Phase 7 strategy's vote was worth as evidence.

    Reads the stored evaluations and reinterprets none of them. The strategy's thresholds, its
    eligibility rules and its confidence are Phase 7's, and this table only asks whether the
    outcomes differed when it happened to agree with what was selected.
    """
    by_strategy: dict[str, dict[VoteStance, list[AnalyticsRow]]] = defaultdict(
        lambda: {"AGREED": [], "DISAGREED": [], "ABSTAINED": []}
    )
    neutral: dict[str, int] = defaultdict(int)
    skipped: dict[str, int] = defaultdict(int)
    for row in rows:
        for vote in row.strategyVotes:
            by_strategy[vote.strategyId][stance_of(vote.direction, row.direction)].append(row)
            if vote.direction == "NEUTRAL":
                neutral[vote.strategyId] += 1
            elif vote.direction == "SKIP":
                skipped[vote.strategyId] += 1
    contributions = []
    for strategy_id in sorted(by_strategy):
        stances = by_strategy[strategy_id]
        present = sum(len(items) for items in stances.values())
        contributions.append(
            StrategyContribution(
                strategyId=strategy_id,
                votesPresent=present,
                agreed=len(stances["AGREED"]),
                disagreed=len(stances["DISAGREED"]),
                abstained=len(stances["ABSTAINED"]),
                neutralVotes=neutral[strategy_id],
                skippedVotes=skipped[strategy_id],
                agreementRate=len(stances["AGREED"]) / present if present else None,
                whenAgreed=outcome_metrics(stances["AGREED"], minimum=settings.minDisplaySample),
                whenDisagreed=outcome_metrics(
                    stances["DISAGREED"], minimum=settings.minDisplaySample
                ),
                whenAbstained=outcome_metrics(
                    stances["ABSTAINED"], minimum=settings.minDisplaySample
                ),
                moneyWhenAgreed=money_metrics(stances["AGREED"]),
                sampleLabel=sample_label(present, minimum=settings.minDisplaySample),
            )
        )
    return contributions


def strategy_regime_matrix(rows: Sequence[AnalyticsRow], *, settings: AnalyticsSettings) -> Matrix:
    """Which strategy's agreement came with better outcomes under which regime.

    Each cell is scored over the outcomes where that strategy agreed with the selection, which
    is the only version of the question that has an answer: a strategy that abstained
    contributed no evidence, and counting the result as its own would credit or blame it for a
    decision it declined to take part in.
    """
    agreed: dict[str, list[AnalyticsRow]] = defaultdict(list)
    present: dict[str, int] = defaultdict(int)
    strategies: set[str] = set()
    regimes: set[str] = set()
    for row in rows:
        for vote in row.strategyVotes:
            strategies.add(vote.strategyId)
            regimes.add(row.primaryRegime)
            key = f"{vote.strategyId}|{row.primaryRegime}"
            present[key] += 1
            if stance_of(vote.direction, row.direction) == "AGREED":
                agreed[key].append(row)
    ordered_strategies = sorted(strategies)
    ordered_regimes: list[str] = [regime for regime in REGIMES if regime in regimes]
    cells = [
        _cell(
            agreed.get(f"{strategy}|{regime}", []),
            row=strategy,
            column=regime,
            settings=settings,
            agreed=len(agreed.get(f"{strategy}|{regime}", [])),
            samples=present[f"{strategy}|{regime}"],
        )
        for strategy in ordered_strategies
        for regime in ordered_regimes
    ]
    return Matrix(
        name="strategyRegime",
        rowLabel="strategy",
        columnLabel="regime",
        rows=ordered_strategies,
        columns=ordered_regimes,
        cells=cells,
        sampleCount=sum(present.values()),
        note="Outcomes where the strategy agreed with the selection. Nothing is weighted by it.",
    )


def rank_confidence_matrix(rows: Sequence[AnalyticsRow], *, settings: AnalyticsSettings) -> Matrix:
    """Rank score against ensemble confidence.

    The interesting cells are the off-diagonal ones — a high rank on low confidence, or the
    reverse — because a score pair that only ever agrees with itself carries one piece of
    information, not two.
    """
    side = settings.gridBinCount
    buckets: dict[str, list[AnalyticsRow]] = defaultdict(list)
    for row in rows:
        rank_band = bin_index(row.rankScore, side)
        confidence_band = bin_index(row.ensembleConfidence, side)
        buckets[f"{rank_band}|{confidence_band}"].append(row)
    labels = [label_for(index, side) for index in range(side)]
    cells = [
        _cell(
            buckets.get(f"{rank_band}|{confidence_band}", []),
            row=labels[rank_band],
            column=labels[confidence_band],
            settings=settings,
        )
        for rank_band in range(side)
        for confidence_band in range(side)
    ]
    return Matrix(
        name="rankConfidence",
        rowLabel="rankScore",
        columnLabel="ensembleConfidence",
        rows=labels,
        columns=labels,
        cells=cells,
        sampleCount=len(rows),
        note=f"Bands of width {1 / side:.2f}. Descriptive only.",
    )


def agreement_bins(rows: Sequence[AnalyticsRow], *, settings: AnalyticsSettings) -> list[ScoreBin]:
    return score_bins(
        rows, lambda row: row.agreement, count=settings.agreementBinCount, settings=settings
    )


def lead_margin_bins(
    rows: Sequence[AnalyticsRow], *, settings: AnalyticsSettings
) -> list[ScoreBin]:
    """Phase 8's margin of the leader over the runner-up.

    ``None`` on a board that had only one directional candidate, and those rows are excluded
    rather than treated as a margin of zero: a sole candidate did not win by nothing, it had
    nobody to win against.
    """
    return score_bins(
        rows, lambda row: row.leadMargin, count=settings.leadMarginBinCount, settings=settings
    )


def regime_confidence_bins(
    rows: Sequence[AnalyticsRow], *, settings: AnalyticsSettings
) -> list[ScoreBin]:
    return score_bins(
        rows,
        lambda row: row.regimeConfidence,
        count=settings.regimeConfidenceBinCount,
        settings=settings,
    )


def empty_matrix(name: str, row_label: str, column_label: str) -> Matrix:
    return Matrix(
        name=name,
        rowLabel=row_label,
        columnLabel=column_label,
        rows=[],
        columns=[],
        cells=[],
        sampleCount=0,
        note="No rows carried the evidence this matrix is built from.",
    )


def empty_cell(row: str, column: str) -> MatrixCell:
    return MatrixCell(
        row=row,
        column=column,
        samples=0,
        agreed=0,
        outcomes=EMPTY_OUTCOMES,
        money=EMPTY_MONEY,
        sampleLabel="INSUFFICIENT_SAMPLE",
    )


def band_bounds(index: int, count: int) -> tuple[float, float]:
    return bounds(index, count)
