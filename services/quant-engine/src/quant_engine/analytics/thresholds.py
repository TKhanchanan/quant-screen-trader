"""Research-only threshold discovery over chronologically split history.

Nothing in this module changes anything. A candidate it produces is an observation that recorded
outcomes above some score band differed from the ones below it — it is not a setting, there is no
function anywhere in this application that reads one and applies it, and every candidate carries
``appliedToLiveExecution`` as a literal false.

Three rules keep the output from being wishful thinking:

* the split is **chronological**, never shuffled. Market history is a sequence; a random split
  lets a threshold learn from trades that had not happened yet;
* thresholds are **searched on TRAIN only** and merely *evaluated* on VALIDATION and TEST. A
  threshold chosen with test data in view has already used the data it is about to be judged by;
* the objective is **not win rate**. A rule that wins 90% of the time on three trades is not a
  finding, so coverage, sample size, expectancy and consistency across all three periods all
  have to hold before anything is marked STABLE.

No objective here has any awareness of a daily profit target, a loss limit or a number of trades
needed to reach one. Phase 9.5 owns session risk, and optimizing a score toward a money goal is
the exact failure this separation exists to prevent.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from math import sqrt

from quant_engine.analytics.metrics import money_metrics, outcome_metrics
from quant_engine.analytics.models import (
    AnalyticsRow,
    AnalyticsSettings,
    Code,
    OutcomeMetrics,
    SplitMetrics,
    SplitName,
    Stability,
    TemporalSplitReport,
    ThresholdCandidate,
)

type Accessor = Callable[[AnalyticsRow], float | None]

METRICS: tuple[tuple[str, Accessor], ...] = (
    ("rankScore", lambda row: row.rankScore),
    ("ensembleConfidence", lambda row: row.ensembleConfidence),
    ("agreement", lambda row: row.agreement),
    ("regimeConfidence", lambda row: row.regimeConfidence),
    ("leadMargin", lambda row: row.leadMargin),
)
"""Every score a threshold may be searched on. A fixed list, so the search space is a documented
constant rather than something that grows quietly as fields are added."""


@dataclass(frozen=True, slots=True)
class TemporalSplit:
    """One chronological partition of an already-sorted dataset."""

    train: tuple[AnalyticsRow, ...] = ()
    validation: tuple[AnalyticsRow, ...] = ()
    test: tuple[AnalyticsRow, ...] = ()

    def of(self, name: SplitName) -> tuple[AnalyticsRow, ...]:
        return {"TRAIN": self.train, "VALIDATION": self.validation, "TEST": self.test}[name]


def chronological_split(
    rows: Sequence[AnalyticsRow], *, settings: AnalyticsSettings
) -> TemporalSplit:
    """Earliest rows to TRAIN, then VALIDATION, then the most recent to TEST.

    The input is already sorted by market time, and this function only cuts it. There is no
    shuffle, no seed and no sampling anywhere in the module for exactly that reason.
    """
    total = len(rows)
    train = int(total * settings.trainRatio)
    validation = int(total * settings.validationRatio)
    return TemporalSplit(
        train=tuple(rows[:train]),
        validation=tuple(rows[train : train + validation]),
        test=tuple(rows[train + validation :]),
    )


def split_report(split: TemporalSplit, *, settings: AnalyticsSettings) -> TemporalSplitReport:
    def window(rows: Sequence[AnalyticsRow]) -> tuple[int | None, int | None]:
        return (rows[0].expiryTime, rows[-1].expiryTime) if rows else (None, None)

    train_start, train_end = window(split.train)
    validation_start, validation_end = window(split.validation)
    test_start, test_end = window(split.test)
    return TemporalSplitReport(
        total=len(split.train) + len(split.validation) + len(split.test),
        train=len(split.train),
        validation=len(split.validation),
        test=len(split.test),
        trainRatio=settings.trainRatio,
        validationRatio=settings.validationRatio,
        testRatio=settings.testRatio,
        trainStart=train_start,
        trainEnd=train_end,
        validationStart=validation_start,
        validationEnd=validation_end,
        testStart=test_start,
        testEnd=test_end,
    )


def folds(rows: Sequence[AnalyticsRow], count: int) -> list[tuple[AnalyticsRow, ...]]:
    """Contiguous chronological folds, for evaluating a rule period by period.

    Phase 11 will replay history properly; this is the reusable piece it will need — a way to
    cut an ordered dataset into successive windows without ever reordering it. Phase 10 uses it
    for nothing more than that, and implements no replay of its own.
    """
    if count < 1 or not rows:
        return []
    size = len(rows) / count
    cuts = [round(index * size) for index in range(count + 1)]
    return [tuple(rows[cuts[index] : cuts[index + 1]]) for index in range(count)]


def passes(row: AnalyticsRow, accessor: Accessor, threshold: float) -> bool:
    value = accessor(row)
    return value is not None and value >= threshold


def evaluate(
    rows: Sequence[AnalyticsRow],
    accessor: Accessor,
    threshold: float,
    *,
    split: SplitName,
    settings: AnalyticsSettings,
    baseline: OutcomeMetrics | None = None,
) -> SplitMetrics:
    """What one threshold would have selected inside one period, and how that compares to it.

    ``baselineWinRate`` is the whole period's own rate, so a threshold is judged against the
    market it actually traded in. Comparing it to a pooled all-time average would credit a rule
    for happening to sit in an easier stretch of history.

    The baseline may be supplied because it does not depend on the threshold: a grid search
    walks many thresholds over the same rows, and recomputing the period's own rate at every
    step of it was the most expensive thing this layer did.
    """
    selected = [row for row in rows if passes(row, accessor, threshold)]
    period = baseline or outcome_metrics(rows, minimum=settings.minDisplaySample)
    metrics = outcome_metrics(selected, minimum=settings.minDisplaySample)
    rate = metrics.winRateExcludingDraws
    base_rate = period.winRateExcludingDraws
    return SplitMetrics(
        split=split,
        total=len(rows),
        count=len(selected),
        coverage=len(selected) / len(rows) if rows else None,
        outcomes=metrics,
        money=money_metrics(selected),
        baselineWinRate=base_rate,
        lift=rate - base_rate if rate is not None and base_rate is not None else None,
    )


def _research_score(train: SplitMetrics, settings: AnalyticsSettings) -> float | None:
    """A documented ordering for the research table. Explicitly not a production objective.

    ``lift * sqrt(coverage)``: a rule has to beat the period it traded in *and* apply often
    enough to have been measured. The square root is a deliberate compromise between the two and
    is not fitted to anything — it exists so a rule that fires four times cannot outrank one
    that fires four hundred on a fractionally smaller edge.
    """
    if train.lift is None or train.coverage is None or train.coverage < settings.minCoverage:
        return None
    return train.lift * sqrt(train.coverage)


def _grid(settings: AnalyticsSettings) -> list[float]:
    """Candidate thresholds as exact multiples of the step, so a grid is reproducible."""
    steps = int(
        round((settings.thresholdCeiling - settings.thresholdFloor) / settings.thresholdStep)
    )
    return [
        round(settings.thresholdFloor + index * settings.thresholdStep, 6)
        for index in range(steps + 1)
    ]


def _consistent(
    periods: Sequence[SplitMetrics], value: Callable[[SplitMetrics], float | None]
) -> bool:
    """Whether a quantity was positive in every period. A missing period is not a pass."""
    return all((measured := value(period)) is not None and measured > 0 for period in periods)


def _stability(
    train: SplitMetrics,
    validation: SplitMetrics,
    test: SplitMetrics,
    *,
    settings: AnalyticsSettings,
) -> tuple[Stability, Stability, Stability, bool, list[Code]]:
    """Directional stability, monetary stability, and the overall verdict built from both.

    They are separate because they disagree, and the case where they disagree is the one worth
    catching. At a 0.8 payout a rule has to win about 56% of the time to break even, so a
    threshold that lifts the win rate from 52% to 55% in *every* period is directionally stable
    and loses money in all three. One combined flag would report that as a finding.

    So the overall verdict requires both whenever both can be measured. When Phase 9 priced
    nothing the monetary verdict is ``UNTESTED`` — a different statement from "it failed" — and
    the candidate stays eligible while carrying ``MONETARY_UNVERIFIED``, because directional
    evidence is still evidence and pretending otherwise would discard it.

    Train strong, validation strong, test weak is UNSTABLE and is not recommended. A candidate
    whose later periods are simply too small to have tested anything is UNTESTED.
    """
    periods = (train, validation, test)
    names = ("TRAIN", "VALIDATION", "TEST")
    reasons: list[Code] = []
    if train.count < settings.minRecommendationSample:
        reasons.append("TRAIN_SAMPLE_BELOW_MINIMUM")
    if validation.count < settings.minDisplaySample or test.count < settings.minDisplaySample:
        reasons.append("OUT_OF_SAMPLE_TOO_SMALL")
        return ("UNTESTED", "UNTESTED", "UNTESTED", False, reasons)

    directional_ok = [period.lift is not None and period.lift > 0 for period in periods]
    if all(directional_ok):
        directional: Stability = "STABLE"
    else:
        directional = "UNSTABLE"
        reasons.append("DIRECTION_NOT_CONSISTENT")
        reasons.extend(
            f"WEAK_IN_{name}" for name, ok in zip(names, directional_ok, strict=True) if not ok
        )

    priced = [period.money.available for period in periods]
    if not any(priced):
        monetary: Stability = "UNTESTED"
        reasons.append("MONETARY_UNVERIFIED")
    elif not all(priced):
        monetary = "UNTESTED"
        reasons.append("MONETARY_PARTIALLY_PRICED")
    elif _consistent(periods, lambda period: period.money.expectancyPerTrade):
        monetary = "STABLE"
    else:
        monetary = "UNSTABLE"
        reasons.append("EXPECTANCY_NOT_CONSISTENT")
        reasons.extend(
            f"UNPROFITABLE_IN_{name}"
            for name, period in zip(names, periods, strict=True)
            if period.money.expectancyPerTrade is None or period.money.expectancyPerTrade <= 0
        )

    if any(reason == "TRAIN_SAMPLE_BELOW_MINIMUM" for reason in reasons):
        return ("UNSTABLE", directional, monetary, False, reasons)
    if directional == "UNSTABLE" or monetary == "UNSTABLE":
        return ("UNSTABLE", directional, monetary, False, reasons)
    reasons.append("CONSISTENT_ACROSS_SPLITS")
    return ("STABLE", directional, monetary, True, reasons)


def discover(
    split: TemporalSplit, *, settings: AnalyticsSettings
) -> tuple[list[ThresholdCandidate], int]:
    """The best candidate per metric, plus how many comparisons were searched to find them.

    The count is returned rather than hidden because it is the honest caveat on the whole
    table: the best of eighty-five comparisons on one history is the best of eighty-five
    comparisons on one history.
    """
    grid = _grid(settings)
    candidates: list[ThresholdCandidate] = []
    comparisons = 0
    # A period's own win rate is a property of the period, not of the threshold being tried
    # against it, so each is measured once and handed to every evaluation that needs it.
    periods: tuple[SplitName, ...] = ("TRAIN", "VALIDATION", "TEST")
    baselines = {
        name: outcome_metrics(split.of(name), minimum=settings.minDisplaySample) for name in periods
    }
    for metric, accessor in METRICS:
        best: tuple[float, float, SplitMetrics] | None = None
        for threshold in grid:
            train = evaluate(
                split.train,
                accessor,
                threshold,
                split="TRAIN",
                settings=settings,
                baseline=baselines["TRAIN"],
            )
            comparisons += 1
            if train.count < settings.minRecommendationSample:
                continue
            score = _research_score(train, settings)
            if score is None or score <= 0:
                continue
            if best is None or score > best[0]:
                best = (score, threshold, train)
        if best is None:
            continue
        score, threshold, train = best
        validation = evaluate(
            split.validation,
            accessor,
            threshold,
            split="VALIDATION",
            settings=settings,
            baseline=baselines["VALIDATION"],
        )
        test = evaluate(
            split.test,
            accessor,
            threshold,
            split="TEST",
            settings=settings,
            baseline=baselines["TEST"],
        )
        stability, directional, monetary, stable, reasons = _stability(
            train, validation, test, settings=settings
        )
        candidates.append(
            ThresholdCandidate(
                metric=metric,
                threshold=threshold,
                train=train,
                validation=validation,
                test=test,
                stable=stable,
                stability=stability,
                directionalStability=directional,
                monetaryStability=monetary,
                researchScore=score,
                reasons=reasons,
            )
        )
    return (candidates, comparisons)
