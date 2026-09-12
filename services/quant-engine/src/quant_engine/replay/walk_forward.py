"""Chronological walk-forward validation with purge and embargo (Phase 11).

The question this module exists to answer is not "does a threshold look good on this history?" —
Phase 10 already answers that, and the answer is worth very little. It is "would a threshold
discovered from the past have held on data that had not happened yet, repeatedly, as the past
moved forward?"

Four rules make the answer mean something.

* **Strictly chronological.** Folds roll forward through market time. There is no shuffle, no
  seed and no sampling anywhere in this file.
* **Discovery on TRAIN only.** The candidate search is ``qst-analytics-v1``'s own, run over the
  fold's training rows; VALIDATION and TEST are only ever *evaluated*. A future fold cannot
  reach a past one, and appending more history cannot change a fold that has already been cut.
* **Purge.** An outcome whose selection was made before a window opened carries information from
  before the cutoff, so it is removed from that window rather than counted in it.
* **Embargo.** A gap the width of the longest possible trade lifetime is inserted at every
  boundary, so no single trade can have its decision on one side of a split and its outcome on
  the other.

Nothing here is applied. A fold's candidate is an observation about recorded history; there is
no code in this application that reads one and changes a decision with it.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import median
from uuid import UUID

from quant_engine.analytics.dataset import fingerprint, settings_fingerprint
from quant_engine.analytics.engine import snapshot_id
from quant_engine.analytics.metrics import money_metrics, outcome_metrics
from quant_engine.analytics.models import (
    AnalyticsFilters,
    AnalyticsRow,
    AnalyticsSettings,
    MoneyMetrics,
    SplitMetrics,
)
from quant_engine.analytics.thresholds import TemporalSplit, discover, folds
from quant_engine.configuration import Platform
from quant_engine.paper.policy import PaperSettings
from quant_engine.replay.clock import settlement_tail_ms
from quant_engine.replay.models import (
    MAX_FOLDS,
    Code,
    FoldMetrics,
    StabilityVerdict,
    WalkForwardFold,
    WalkForwardSettings,
    WalkForwardSummary,
)

THRESHOLD_SPREAD_LIMIT = 0.10
"""How far the fold-by-fold thresholds may drift and still be called one finding.

Two grid steps at the default resolution. A metric whose best cut moves from 0.35 to 0.85 across
folds has not found a level in the market; it has found a different level in each fold, which is
what ``PARAMETER_INSTABILITY`` says."""


def embargo_for(platforms: Sequence[Platform], settings: PaperSettings) -> int:
    """The narrowest embargo that can be honest, derived rather than chosen.

    The longest a selection can stay alive under ``qst-paper-v1`` is its entry bound plus its
    horizon plus its resolution bound — eighty seconds on IQ Option, fifteen on CapitalBear. A
    gap that wide guarantees no trade straddles a boundary; anything narrower would let an
    outcome that was still running at the cutoff be counted on the far side of it.
    """
    return settlement_tail_ms(tuple(platforms), settings)


@dataclass(frozen=True, slots=True)
class FoldWindow:
    """One fold's six boundaries in market time, already separated by the embargo."""

    trainStart: int
    trainEnd: int
    validationStart: int
    validationEnd: int
    testStart: int
    testEnd: int


@dataclass(frozen=True, slots=True)
class Selection:
    """The rows one period may legitimately use, and what was taken away from it."""

    rows: tuple[AnalyticsRow, ...]
    purged: int


def _expiries(rows: Sequence[AnalyticsRow]) -> list[int]:
    return [row.expiryTime for row in rows]


def select(rows: Sequence[AnalyticsRow], expiries: list[int], start: int, end: int) -> Selection:
    """Outcomes that both resolved and were decided inside one window.

    Membership is by expiry, because that is when the outcome became knowable. The purge is the
    second half of the rule: a trade that resolved inside this window but was *selected* before
    it opened was already running across the boundary, and counting it here would carry
    information from the other side of the cutoff.
    """
    lo = bisect_left(expiries, start)
    hi = bisect_right(expiries, end)
    window = rows[lo:hi]
    kept = tuple(row for row in window if row.boardAsOf >= start)
    return Selection(rows=kept, purged=len(window) - len(kept))


def plan_count(
    rows: Sequence[AnalyticsRow], settings: WalkForwardSettings, embargo: int
) -> list[FoldWindow]:
    """Rolling folds over equal contiguous segments of the ordered outcomes.

    What a short record leaves available. It is a weaker statement than rolling calendar windows
    — the folds are equal in *number of outcomes* rather than in market time — and it is labelled
    as one wherever it is reported.
    """
    segments = settings.foldCount + settings.trainSegments + 1
    chunks = [chunk for chunk in folds(rows, segments) if chunk]
    if len(chunks) < settings.trainSegments + 2:
        return []
    planned: list[FoldWindow] = []
    last = settings.trainSegments
    for index in range(min(settings.foldCount, len(chunks) - settings.trainSegments - 1)):
        train = [row for chunk in chunks[index : index + last] for row in chunk]
        validation = chunks[index + last]
        test = chunks[index + last + 1]
        if not train or not validation or not test:
            continue
        train_end = train[-1].expiryTime
        validation_end = validation[-1].expiryTime
        planned.append(
            FoldWindow(
                trainStart=train[0].expiryTime,
                trainEnd=train_end,
                validationStart=train_end + embargo,
                validationEnd=max(validation_end, train_end + embargo),
                testStart=max(validation_end, train_end + embargo) + embargo,
                testEnd=max(test[-1].expiryTime, validation_end + embargo),
            )
        )
    return planned[:MAX_FOLDS]


def plan_duration(
    rows: Sequence[AnalyticsRow], settings: WalkForwardSettings, embargo: int
) -> list[FoldWindow]:
    """Rolling calendar windows: train for a stretch, validate on the next, test on the one after.

    The preferred shape when there is enough history for it, because a fold is then a period of
    the market rather than a count of trades, and two folds are comparable.
    """
    if not rows:
        return []
    start, end = rows[0].expiryTime, rows[-1].expiryTime
    planned: list[FoldWindow] = []
    cursor = start
    while len(planned) < MAX_FOLDS:
        train_end = cursor + settings.trainingDurationMs
        validation_start = train_end + embargo
        validation_end = validation_start + settings.validationDurationMs
        test_start = validation_end + embargo
        test_end = test_start + settings.testDurationMs
        if test_end > end:
            break
        planned.append(
            FoldWindow(
                trainStart=cursor,
                trainEnd=train_end,
                validationStart=validation_start,
                validationEnd=validation_end,
                testStart=test_start,
                testEnd=test_end,
            )
        )
        cursor += settings.stepDurationMs
    return planned


def _money_verdict(periods: Sequence[MoneyMetrics]) -> tuple[bool, StabilityVerdict, list[Code]]:
    """Whether the money across these periods is comparable at all, before it is compared.

    Three conditions, all required. Every period has to have been priced; every period has to be
    denominated in the *same* currency; and no period may have set a priced outcome aside because
    it was in another one. Nothing in this application converts between currencies, so a
    "stable" expectancy measured in two of them would be a number with no meaning.
    """
    reasons: list[Code] = []
    if not all(period.available for period in periods):
        return (False, "UNTESTED", ["MONETARY_UNVERIFIED"])
    currencies = {period.currency for period in periods}
    if len(currencies) > 1 or any(period.mixedCurrency for period in periods):
        return (False, "UNTESTED", ["MONETARY_UNVERIFIED", "MIXED_CURRENCY"])
    if any(period.excludedByCurrency for period in periods):
        return (False, "UNTESTED", ["MONETARY_UNVERIFIED", "MIXED_CURRENCY"])
    values = [period.expectancyPerTrade for period in periods]
    if all(value is not None and value > 0 for value in values):
        return (True, "STABLE", reasons)
    return (False, "UNSTABLE", ["EXPECTANCY_NOT_CONSISTENT"])


def _metrics(period: str, split: SplitMetrics) -> FoldMetrics:
    money = split.money
    return FoldMetrics(
        period="TRAIN"
        if period == "TRAIN"
        else ("VALIDATION" if period == "VALIDATION" else "TEST"),
        total=split.total,
        selected=split.count,
        coverage=split.coverage,
        resolved=split.outcomes.resolved,
        winRateExcludingDraws=split.outcomes.winRateExcludingDraws,
        baselineWinRate=split.baselineWinRate,
        lift=split.lift,
        expectancyPerTrade=money.expectancyPerTrade,
        profitFactor=money.profitFactor,
        currency=money.currency,
        mixedCurrency=money.mixedCurrency,
        excludedByCurrency=money.excludedByCurrency,
        monetaryTrades=money.monetaryTrades,
    )


def _empty(period: str, rows: Sequence[AnalyticsRow], settings: AnalyticsSettings) -> FoldMetrics:
    outcomes = outcome_metrics(rows, minimum=settings.minDisplaySample)
    return FoldMetrics(
        period="TRAIN"
        if period == "TRAIN"
        else ("VALIDATION" if period == "VALIDATION" else "TEST"),
        total=len(rows),
        selected=0,
        coverage=0.0 if rows else None,
        resolved=outcomes.resolved,
        winRateExcludingDraws=None,
        baselineWinRate=outcomes.winRateExcludingDraws,
        lift=None,
        currency=money_metrics(rows).currency,
    )


def _source_snapshot(rows: Sequence[AnalyticsRow], settings: AnalyticsSettings) -> UUID:
    """A deterministic id for the exact training evidence a candidate was discovered from."""
    return snapshot_id(fingerprint(rows), settings_fingerprint(settings, AnalyticsFilters()))


def run_fold(
    fold_id: int,
    window: FoldWindow,
    rows: Sequence[AnalyticsRow],
    expiries: list[int],
    *,
    analytics: AnalyticsSettings,
    embargo: int,
) -> WalkForwardFold:
    """One fold end to end: cut, purge, discover on TRAIN, freeze, evaluate forward.

    The order of operations is the guarantee. TRAIN is searched before VALIDATION or TEST is
    touched at all, and the threshold that comes out of that search is the one both later
    periods are measured against — changing it afterwards would be fitting to the data the fold
    exists to be judged by.
    """
    train = select(rows, expiries, window.trainStart, window.trainEnd)
    validation = select(rows, expiries, window.validationStart, window.validationEnd)
    test = select(rows, expiries, window.testStart, window.testEnd)
    inside = bisect_right(expiries, window.testEnd) - bisect_left(expiries, window.trainStart)
    embargoed = (
        inside
        - (len(train.rows) + len(validation.rows) + len(test.rows))
        - (train.purged + validation.purged + test.purged)
    )
    split = TemporalSplit(train=train.rows, validation=validation.rows, test=test.rows)
    candidates, comparisons = discover(split, settings=analytics)
    best = max(
        (item for item in candidates if item.researchScore is not None),
        key=lambda item: (item.researchScore or 0.0, item.metric),
        default=None,
    )
    warnings: list[Code] = []
    if best is None:
        warnings.append("NO_STABLE_CANDIDATE")
        return WalkForwardFold(
            foldId=fold_id,
            trainStart=window.trainStart,
            trainEnd=window.trainEnd,
            validationStart=window.validationStart,
            validationEnd=window.validationEnd,
            testStart=window.testStart,
            testEnd=window.testEnd,
            purgeMs=embargo,
            embargoMs=embargo,
            purgedRows=train.purged + validation.purged + test.purged,
            embargoedRows=max(0, embargoed),
            candidateSourceSnapshotId=_source_snapshot(train.rows, analytics),
            comparisonsEvaluated=comparisons,
            train=_empty("TRAIN", train.rows, analytics),
            validation=_empty("VALIDATION", validation.rows, analytics),
            test=_empty("TEST", test.rows, analytics),
            warnings=warnings,
        )
    periods = (best.train, best.validation, best.test)
    directional = all(item.lift is not None and item.lift > 0 for item in periods)
    monetary, verdict, money_reasons = _money_verdict([item.money for item in periods])
    warnings.extend(money_reasons)
    if (
        best.train.lift is not None
        and best.train.lift > 0
        and (best.test.lift is None or best.test.lift <= 0)
    ):
        warnings.append("OUT_OF_SAMPLE_DEGRADATION")
    if comparisons > 1:
        warnings.append("MULTIPLE_TESTING_WARNING")
    return WalkForwardFold(
        foldId=fold_id,
        trainStart=window.trainStart,
        trainEnd=window.trainEnd,
        validationStart=window.validationStart,
        validationEnd=window.validationEnd,
        testStart=window.testStart,
        testEnd=window.testEnd,
        purgeMs=embargo,
        embargoMs=embargo,
        purgedRows=train.purged + validation.purged + test.purged,
        embargoedRows=max(0, embargoed),
        candidateSourceSnapshotId=_source_snapshot(train.rows, analytics),
        candidateMetric=best.metric,
        candidateThreshold=best.threshold,
        comparisonsEvaluated=comparisons,
        train=_metrics("TRAIN", best.train),
        validation=_metrics("VALIDATION", best.validation),
        test=_metrics("TEST", best.test),
        directionalStable=directional,
        monetaryStable=monetary,
        monetaryVerdict=verdict,
        warnings=warnings[:16],
    )


def analyse(
    rows: Sequence[AnalyticsRow],
    *,
    settings: WalkForwardSettings,
    analytics: AnalyticsSettings,
    platforms: Sequence[Platform],
    paper: PaperSettings,
) -> WalkForwardSummary:
    """Every fold, and the summary that deliberately has no best-fold field."""
    embargo = (
        settings.embargoMs if settings.embargoMs is not None else embargo_for(platforms, paper)
    )
    ordered = tuple(rows)
    expiries = _expiries(ordered)
    planned = (
        plan_count(ordered, settings, embargo)
        if settings.mode == "COUNT"
        else plan_duration(ordered, settings, embargo)
    )
    results = [
        run_fold(index + 1, window, ordered, expiries, analytics=analytics, embargo=embargo)
        for index, window in enumerate(planned)
    ]
    return summarize(results, settings=settings)


def summarize(
    results: Sequence[WalkForwardFold], *, settings: WalkForwardSettings
) -> WalkForwardSummary:
    """Read the folds together. No fold is promoted, and none is quietly dropped."""
    with_candidate = [item for item in results if item.candidateThreshold is not None]
    test_rates = [
        item.test.winRateExcludingDraws
        for item in with_candidate
        if item.test.winRateExcludingDraws is not None
    ]
    test_expectancy = [
        item.test.expectancyPerTrade
        for item in with_candidate
        if item.test.expectancyPerTrade is not None
    ]
    coverages = [item.test.coverage for item in with_candidate if item.test.coverage is not None]
    thresholds = [
        item.candidateThreshold for item in with_candidate if item.candidateThreshold is not None
    ]
    metrics = sorted({item.candidateMetric for item in with_candidate if item.candidateMetric})
    spread = max(thresholds) - min(thresholds) if thresholds else None
    warnings: list[Code] = []
    if len(results) < 2:
        warnings.append("INSUFFICIENT_FOLDS")
    if not with_candidate:
        warnings.append("NO_STABLE_CANDIDATE")
    if any("OUT_OF_SAMPLE_DEGRADATION" in item.warnings for item in results):
        warnings.append("OUT_OF_SAMPLE_DEGRADATION")
    unstable = len(metrics) > 1 or (spread is not None and spread > THRESHOLD_SPREAD_LIMIT)
    if unstable:
        warnings.append("PARAMETER_INSTABILITY")
    if any("MIXED_CURRENCY" in item.warnings for item in results):
        warnings.append("MIXED_CURRENCY")
    if any(item.monetaryVerdict == "UNTESTED" for item in results):
        warnings.append("MONETARY_UNVERIFIED")
    if sum(item.comparisonsEvaluated for item in results) > 1:
        warnings.append("MULTIPLE_TESTING_WARNING")
    directional = sum(1 for item in results if item.directionalStable)
    monetary = sum(1 for item in results if item.monetaryStable)
    verdict: StabilityVerdict = "UNTESTED"
    if len(results) >= 2 and with_candidate:
        stable = (
            len(with_candidate) == len(results) and directional == len(results) and not unstable
        )
        verdict = "STABLE" if stable else "UNSTABLE"
    return WalkForwardSummary(
        mode=settings.mode,
        settings=settings,
        folds=len(results),
        foldsWithCandidate=len(with_candidate),
        foldsDirectionalPositive=directional,
        foldsMonetaryPositive=monetary,
        medianTestWinRate=median(test_rates) if test_rates else None,
        medianTestExpectancy=median(test_expectancy) if test_expectancy else None,
        medianTestCoverage=median(coverages) if coverages else None,
        candidateMetrics=metrics,
        candidateThresholds=thresholds,
        thresholdSpread=spread,
        candidateStability=verdict,
        totalComparisons=sum(item.comparisonsEvaluated for item in results),
        rows=list(results)[:MAX_FOLDS],
        warnings=sorted(set(warnings)),
    )
