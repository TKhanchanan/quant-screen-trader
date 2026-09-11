"""Empirical outcome curves for the Phase 7 and Phase 8 scores.

The question this module exists to answer is whether a higher score came with a better outcome.
It is deliberately *not* a probability calibration, and there is no Brier score in this file:
``rankScore`` orders the opportunities that existed at one moment and ``ensembleConfidence``
measures how much agreeing evidence a panel had, and neither has ever claimed to be the chance
of anything. Scoring them as if they had would be inventing a claim in order to grade it.

Binning convention, fixed for ``qst-analytics-v1``: bands are half-open upward — ``[0.0, 0.1)``,
``[0.1, 0.2)`` ... ``[0.9, 1.0]`` — with the top band closed so a score of exactly 1.0 belongs
somewhere. A value sits in the first band whose upper edge it is strictly below.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from quant_engine.analytics.metrics import (
    average,
    correlation_for,
    money_metrics,
    outcome_metrics,
    sample_label,
    spearman,
)
from quant_engine.analytics.models import (
    WEAK_CORRELATION,
    AnalyticsRow,
    AnalyticsSettings,
    CalibrationReport,
    Code,
    ScoreBin,
)


def bin_index(value: float, count: int) -> int:
    """Which band a score belongs to, by the documented convention.

    Compared against the same ``(index + 1) / count`` expression that defines each band's upper
    edge rather than computed as ``int(value * count)``. The multiplication is not exact in
    binary — ``0.3 * 10`` is 2.9999999999999996 — so the arithmetic shortcut would put a score
    of exactly 0.3 in the band below the one the documentation says it is in.
    """
    clamped = min(1.0, max(0.0, value))
    for index in range(count):
        if clamped < (index + 1) / count:
            return index
    return count - 1


def bounds(index: int, count: int) -> tuple[float, float]:
    return (index / count, (index + 1) / count)


def label_for(index: int, count: int) -> str:
    low, high = bounds(index, count)
    closing = "]" if index == count - 1 else ")"
    return f"[{low:.2f}, {high:.2f}{closing}"


def score_bins(
    rows: Sequence[AnalyticsRow],
    accessor: Callable[[AnalyticsRow], float | None],
    *,
    count: int,
    settings: AnalyticsSettings,
) -> list[ScoreBin]:
    """Every band, populated or not.

    Empty bands are returned rather than skipped: a curve that silently omitted the bands
    nothing ever scored in would look far better covered than it is.
    """
    buckets: list[list[AnalyticsRow]] = [[] for _ in range(count)]
    for row in rows:
        value = accessor(row)
        if value is None:
            continue
        buckets[bin_index(value, count)].append(row)
    return [
        ScoreBin(
            index=index,
            lowerBound=bounds(index, count)[0],
            upperBound=bounds(index, count)[1],
            inclusiveUpper=index == count - 1,
            label=label_for(index, count),
            scoreMean=average(bucket, accessor),
            outcomes=outcome_metrics(bucket, minimum=settings.minDisplaySample),
            money=money_metrics(
                bucket,
                bootstrap_iterations=settings.bootstrapIterations,
                seed=settings.bootstrapSeed + index,
            ),
        )
        for index, bucket in enumerate(buckets)
    ]


def monotonicity(bins: Sequence[ScoreBin], *, minimum: int) -> tuple[bool, float | None]:
    """Whether higher bands did better, whether that was answerable at all, and how strongly.

    Judged only over bands carrying at least ``minimum`` resolved outcomes. A band holding three
    trades is noise, and letting it decide whether a curve is monotonic would make the flag fire
    on every real dataset and therefore mean nothing — a warning that is always on is a warning
    nobody reads.

    The coefficient is computed over every populated band, thin ones included, because a rank
    association is a weaker claim than an ordering and degrades gracefully rather than flipping.
    """
    judged = [
        (item.index, rate)
        for item in bins
        if (rate := item.outcomes.winRateExcludingDraws) is not None
        and item.outcomes.resolved >= minimum
    ]
    populated = [
        (item.index, rate)
        for item in bins
        if (rate := item.outcomes.winRateExcludingDraws) is not None
    ]
    coefficient = (
        spearman([float(index) for index, _ in populated], [rate for _, rate in populated])
        if len(populated) >= 2
        else None
    )
    if len(judged) < 2:
        return (False, coefficient)
    rates = [rate for _, rate in judged]
    ordered = all(later >= earlier for earlier, later in zip(rates, rates[1:], strict=False))
    return (ordered, coefficient)


def calibration_report(
    rows: Sequence[AnalyticsRow],
    metric: str,
    accessor: Callable[[AnalyticsRow], float | None],
    *,
    settings: AnalyticsSettings,
    count: int | None = None,
    non_monotonic_code: Code | None = None,
    inverse_code: Code | None = None,
) -> CalibrationReport:
    """The full curve for one score, with the diagnostics that keep it honest.

    A score that does not work is reported as loudly as one that does. When the association
    between a score and its outcomes is absent the report says so, and when it is *inverted* it
    says that too — there is no path in this function that reinterprets a metric until the
    metric looks good.
    """
    bands = settings.binCount if count is None else count
    bins = score_bins(rows, accessor, count=bands, settings=settings)
    ordered, coefficient = monotonicity(bins, minimum=settings.minDisplaySample)
    correlation = correlation_for(rows, metric, accessor)
    populated = sum(1 for item in bins if item.outcomes.resolved > 0)
    warnings: list[Code] = []
    # The warnings are driven by the rank associations rather than by the strict ordering flag.
    # Strict non-decreasing order across ten bands essentially never survives real noise, so a
    # warning keyed on it would fire on every dataset forever and be read as decoration. The
    # ordering flag is still reported beside the coefficients, where a reader can weigh it.
    across_rows = correlation.coefficient
    inverted = across_rows is not None and across_rows <= -WEAK_CORRELATION
    # Absent evidence is reported as INSUFFICIENT_SAMPLE by the snapshot, not as a failed score.
    # These flags are for a score that was measurable and did not work.
    unhelpful = (across_rows is not None and across_rows < WEAK_CORRELATION) or (
        coefficient is not None and coefficient < WEAK_CORRELATION
    )
    if inverse_code is not None and inverted:
        warnings.append(inverse_code)
    if non_monotonic_code is not None and (inverted or unhelpful):
        warnings.append(non_monotonic_code)
    return CalibrationReport(
        metric=metric,
        binCount=bands,
        bins=bins,
        correlation=correlation,
        monotonic=ordered,
        monotonicityCoefficient=coefficient,
        populatedBins=populated,
        sampleCount=sum(item.outcomes.resolved for item in bins),
        sampleLabel=sample_label(
            sum(item.outcomes.resolved for item in bins), minimum=settings.minDisplaySample
        ),
        warnings=warnings,
        note=(
            "Empirical outcome curve, not a probability calibration: "
            f"{metric} is a relative ordering and has never claimed a win probability."
        ),
    )
