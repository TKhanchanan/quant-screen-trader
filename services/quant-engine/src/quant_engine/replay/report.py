"""Turning one replay into one report (Phase 11).

This module composes; it measures nothing of its own. Every number it assembles was computed by
``qst-analytics-v1`` or by the descriptive helpers beside it, and the only judgement it adds is
which caveats a reader has to be shown whether they asked for them or not.

Two separations are deliberate and load-bearing. CapitalBear and IQ Option are reported
separately before anything combined appears, because a five-second question and a sixty-second
one do not average. And the baseline is reported as ``CURRENT_FROZEN_PIPELINE`` — no threshold
changed, no Phase 10 finding applied, no regime filtered — so the research studies beside it can
never be mistaken for what the code would actually have done.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence

from quant_engine.analytics.dataset import terminal_rows
from quant_engine.analytics.models import AnalyticsRow
from quant_engine.configuration import Platform
from quant_engine.paper.models import PaperTrade
from quant_engine.replay import robustness
from quant_engine.replay.engine import ReplayResult
from quant_engine.replay.models import (
    MAX_ROWS,
    Code,
    LatencyScenario,
    OutcomeTally,
    ReplayEvidence,
    ReplaySummary,
    SessionGuardDay,
    SessionGuardScenarioReport,
    WalkForwardSummary,
)
from quant_engine.session_guard.models import DailySession

PLATFORMS: tuple[Platform, ...] = ("capitalbear", "iqoption")


def by_platform(rows: Sequence[AnalyticsRow]) -> dict[Platform, list[AnalyticsRow]]:
    grouped: dict[Platform, list[AnalyticsRow]] = {name: [] for name in PLATFORMS}
    for row in rows:
        grouped[row.platform].append(row)
    return grouped


def unresolved(trades: Sequence[PaperTrade]) -> dict[Platform, dict[str, int]]:
    """Cancelled and invalid trades per platform, from the terminal state of each trade.

    Collapsed through ``qst-analytics-v1``'s own rule rather than a second one: the durable
    record holds a row per transition, and two different opinions about which row is the trade
    would make the counts beside the win rate describe a different population from the win rate.
    """
    counts: dict[Platform, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for trade in terminal_rows(trades):
        if trade.status in ("CANCELLED", "INVALID"):
            counts[trade.platform][trade.status] += 1
    return {platform: dict(values) for platform, values in counts.items()}


def guard_report(sessions: Sequence[DailySession]) -> SessionGuardScenarioReport:
    """What a fixed session-guard configuration would have done inside the replay sandbox.

    Reconstructed from the sandbox guard's own session revisions, which is the same evidence
    Phase 9.5 keeps for a real day. Nothing here proposes a target or a limit, and no objective
    anywhere in this package is a function of either: Phase 11 evaluates the configuration it
    was handed and reports the result.
    """
    if not sessions:
        return SessionGuardScenarioReport(enabled=False)
    grouped: dict[str, list[DailySession]] = defaultdict(list)
    for session in sessions:
        grouped[str(session.sessionId)].append(session)
    rows: list[SessionGuardDay] = []
    for key in sorted(grouped, key=lambda item: grouped[item][0].startedAt):
        revisions = sorted(grouped[key], key=lambda item: item.revision)
        final = revisions[-1]
        triggered = next(
            (
                item
                for item in revisions
                if item.targetReachedAt is not None or item.lossLimitReachedAt is not None
            ),
            None,
        )
        stamp = None
        if triggered is not None:
            stamp = (
                triggered.targetReachedAt
                if triggered.targetReachedAt is not None
                else triggered.lossLimitReachedAt
            )
        rows.append(
            SessionGuardDay(
                localDate=final.sessionDate,
                tradesBeforeStop=(
                    triggered.resolvedTrades if triggered is not None else final.resolvedTrades
                ),
                targetReached=final.targetReachedAt is not None,
                lossLimitReached=final.lossLimitReachedAt is not None,
                timeToTriggerMs=(max(0, stamp - final.startedAt) if stamp is not None else None),
                pnlAtTrigger=triggered.realizedPnl if triggered is not None else None,
                finalPnl=final.realizedPnl,
                currency=final.currency,
            )
        )
    return SessionGuardScenarioReport(
        enabled=True,
        daysEvaluated=len(rows),
        daysTargetReached=sum(1 for row in rows if row.targetReached),
        daysLossLimitReached=sum(1 for row in rows if row.lossLimitReached),
        daysNeitherReached=sum(
            1 for row in rows if not row.targetReached and not row.lossLimitReached
        ),
        rows=rows[:MAX_ROWS],
    )


def latency_report(
    baseline: ReplayResult, scenarios: Sequence[tuple[int, ReplayResult]]
) -> list[LatencyScenario]:
    """The zero-delay baseline and every research delay beside it, in delay order.

    Each scenario is a complete second replay of the same history with one thing changed: when
    the decision is treated as actionable. The Phase 7 opinion and the Phase 8 board are the same
    objects with the same identities in every one of them, so a difference in the table is a
    difference in what the market did next.
    """
    rows = baseline.analysis.rows if baseline.analysis is not None else ()
    zero = robustness.latency_scenario(
        rows,
        delay_ms=0,
        selections=baseline.run.boardsSelected,
        baseline=None,
        invalid=baseline.run.paperInvalid,
    )
    results = [zero]
    for delay, result in sorted(scenarios, key=lambda item: item[0]):
        if delay == 0:
            continue
        results.append(
            robustness.latency_scenario(
                result.analysis.rows if result.analysis is not None else (),
                delay_ms=delay,
                selections=result.run.boardsSelected,
                baseline=zero,
                invalid=result.run.paperInvalid,
            )
        )
    return results


def build_summary(
    result: ReplayResult,
    *,
    latency: Sequence[LatencyScenario] = (),
    walk_forward: WalkForwardSummary | None = None,
    guard: SessionGuardScenarioReport | None = None,
) -> ReplaySummary:
    """The whole baseline backtest, platform by platform, with its caveats attached."""
    manifest = result.manifest
    settings = manifest.analyticsSettings
    rows = result.analysis.rows if result.analysis is not None else ()
    grouped = by_platform(rows)
    terminal = unresolved(result.trades)
    span = max(0, result.window.evaluationEnd - result.window.evaluationStart)
    tallies: list[OutcomeTally] = []
    for platform in manifest.platforms:
        tallies.append(
            robustness.tally(
                grouped[platform],
                platform=platform,
                duration_ms=manifest.paperSettings.duration_ms(platform),
                ensembles=result.ensemblesByPlatform.get(platform, 0),
                boards=result.boards.get(platform, {}),
                market_span_ms=span,
                unresolved=terminal.get(platform, {}),
                settings=settings,
                starting_capital=manifest.paperStartingCapital,
            )
        )
    combined = robustness.tally(
        rows,
        platform=None,
        duration_ms=0,
        ensembles=result.ensembles,
        boards=_merge(result.boards),
        market_span_ms=span,
        unresolved=_merge(terminal),
        settings=settings,
        starting_capital=manifest.paperStartingCapital,
    )
    view = robustness.coverage(
        rows,
        regimes=result.regimes,
        hour_buckets=result.hourBuckets,
        timezone=settings.timezone,
    )
    quality = result.analysis.quality if result.analysis is not None else None
    comparisons = walk_forward.totalComparisons if walk_forward is not None else 0
    warnings: list[Code] = robustness.warnings_for(
        rows=rows,
        dataset=result.dataset,
        view=view,
        platforms=manifest.platforms,
        resolution_rate=quality.resolvedRate if quality is not None else None,
        comparisons=comparisons,
        settings=settings,
        truncated=result.truncated,
    )
    if walk_forward is not None:
        warnings.extend(walk_forward.warnings)
    return ReplaySummary(
        replayRunId=result.run.replayRunId,
        dataset=result.dataset,
        causality=result.causality,
        analyticsSnapshotId=result.snapshot.snapshotId if result.snapshot is not None else None,
        datasetFingerprint=result.analysis.fingerprint if result.analysis is not None else "",
        overall=combined,
        platforms=tallies,
        coverage=view,
        daily=robustness.daily(rows, settings.timezone),
        contributions=robustness.contributions(rows),
        rolling=robustness.rolling(rows),
        equityPoints=len(robustness.equity(rows)),
        latency=list(latency)[:16],
        payout=robustness.payout_scenarios(rows, manifest.payoutScenarios),
        walkForward=walk_forward,
        sessionGuard=guard,
        warnings=sorted(set(warnings))[:32],
    )


def build_evidence(result: ReplayResult, summary: ReplaySummary) -> ReplayEvidence:
    """The durable research artefact for a phase that does not exist yet.

    Written and then left alone. Nothing in Phases 6-10, nothing in the execution layer and
    nothing in the desktop reads this file; a test searches the repository for a consumer and
    asserts there is none. Phase 12 is the first phase allowed to *consider* it.
    """
    folds = summary.walkForward.rows if summary.walkForward is not None else []
    findings = result.snapshot.research if result.snapshot is not None else []
    return ReplayEvidence(
        replayRunId=result.run.replayRunId,
        inputFingerprint=result.run.inputFingerprint,
        settingsFingerprint=result.run.settingsFingerprint,
        baselineAnalyticsSnapshotId=summary.analyticsSnapshotId,
        sourceMode=result.run.sourceMode,
        entryLayer=result.run.entryLayer,
        baseline=summary.overall,
        platformBaselines=summary.platforms,
        walkForwardFolds=list(folds),
        stableDirectionalCandidates=[
            f"{fold.candidateMetric} >= {fold.candidateThreshold:.2f}"
            for fold in folds
            if fold.directionalStable
            and fold.candidateMetric is not None
            and fold.candidateThreshold is not None
        ],
        stableMonetaryCandidates=[
            f"{fold.candidateMetric} >= {fold.candidateThreshold:.2f}"
            for fold in folds
            if fold.monetaryStable
            and fold.candidateMetric is not None
            and fold.candidateThreshold is not None
        ],
        stableRegimes=[
            item.subject
            for item in findings
            if item.stable and item.kind in ("REGIME", "SKIP_CONDITION")
        ][:MAX_ROWS],
        stableStrategyRegimes=[
            item.subject for item in findings if item.stable and item.kind == "STRATEGY_REGIME"
        ][:MAX_ROWS],
        latencySensitivity=list(summary.latency),
        coverage=summary.coverage,
        warnings=list(summary.warnings),
    )


def _merge(values: Mapping[Platform, Mapping[str, int]]) -> dict[str, int]:
    merged: dict[str, int] = defaultdict(int)
    for counts in values.values():
        for key, count in counts.items():
            merged[key] += count
    return dict(merged)
