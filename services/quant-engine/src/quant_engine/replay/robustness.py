"""Descriptive robustness over a replayed history (Phase 11).

Everything here answers the same family of questions: *how much of a market did this backtest
actually see, and did whatever it found stay found?* Coverage by regime, asset, hour and date;
the simulated equity path and the worst run inside it; rolling windows that show whether an edge
was stable, decaying or episodic; contribution by platform, asset, regime and direction; and the
two sensitivity studies — decision latency and payout rate — that ask whether a result survives
assumptions nobody measured.

Two rules run through all of it.

**Descriptive only.** Nothing here removes an asset, filters a regime, picks a latency or sets a
payout. A contribution table is not a whitelist and a latency table is not a setting: the
best-looking delay in a history is a property of that history.

**Money in one currency or not at all.** Every monetary figure is produced by
``qst-analytics-v1``'s own ``money_metrics``, which aggregates the dominant currency and counts
the rest as excluded. There is no conversion anywhere in this application.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from statistics import fmean, median
from zoneinfo import ZoneInfo

from quant_engine.analytics.metrics import money_metrics, outcome_metrics
from quant_engine.analytics.models import AnalyticsRow, AnalyticsSettings
from quant_engine.analytics.segmentation import REGIMES
from quant_engine.configuration import Platform
from quant_engine.replay.models import (
    ASSET_CONCENTRATION_SHARE,
    HIGH_GAP_SHARE,
    MAX_ROWS,
    MIN_HOURS_COVERED,
    MIN_SELECTION_SAMPLE,
    MIN_TRADING_DATES,
    REGIME_DOMINANCE_SHARE,
    Code,
    ContributionRow,
    DailyDistribution,
    DailyPerformance,
    EquityPoint,
    LatencyScenario,
    OutcomeTally,
    PayoutScenario,
    ReplayCoverage,
    ReplayDatasetSummary,
    RollingWindow,
)
from quant_engine.strategy.models import Regime

ROLLING_WINDOW = 100
"""Rolling windows of a hundred consecutive outcomes. Descriptive: the point is the shape of the
series, not any one window's number."""

HOUR_MS = 3_600_000
DAY_MS = 86_400_000


def streaks(rows: Sequence[AnalyticsRow]) -> tuple[int, int]:
    """Longest runs of wins and of losses, in resolution order.

    Reported because an operator will experience them, and for no other reason. A streak is not
    a signal, and nothing in this application sizes anything from one.
    """
    best_win = best_loss = run_win = run_loss = 0
    for row in rows:
        run_win = run_win + 1 if row.outcome == "WIN" else 0
        run_loss = run_loss + 1 if row.outcome == "LOSS" else 0
        best_win = max(best_win, run_win)
        best_loss = max(best_loss, run_loss)
    return (best_win, best_loss)


def equity(rows: Sequence[AnalyticsRow]) -> list[EquityPoint]:
    """The simulated paper equity path, in one currency, in settlement order.

    ``SIMULATED PAPER EQUITY``. No account was funded, no order was placed and no broker balance
    was read; this is the running total of an arithmetic exercise over recorded price moves.
    """
    priced = [
        row for row in rows if row.realizedPaperPnl is not None and row.paperCurrency is not None
    ]
    if not priced:
        return []
    currency = money_metrics(priced).currency
    if currency is None:
        return []
    points: list[EquityPoint] = []
    cumulative = peak = 0.0
    for row in sorted(priced, key=lambda item: (item.expiryTime, str(item.paperTradeId))):
        if row.paperCurrency != currency or row.realizedPaperPnl is None:
            continue
        cumulative += row.realizedPaperPnl
        peak = max(peak, cumulative)
        points.append(
            EquityPoint(
                settledAt=row.expiryTime,
                tradeId=row.paperTradeId,
                platform=row.platform,
                assetName=row.assetName,
                outcome=row.outcome,
                pnl=row.realizedPaperPnl,
                cumulativePnl=cumulative,
                peak=peak,
                drawdown=max(0.0, peak - cumulative),
                currency=currency,
            )
        )
    return points


def drawdown(
    points: Sequence[EquityPoint], starting_capital: float | None
) -> tuple[float | None, float | None]:
    """The deepest trough below the running peak, and its share of a *stated* starting balance.

    The relative figure is ``None`` unless the operator supplied a simulated starting capital.
    Expressing a drawdown as a percentage of an account nobody named would be inventing the
    account, which is the one number an operator is most likely to read as real.
    """
    if not points:
        return (None, None)
    worst = max(point.drawdown for point in points)
    if starting_capital is None or starting_capital <= 0:
        return (worst, None)
    return (worst, worst / starting_capital)


def rolling(rows: Sequence[AnalyticsRow], window: int = ROLLING_WINDOW) -> list[RollingWindow]:
    """Consecutive windows over the ordered outcomes, so decay and episodes are visible."""
    if window < 2 or len(rows) < window:
        return []
    results: list[RollingWindow] = []
    step = max(1, window // 2)
    for index, start in enumerate(range(0, len(rows) - window + 1, step)):
        slice_ = rows[start : start + window]
        outcomes = outcome_metrics(slice_, minimum=window)
        money = money_metrics(slice_)
        results.append(
            RollingWindow(
                index=index,
                startTime=slice_[0].expiryTime,
                endTime=slice_[-1].expiryTime,
                resolved=outcomes.resolved,
                winRateExcludingDraws=outcomes.winRateExcludingDraws,
                expectancyPerTrade=money.expectancyPerTrade,
                profitFactor=money.profitFactor,
            )
        )
        if len(results) >= MAX_ROWS:
            break
    return results


def _contribution(
    dimension: str, key: str, slice_: Sequence[AnalyticsRow], total: int
) -> ContributionRow:
    outcomes = outcome_metrics(slice_, minimum=MIN_SELECTION_SAMPLE)
    money = money_metrics(slice_)
    return ContributionRow(
        dimension="PLATFORM"
        if dimension == "PLATFORM"
        else (
            "ASSET"
            if dimension == "ASSET"
            else ("REGIME" if dimension == "REGIME" else "DIRECTION")
        ),
        key=key,
        resolved=outcomes.resolved,
        share=outcomes.resolved / total if total else 0.0,
        wins=outcomes.wins,
        losses=outcomes.losses,
        draws=outcomes.draws,
        winRateExcludingDraws=outcomes.winRateExcludingDraws,
        netPaperPnl=money.netPaperPnl,
        currency=money.currency,
    )


def contributions(rows: Sequence[AnalyticsRow]) -> list[ContributionRow]:
    """Where the simulated result came from. Descriptive, never a selection rule."""
    total = len(rows)
    grouped: dict[str, dict[str, list[AnalyticsRow]]] = {
        "PLATFORM": defaultdict(list),
        "ASSET": defaultdict(list),
        "REGIME": defaultdict(list),
        "DIRECTION": defaultdict(list),
    }
    for row in rows:
        grouped["PLATFORM"][row.platform].append(row)
        grouped["ASSET"][row.assetName].append(row)
        grouped["REGIME"][row.primaryRegime].append(row)
        grouped["DIRECTION"][row.direction].append(row)
    result: list[ContributionRow] = []
    for dimension in ("PLATFORM", "ASSET", "REGIME", "DIRECTION"):
        for key in sorted(grouped[dimension]):
            result.append(_contribution(dimension, key, grouped[dimension][key], total))
    return result[:MAX_ROWS]


def daily(rows: Sequence[AnalyticsRow], timezone: str) -> DailyDistribution:
    """Per local trading date, in an explicitly named timezone. No claim of daily income."""
    grouped: dict[str, list[AnalyticsRow]] = defaultdict(list)
    for row in rows:
        grouped[row.localDate].append(row)
    currency = money_metrics(rows).currency
    entries: list[DailyPerformance] = []
    values: list[float] = []
    for date in sorted(grouped):
        slice_ = grouped[date]
        outcomes = outcome_metrics(slice_, minimum=MIN_SELECTION_SAMPLE)
        money = money_metrics(slice_)
        net = money.netPaperPnl if money.currency == currency else None
        if net is not None:
            values.append(net)
        entries.append(
            DailyPerformance(
                localDate=date,
                timezone=timezone,
                resolved=outcomes.resolved,
                wins=outcomes.wins,
                losses=outcomes.losses,
                draws=outcomes.draws,
                netPaperPnl=net,
                currency=money.currency,
            )
        )
    return DailyDistribution(
        days=len(entries),
        profitableDays=sum(1 for value in values if value > 0),
        losingDays=sum(1 for value in values if value < 0),
        flatDays=sum(1 for value in values if value == 0),
        meanDailyPnl=fmean(values) if values else None,
        medianDailyPnl=median(values) if values else None,
        currency=currency,
        rows=entries[:MAX_ROWS],
    )


def coverage(
    rows: Sequence[AnalyticsRow],
    *,
    regimes: Mapping[str, int],
    hour_buckets: frozenset[int],
    timezone: str,
) -> ReplayCoverage:
    """How broad the replayed history was, quite apart from how well it did.

    The regime counts are over every classification the pipeline produced, not only the ones
    that became a trade: a backtest that only counted the regimes it traded in could never show
    that it never saw one. The hour, weekday and date counts are over market time the evaluation
    window actually received data in, so a hundred thousand events from one afternoon cannot be
    reported as broad history.
    """
    total_regimes = sum(regimes.values())
    dominant = max(regimes.items(), key=lambda item: (item[1], item[0]), default=None)
    asset_rows = [row for row in contributions(rows) if row.dimension == "ASSET"]
    top = max(asset_rows, key=lambda row: (row.share, row.key), default=None)
    zone = ZoneInfo(timezone)
    stamps = [datetime.fromtimestamp(bucket * HOUR_MS / 1000, tz=zone) for bucket in hour_buckets]
    # Through the known regime set rather than by casting: a classification this build does
    # not recognise is a contract mismatch, and quietly typing it as one would hide that.
    counted: dict[Regime, int] = {name: regimes[name] for name in REGIMES if regimes.get(name)}
    return ReplayCoverage(
        regimeCounts=counted,
        dominantRegime=dominant[0] if dominant else None,
        dominantRegimeShare=dominant[1] / total_regimes if dominant and total_regimes else None,
        assetShares=asset_rows[:MAX_ROWS],
        topAsset=top.key if top else None,
        topAssetShare=top.share if top else None,
        hoursCovered=len({stamp.hour for stamp in stamps}),
        weekdaysCovered=len({stamp.weekday() for stamp in stamps}),
        tradingDates=len({stamp.date() for stamp in stamps}),
        timezone=timezone,
    )


def tally(
    rows: Sequence[AnalyticsRow],
    *,
    platform: Platform | None,
    duration_ms: int,
    ensembles: int,
    boards: Mapping[str, int],
    market_span_ms: int,
    unresolved: Mapping[str, int],
    settings: AnalyticsSettings,
    starting_capital: float | None,
) -> OutcomeTally:
    """One platform's whole baseline result, or the combined view when ``platform`` is ``None``.

    The combined view carries ``durationMs`` of zero and only ever appears beside the
    per-platform ones: CapitalBear measures a five-second question and IQ Option a sixty-second
    one, and one unlabelled number over both would be an average of two different questions.
    """
    outcomes = outcome_metrics(rows, minimum=settings.minDisplaySample)
    money = money_metrics(
        rows, bootstrap_iterations=settings.bootstrapIterations, seed=settings.bootstrapSeed
    )
    finalized = sum(count for status, count in boards.items() if status != "SELECTED")
    selected = boards.get("SELECTED", 0)
    win_streak, loss_streak = streaks(rows)
    points = equity(rows)
    worst, relative = drawdown(points, starting_capital)
    hours = market_span_ms / HOUR_MS if market_span_ms > 0 else None
    days = market_span_ms / DAY_MS if market_span_ms > 0 else None
    return OutcomeTally(
        platform=platform,
        durationMs=duration_ms,
        ensembles=ensembles,
        boardsFinalized=finalized,
        boardsSelected=selected,
        boardsNoOpportunity=boards.get("NO_OPPORTUNITY", 0),
        boardsPartial=boards.get("PARTIAL", 0),
        selectionCoverage=selected / finalized if finalized else None,
        trades=len(rows) + sum(unresolved.values()),
        resolved=outcomes.resolved,
        wins=outcomes.wins,
        losses=outcomes.losses,
        draws=outcomes.draws,
        invalid=unresolved.get("INVALID", 0),
        cancelled=unresolved.get("CANCELLED", 0),
        winRateExcludingDraws=outcomes.winRateExcludingDraws,
        lower95=outcomes.lower95,
        upper95=outcomes.upper95,
        averagePriceDeltaBps=outcomes.averagePriceDeltaBps,
        tradesPerHour=selected / hours if hours else None,
        tradesPerDay=selected / days if days else None,
        maxWinStreak=win_streak,
        maxLossStreak=loss_streak,
        moneyAvailable=money.available,
        currency=money.currency,
        monetaryTrades=money.monetaryTrades,
        mixedCurrency=money.mixedCurrency,
        excludedByCurrency=money.excludedByCurrency,
        grossProfit=money.grossProfit,
        grossLoss=money.grossLoss,
        netPaperPnl=money.netPaperPnl,
        profitFactor=money.profitFactor,
        expectancyPerTrade=money.expectancyPerTrade,
        payoffRatio=money.payoffRatio,
        expectancyLower95=money.lowerMean95,
        expectancyUpper95=money.upperMean95,
        maxDrawdown=worst,
        maxDrawdownRelative=relative,
    )


def latency_scenario(
    rows: Sequence[AnalyticsRow],
    *,
    delay_ms: int,
    selections: int,
    baseline: LatencyScenario | None,
    invalid: int,
) -> LatencyScenario:
    """One decision-delay scenario, beside the zero-delay baseline it is compared to.

    The same boards, the same selections and the same deterministic trade ids in every scenario:
    only the moment the decision is treated as actionable moves, so a difference here is a
    difference in what the market did next and never a difference in what the analysis said.
    """
    outcomes = outcome_metrics(rows, minimum=MIN_SELECTION_SAMPLE)
    money = money_metrics(rows)
    rate = outcomes.winRateExcludingDraws
    expectancy = money.expectancyPerTrade
    delta = outcomes.averagePriceDeltaBps
    return LatencyScenario(
        delayMs=delay_ms,
        selections=selections,
        resolved=outcomes.resolved,
        wins=outcomes.wins,
        losses=outcomes.losses,
        draws=outcomes.draws,
        invalid=invalid,
        winRateExcludingDraws=rate,
        expectancyPerTrade=expectancy,
        averagePriceDeltaBps=delta,
        winRateDelta=(
            rate - baseline.winRateExcludingDraws
            if baseline is not None
            and rate is not None
            and baseline.winRateExcludingDraws is not None
            else None
        ),
        expectancyDelta=(
            expectancy - baseline.expectancyPerTrade
            if baseline is not None
            and expectancy is not None
            and baseline.expectancyPerTrade is not None
            else None
        ),
        priceDeltaBpsDelta=(
            delta - baseline.averagePriceDeltaBps
            if baseline is not None
            and delta is not None
            and baseline.averagePriceDeltaBps is not None
            else None
        ),
    )


def payout_scenarios(rows: Sequence[AnalyticsRow], rates: Sequence[float]) -> list[PayoutScenario]:
    """The same recorded outcomes re-priced at stated payout rates. No direction is changed.

    Only rows that already carry an explicit simulated stake take part, and only in one
    currency. No payout is ever inferred from a broker panel, and the break-even rate is printed
    beside every scenario so an apparent directional edge can be read against the payout it
    would actually need.
    """
    priced = [row for row in rows if row.paperStake is not None and row.paperCurrency is not None]
    if not priced or not rates:
        return []
    currency = money_metrics(priced).currency
    counted = [row for row in priced if row.paperCurrency == currency]
    if not counted:
        return []
    results: list[PayoutScenario] = []
    for rate in sorted(set(rates)):
        values = [_repriced(row, rate) for row in counted]
        wins = [value for value in values if value > 0]
        losses = [value for value in values if value < 0]
        gross_loss = -math.fsum(losses)
        expectancy = fmean(values) if values else None
        break_even = 1 / (1 + rate) if rate > 0 else None
        results.append(
            PayoutScenario(
                payoutRate=rate,
                resolved=len(values),
                expectancyPerTrade=expectancy,
                netPaperPnl=math.fsum(values),
                profitFactor=math.fsum(wins) / gross_loss if gross_loss > 0 else None,
                breakEvenWinRate=break_even,
                currency=currency,
                aboveBreakEven=None if expectancy is None else expectancy > 0,
            )
        )
    return results[:MAX_ROWS]


def _repriced(row: AnalyticsRow, rate: float) -> float:
    stake = row.paperStake if row.paperStake is not None else 0.0
    if row.outcome == "WIN":
        return stake * rate
    return -stake if row.outcome == "LOSS" else 0.0


def warnings_for(
    *,
    rows: Sequence[AnalyticsRow],
    dataset: ReplayDatasetSummary,
    view: ReplayCoverage,
    platforms: Sequence[Platform],
    resolution_rate: float | None,
    comparisons: int,
    settings: AnalyticsSettings,
    truncated: bool,
) -> list[Code]:
    """Every caveat a reader must be shown, whether or not they went looking for it.

    Ordered by how badly a reader could be misled without it. A thin sample first, because a
    backtest over forty trades is the single most likely thing here to be read as a result.
    """
    found: list[Code] = []
    if len(rows) < MIN_SELECTION_SAMPLE:
        found.append("INSUFFICIENT_HISTORY")
        found.append("LOW_SELECTION_COUNT")
    if resolution_rate is not None and resolution_rate < settings.minResolutionRate:
        found.append("LOW_RESOLUTION_RATE")
    if view.tradingDates < MIN_TRADING_DATES or view.hoursCovered < MIN_HOURS_COVERED:
        found.append("NARROW_TIME_COVERAGE")
    if view.dominantRegimeShare is not None and view.dominantRegimeShare >= REGIME_DOMINANCE_SHARE:
        found.append("SINGLE_REGIME_DOMINANCE")
    if len(view.regimeCounts) <= 1:
        found.append("LIMITED_REGIME_COVERAGE")
    if view.topAssetShare is not None and view.topAssetShare >= ASSET_CONCENTRATION_SHARE:
        found.append("ASSET_CONCENTRATION_WARNING")
    if any(row.resolved < settings.minAssetSample for row in view.assetShares):
        found.append("LOW_ASSET_SAMPLE")
    if dataset.gapShare is not None and dataset.gapShare >= HIGH_GAP_SHARE:
        found.append("HIGH_GAP_RATE")
    money = money_metrics(rows)
    if money.mixedCurrency:
        found.append("MIXED_CURRENCY")
    if not money.available:
        found.append("PAPER_ACCOUNTING_UNAVAILABLE")
        found.append("MONETARY_UNVERIFIED")
    if len(platforms) < 2:
        found.append("SINGLE_PLATFORM")
    if comparisons > 1:
        found.append("MULTIPLE_TESTING_WARNING")
    if truncated:
        found.append("STRATEGY_EVIDENCE_TRUNCATED")
    if dataset.sourceMode == "SYNTHETIC":
        # Never removed and never softened. A synthetic fixture proves software behaviour; it is
        # not evidence about any market, and its numbers are not backtest performance.
        found.append("SYNTHETIC_BEHAVIOR_TEST")
    if dataset.diagnostics.malformed:
        found.append("MALFORMED_INPUT_ROWS")
    if dataset.diagnostics.duplicates or dataset.diagnostics.identityCollisions:
        found.append("DUPLICATE_INPUT_ROWS")
    return found


def utc_date(stamp: int) -> str:
    return datetime.fromtimestamp(stamp / 1000, UTC).strftime("%Y-%m-%d")
