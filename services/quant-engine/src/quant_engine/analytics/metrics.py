"""Outcome and money statistics over a set of analysis rows.

Two rules run through every function here. A denominator of zero returns ``None`` rather than
an invented infinity or a silent zero, and a draw is never counted as half a win: it is its own
outcome, excluded from the binary rate and reported separately.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from collections.abc import Callable, Sequence
from statistics import fmean, median

from quant_engine.analytics.models import (
    MODERATE_CORRELATION,
    STRONG_CORRELATION,
    WEAK_CORRELATION,
    AnalyticsRow,
    Correlation,
    CorrelationStrength,
    MoneyMetrics,
    OutcomeMetrics,
    SampleLabel,
)

Z95 = 1.959963984540054
"""Two-sided 95% normal quantile, spelled out so the interval is reproducible to the digit."""

EMPTY_OUTCOMES = OutcomeMetrics(
    resolved=0, wins=0, losses=0, draws=0, sampleCount=0, sampleLabel="INSUFFICIENT_SAMPLE"
)
EMPTY_MONEY = MoneyMetrics(available=False, monetaryTrades=0)


def sample_label(count: int, *, minimum: int) -> SampleLabel:
    """How much weight a reader may put on a segment of this size.

    Nothing is hidden below the floor. A segment that is too thin to reason about is still
    reported with its raw counts and labelled, because a table that quietly dropped its thin
    slices would look far more certain than the evidence is.
    """
    if count <= 0:
        return "INSUFFICIENT_SAMPLE"
    return "OK" if count >= minimum else "LOW_SAMPLE"


def wilson_interval(
    successes: int, total: int, z: float = Z95
) -> tuple[float | None, float | None]:
    """Wilson score interval for a proportion.

    Used instead of the normal approximation everywhere in this package. On the sample sizes a
    freshly started installation actually has — eight resolved trades, six of them wins — the
    normal approximation produces bounds above 1.0, and an interval that claims impossible
    values is worse than no interval at all.
    """
    if total <= 0 or successes < 0 or successes > total:
        return (None, None)
    proportion = successes / total
    denominator = 1 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    spread = z * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total))
    margin = spread / denominator
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def outcome_metrics(rows: Sequence[AnalyticsRow], *, minimum: int) -> OutcomeMetrics:
    """Directional performance for one slice. Descriptive only.

    One pass rather than four. This is the hot function of the whole layer — every bin, cell,
    segment and threshold evaluation calls it — and at a hundred thousand outcomes the
    difference between one traversal and four is the difference between a panel that opens and
    one an operator gives up on.
    """
    wins = losses = draws = 0
    deltas: list[float] = []
    for row in rows:
        outcome = row.outcome
        if outcome == "WIN":
            wins += 1
        elif outcome == "LOSS":
            losses += 1
        else:
            draws += 1
        if row.priceDeltaBps is not None:
            deltas.append(row.priceDeltaBps)
    resolved = wins + losses + draws
    binary = wins + losses
    # Named "low"/"high" rather than "lower"/"upper": "higher" and "lower" are the two broker
    # controls, and the boundary test that scans this package for them cannot tell a statistic
    # from a button. A boundary test that has to guess is not a boundary test.
    low, high = wilson_interval(wins, binary)
    return OutcomeMetrics(
        resolved=resolved,
        wins=wins,
        losses=losses,
        draws=draws,
        winRateExcludingDraws=wins / binary if binary else None,
        winRateIncludingDraws=wins / resolved if resolved else None,
        drawRate=draws / resolved if resolved else None,
        lower95=low,
        upper95=high,
        averagePriceDeltaBps=fmean(deltas) if deltas else None,
        medianPriceDeltaBps=median(deltas) if deltas else None,
        sampleCount=resolved,
        sampleLabel=sample_label(resolved, minimum=minimum),
    )


def dominant_currency(rows: Sequence[AnalyticsRow]) -> tuple[str | None, list[str]]:
    """The one currency a money total may be denominated in, and every currency seen.

    Most priced outcomes wins; an exact tie is broken alphabetically so the choice is
    reproducible rather than dependent on iteration order. A row carrying a realized result but
    no currency label cannot be added to anything and is never the dominant one.
    """
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        if row.realizedPaperPnl is not None and row.paperCurrency is not None:
            counts[row.paperCurrency] += 1
    if not counts:
        return (None, [])
    best = min(counts, key=lambda code: (-counts[code], code))
    return (best, sorted(counts))


def money_metrics(
    rows: Sequence[AnalyticsRow],
    *,
    bootstrap_iterations: int = 0,
    seed: int = 0,
) -> MoneyMetrics:
    """Simulated money for one slice, in exactly one currency, or an honest unavailable.

    Two rules, and the second one is the reason this function is longer than it looks.

    Only rows that actually carry a realized simulated result take part: a resolved trade that
    ran with no configured stake contributes its direction to the outcome metrics and nothing
    at all here, so the two sample counts legitimately differ and both are reported.

    And **nothing is ever pooled across currencies.** A run that switched the simulated currency
    halfway through holds two incomparable sets of numbers, and adding them would produce a
    total that is not wrong in any particular row and is meaningless as a whole — the one number
    an operator is most likely to act on and least able to check. The dominant currency is
    aggregated and labelled; the rest are counted in ``excludedByCurrency`` and left out. There
    is no conversion anywhere in this application, exactly as Phase 9.5 refuses one.
    """
    currency, observed = dominant_currency(rows)
    priced = [row for row in rows if row.realizedPaperPnl is not None]
    if currency is None:
        return MoneyMetrics(
            available=False,
            monetaryTrades=0,
            currenciesObserved=observed,
            excludedByCurrency=len(priced),
            mixedCurrency=False,
        )
    counted = [row for row in priced if row.paperCurrency == currency]
    values = [row.realizedPaperPnl for row in counted if row.realizedPaperPnl is not None]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    gross_profit = math.fsum(wins)
    gross_loss = -math.fsum(losses)
    average_win = fmean(wins) if wins else None
    average_loss = fmean(losses) if losses else None
    low, high = (
        bootstrap_mean(values, iterations=bootstrap_iterations, seed=seed)
        if bootstrap_iterations
        else (None, None)
    )
    return MoneyMetrics(
        available=True,
        monetaryTrades=len(values),
        currency=currency,
        currenciesObserved=observed,
        excludedByCurrency=len(priced) - len(counted),
        mixedCurrency=len(observed) > 1,
        grossProfit=gross_profit,
        grossLoss=gross_loss,
        netPaperPnl=math.fsum(values),
        averageWin=average_win,
        averageLoss=average_loss,
        profitFactor=gross_profit / gross_loss if gross_loss > 0 else None,
        expectancyPerTrade=fmean(values) if values else None,
        payoffRatio=(
            average_win / abs(average_loss)
            if average_win is not None and average_loss is not None and average_loss != 0
            else None
        ),
        lowerMean95=low,
        upperMean95=high,
    )


def bootstrap_mean(
    values: Sequence[float], *, iterations: int, seed: int
) -> tuple[float | None, float | None]:
    """Percentile bootstrap interval for a mean, from a fixed seed.

    Seeded rather than randomized: an interval that moved every time the panel refreshed would
    invite rerunning until it looked narrow.
    """
    if iterations <= 0 or len(values) < 2:
        return (None, None)
    generator = random.Random(seed)
    size = len(values)
    means = sorted(
        fmean([values[generator.randrange(size)] for _ in range(size)]) for _ in range(iterations)
    )
    return (
        means[max(0, int(0.025 * iterations) - 1)],
        means[min(iterations - 1, int(0.975 * iterations))],
    )


def ranks(values: Sequence[float]) -> list[float]:
    """Average ranks, so tied scores cannot manufacture an ordering that was never observed."""
    order = sorted(range(len(values)), key=lambda index: values[index])
    result = [0.0] * len(values)
    position = 0
    while position < len(order):
        end = position
        while end + 1 < len(order) and values[order[end + 1]] == values[order[position]]:
            end += 1
        shared = (position + end) / 2 + 1
        for index in range(position, end + 1):
            result[order[index]] = shared
        position = end + 1
    return result


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Spearman rank correlation, or ``None`` when the question cannot be answered.

    ``None`` for fewer than three pairs or for a constant series: a correlation over two points
    is a line through two points, and a score that never varied has no ordering to correlate.
    """
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    rx, ry = ranks(xs), ranks(ys)
    mean_x, mean_y = fmean(rx), fmean(ry)
    dx = [value - mean_x for value in rx]
    dy = [value - mean_y for value in ry]
    denominator = math.sqrt(math.fsum(v * v for v in dx) * math.fsum(v * v for v in dy))
    if denominator == 0:
        return None
    return max(-1.0, min(1.0, math.fsum(a * b for a, b in zip(dx, dy, strict=True)) / denominator))


def strength_of(coefficient: float | None) -> CorrelationStrength:
    """Plain words for a coefficient, chosen so a weak one cannot be read as a finding."""
    if coefficient is None:
        return "NONE"
    magnitude = abs(coefficient)
    if magnitude < WEAK_CORRELATION:
        return "NEGLIGIBLE"
    if magnitude < MODERATE_CORRELATION:
        return "WEAK"
    if magnitude < STRONG_CORRELATION:
        return "MODERATE"
    return "STRONG"


def correlation_for(
    rows: Sequence[AnalyticsRow], metric: str, accessor: Callable[[AnalyticsRow], float | None]
) -> Correlation:
    """Does a higher score tend to come with a better outcome?

    Draws are excluded and the number excluded is reported. A draw is neither a win nor a loss,
    and folding it in as a half would invent an outcome the market never produced.
    """
    pairs = [
        (value, 1.0 if row.outcome == "WIN" else 0.0)
        for row in rows
        if row.binary and (value := accessor(row)) is not None
    ]
    coefficient = spearman([p[0] for p in pairs], [p[1] for p in pairs])
    strength = strength_of(coefficient)
    if coefficient is None:
        note = "Insufficient variation or sample to measure an ordering."
    else:
        # The direction is stated rather than left in the sign. A score that is *inversely*
        # related to outcomes is the most important thing this measurement can find, and a
        # sentence that reads the same either way would let it pass as a success.
        sense = "inverse" if coefficient < 0 else "positive"
        note = (
            f"{strength.capitalize()} {sense} rank association "
            f"between {metric} and a winning outcome."
        )
    return Correlation(
        metric=metric,
        coefficient=coefficient,
        sampleCount=len(pairs),
        drawsExcluded=sum(1 for row in rows if row.outcome == "DRAW"),
        strength=strength,
        note=note,
    )


def average(
    rows: Sequence[AnalyticsRow], accessor: Callable[[AnalyticsRow], float | None]
) -> float | None:
    values = [value for row in rows if (value := accessor(row)) is not None]
    return fmean(values) if values else None
