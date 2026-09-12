"""Orchestration: one dataset in, one snapshot out.

The snapshot is a pure function of the rows, the settings and the filters, so the same recorded
history analysed twice produces the same identifier and the same numbers — which is what makes
a snapshot something two people can argue about rather than something that moved between them.

This module composes; it decides nothing. Every metric it assembles is computed elsewhere, and
the only judgement it adds is which diagnostics a reader must be shown whether they asked for
them or not.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Literal
from uuid import UUID, uuid5

from quant_engine.analytics import calibration, segmentation, thresholds
from quant_engine.analytics.dataset import GENERATED_FROM, settings_fingerprint
from quant_engine.analytics.metrics import (
    correlation_for,
    money_metrics,
    outcome_metrics,
    sample_label,
)
from quant_engine.analytics.models import (
    ANALYTICS_VERSION,
    MAX_FINDINGS,
    MAX_WARNINGS,
    AnalyticsDataset,
    AnalyticsFilters,
    AnalyticsRow,
    AnalyticsSettings,
    AnalyticsSnapshot,
    CalibrationReport,
    Code,
    MatrixCell,
    MoneyMetrics,
    OutcomeMetrics,
    ResearchFinding,
    SegmentMetrics,
    ThresholdCandidate,
)
from quant_engine.analytics.segmentation import stance_of

ANALYTICS_NAMESPACE = UUID("2c9b6f41-8d3e-4f70-9a52-6b1c8d0e5a37")
"""Fixed UUID5 namespace for snapshot identity. Never regenerated: a new namespace would give
the same analysis of the same history a different id and break every stored comparison."""


class AnalyticsEngine:
    """Turns one dataset into one snapshot. Holds no state between calls."""

    def __init__(self, settings: AnalyticsSettings | None = None) -> None:
        self.settings = settings if settings is not None else AnalyticsSettings()

    def analyze(
        self, dataset: AnalyticsDataset, filters: AnalyticsFilters | None = None
    ) -> AnalyticsSnapshot:
        settings = self.settings
        selection = filters if filters is not None else AnalyticsFilters()
        rows = dataset.rows

        rank = calibration.calibration_report(
            rows,
            "rankScore",
            lambda row: row.rankScore,
            settings=settings,
            non_monotonic_code="NON_MONOTONIC_RANK_SCORE",
            inverse_code="INVERSE_RANK_SCORE",
        )
        confidence = calibration.calibration_report(
            rows,
            "ensembleConfidence",
            lambda row: row.ensembleConfidence,
            settings=settings,
            non_monotonic_code="NON_MONOTONIC_CONFIDENCE",
            inverse_code="INVERSE_CONFIDENCE",
        )
        correlations = [
            correlation_for(rows, "rankScore", lambda row: row.rankScore),
            correlation_for(rows, "ensembleConfidence", lambda row: row.ensembleConfidence),
            correlation_for(rows, "agreement", lambda row: row.agreement),
            correlation_for(rows, "regimeConfidence", lambda row: row.regimeConfidence),
            correlation_for(rows, "leadMargin", lambda row: row.leadMargin),
        ]

        platforms = segmentation.by_platform(rows, settings=settings)
        regimes = segmentation.by_regime(rows, settings=settings)
        assets = segmentation.by_asset(rows, settings=settings)
        hours = segmentation.by_hour(rows, settings=settings)
        weekdays = segmentation.by_weekday(rows, settings=settings)
        quality_segments = segmentation.by_quality(rows, settings=settings)
        strategy_metrics = segmentation.strategy_contributions(rows, settings=settings)
        strategy_regime = segmentation.strategy_regime_matrix(rows, settings=settings)

        split = thresholds.chronological_split(rows, settings=settings)
        candidates, comparisons = thresholds.discover(split, settings=settings)

        overall = outcome_metrics(rows, minimum=settings.minDisplaySample)
        money = money_metrics(
            rows,
            bootstrap_iterations=settings.bootstrapIterations,
            seed=settings.bootstrapSeed,
        )
        warnings = _warnings(
            dataset=dataset,
            rows=rows,
            rank=rank,
            confidence=confidence,
            candidates=candidates,
            comparisons=comparisons,
            platforms=platforms,
            segments=[*regimes, *assets, *hours, *weekdays],
            money=money,
            strategy_samples=strategy_regime.sampleCount,
            settings=settings,
        )
        fingerprint = settings_fingerprint(settings, selection)
        return AnalyticsSnapshot(
            snapshotId=snapshot_id(dataset.fingerprint, fingerprint),
            analyticsVersion=ANALYTICS_VERSION,
            generatedFrom=GENERATED_FROM,
            datasetFingerprint=dataset.fingerprint,
            settingsFingerprint=fingerprint,
            sampleStart=dataset.sampleStart,
            sampleEnd=dataset.sampleEnd,
            totalResolved=len(rows),
            timezone=settings.timezone,
            filters=selection,
            settings=settings,
            quality=dataset.quality,
            overallMetrics=overall,
            overallMoney=money,
            platformMetrics=platforms,
            rankCalibration=rank,
            confidenceCalibration=confidence,
            correlations=correlations,
            regimeMetrics=regimes,
            regimeDirectionMatrix=segmentation.regime_direction_matrix(rows, settings=settings),
            regimeConfidenceBins=segmentation.regime_confidence_bins(rows, settings=settings),
            strategyMetrics=strategy_metrics,
            strategyRegimeMatrix=strategy_regime,
            agreementBins=segmentation.agreement_bins(rows, settings=settings),
            leadMarginBins=segmentation.lead_margin_bins(rows, settings=settings),
            rankConfidenceMatrix=segmentation.rank_confidence_matrix(rows, settings=settings),
            assetMetrics=assets,
            hourMetrics=hours,
            weekdayMetrics=weekdays,
            qualityMetrics=quality_segments,
            temporalSplit=thresholds.split_report(split, settings=settings),
            thresholdCandidates=candidates,
            comparisonsEvaluated=comparisons,
            research=research_findings(
                split=split,
                candidates=candidates,
                regimes=regimes,
                strategy_cells=strategy_regime.cells,
                settings=settings,
            ),
            warnings=warnings,
        )


def snapshot_id(dataset_fingerprint: str, settings_hash: str) -> UUID:
    return uuid5(ANALYTICS_NAMESPACE, f"{ANALYTICS_VERSION}|{dataset_fingerprint}|{settings_hash}")


def _warnings(
    *,
    dataset: AnalyticsDataset,
    rows: Sequence[AnalyticsRow],
    rank: CalibrationReport,
    confidence: CalibrationReport,
    candidates: Sequence[ThresholdCandidate],
    comparisons: int,
    platforms: Sequence[SegmentMetrics],
    segments: Sequence[SegmentMetrics],
    money: MoneyMetrics,
    strategy_samples: int,
    settings: AnalyticsSettings,
) -> list[Code]:
    """Every caveat a reader must see, whether or not they went looking for it.

    The score diagnostics are surfaced first and never softened. If a higher rank score did not
    come with better outcomes, that is the single most useful thing this whole layer can say —
    it may mean the score is weak, the sample is too small, regimes are mixing or the capture is
    degraded — and hiding it to keep a dashboard tidy would defeat the point of measuring.
    """
    warnings: list[Code] = []
    quality = dataset.quality
    if sample_label(len(rows), minimum=settings.minDisplaySample) != "OK":
        warnings.append("INSUFFICIENT_SAMPLE")
    if quality.resolvedRate is not None and quality.resolvedRate < settings.minResolutionRate:
        warnings.append("LOW_RESOLUTION_RATE")
    if len(quality.versionsObserved) > 1 or quality.unsupportedVersions:
        warnings.append("VERSION_MIXED")
    warnings.extend(rank.warnings)
    warnings.extend(confidence.warnings)
    if any(item.outcomes.sampleLabel != "OK" for item in segments):
        warnings.append("LOW_SAMPLE_SEGMENTS")
    if money.mixedCurrency:
        # Named separately from the money itself: the total on screen is real, and it is a
        # total of part of the record. A reader who cannot see that would read it as all of it.
        warnings.append("MIXED_CURRENCY")
    if not money.available:
        warnings.append("PAPER_ACCOUNTING_UNAVAILABLE")
    if not strategy_samples:
        warnings.append("NO_STRATEGY_EVIDENCE")
    if len(platforms) < 2:
        warnings.append("SINGLE_PLATFORM")
    if any(not candidate.stable for candidate in candidates):
        warnings.append("THRESHOLD_UNSTABLE")
    if comparisons > 1:
        warnings.append("MULTIPLE_TESTING_WARNING")
    return warnings


def _periods(
    split: thresholds.TemporalSplit,
    predicate: Callable[[AnalyticsRow], bool],
    *,
    settings: AnalyticsSettings,
) -> tuple[list[OutcomeMetrics], list[OutcomeMetrics]]:
    """One slice measured inside each chronological period, beside that period's own baseline.

    The baseline travels with the slice because a period is not a constant: a stretch of history
    where everything won makes any subset of it look good, and comparing a slice to a pooled
    all-time average would credit it for sitting in an easy month.
    """
    names: tuple[str, ...] = ("TRAIN", "VALIDATION", "TEST")
    selected = []
    baselines = []
    for name in names:
        rows = split.of(name)  # type: ignore[arg-type]
        selected.append(
            outcome_metrics(
                [row for row in rows if predicate(row)], minimum=settings.minDisplaySample
            )
        )
        baselines.append(outcome_metrics(rows, minimum=settings.minDisplaySample))
    return (selected, baselines)


def _out_of_sample(
    pooled: OutcomeMetrics,
    periods: Sequence[OutcomeMetrics],
    baselines: Sequence[OutcomeMetrics],
    *,
    favourable: bool,
    settings: AnalyticsSettings,
) -> tuple[bool, list[Code]]:
    """Whether a slice held its direction in every period, with evidence in each.

    Applied to regimes and strategy pairings for the same reason it is applied to thresholds: a
    regime table is a set of slices chosen *after* seeing the history it is describing, which is
    the same selection bias a threshold search has. A tight pooled Wilson bound is not
    out-of-sample evidence, however convincing it looks on one pass through the data.
    """
    reasons: list[Code] = []
    if pooled.sampleCount < settings.minRecommendationSample:
        reasons.append("POOLED_SAMPLE_BELOW_MINIMUM")
    thin = [
        name
        for name, period in zip(("TRAIN", "VALIDATION", "TEST"), periods, strict=True)
        if period.sampleCount < settings.minDisplaySample
    ]
    if thin:
        reasons.append("OUT_OF_SAMPLE_TOO_SMALL")
        reasons.extend(f"THIN_IN_{name}" for name in thin)
        return (False, reasons)
    held = []
    for name, period, baseline in zip(
        ("TRAIN", "VALIDATION", "TEST"), periods, baselines, strict=True
    ):
        rate, base = period.winRateExcludingDraws, baseline.winRateExcludingDraws
        if rate is None or base is None or (rate > base) != favourable:
            reasons.append(f"NOT_HELD_IN_{name}")
            held.append(False)
        else:
            held.append(True)
    if not all(held):
        reasons.append("DIRECTION_NOT_CONSISTENT")
        return (False, reasons)
    if reasons:
        return (False, reasons)
    reasons.append("CONSISTENT_ACROSS_SPLITS")
    return (True, reasons)


def _in_regime(regime: str) -> Callable[[AnalyticsRow], bool]:
    """A closure rather than a lambda with a default argument.

    Both avoid late binding; only one of them says so to a reader who has not been bitten by it.
    """

    def matches(row: AnalyticsRow) -> bool:
        return row.primaryRegime == regime

    return matches


def _agreed_in(strategy: str, regime: str) -> Callable[[AnalyticsRow], bool]:
    """Outcomes in one regime where one strategy agreed with the selection that was taken."""

    def matches(row: AnalyticsRow) -> bool:
        return row.primaryRegime == regime and any(
            vote.strategyId == strategy and stance_of(vote.direction, row.direction) == "AGREED"
            for vote in row.strategyVotes
        )

    return matches


def _finding(
    *,
    kind: Literal["SCORE_BAND", "REGIME", "STRATEGY_REGIME", "SKIP_CONDITION"],
    subject: str,
    detail: str,
    pooled: OutcomeMetrics,
    periods: Sequence[OutcomeMetrics],
    stable: bool,
    reasons: Sequence[Code],
) -> ResearchFinding:
    return ResearchFinding(
        kind=kind,
        subject=subject,
        detail=detail,
        sampleCount=pooled.sampleCount,
        winRate=pooled.winRateExcludingDraws,
        lower95=pooled.lower95,
        upper95=pooled.upper95,
        trainCount=periods[0].sampleCount,
        validationCount=periods[1].sampleCount,
        testCount=periods[2].sampleCount,
        trainWinRate=periods[0].winRateExcludingDraws,
        validationWinRate=periods[1].winRateExcludingDraws,
        testWinRate=periods[2].winRateExcludingDraws,
        stable=stable,
        reasons=list(reasons)[:MAX_WARNINGS],
    )


def research_findings(
    *,
    split: thresholds.TemporalSplit,
    candidates: Sequence[ThresholdCandidate],
    regimes: Sequence[SegmentMetrics],
    strategy_cells: Sequence[MatrixCell],
    settings: AnalyticsSettings,
) -> list[ResearchFinding]:
    """Durable research output, deliberately wired to nothing.

    Phase 12 may one day consume stable score bands, regime filters and strategy-regime
    relationships. Persisting them now means that phase inherits evidence instead of starting
    from an empty table — and persisting them *without* a consumer is the point: there is no
    code path in this application that reads a finding and changes a decision with it.

    ``stable`` means the same thing here as it does for a threshold, and is earned the same way:
    the effect held in every chronological period against that period's own baseline, with
    enough sample in each. A regime that looks excellent pooled and only ever won in the first
    third of the history is recorded as an observation with its reasons named, never as stable.
    """
    findings: list[ResearchFinding] = []
    for candidate in candidates:
        periods = [candidate.train.outcomes, candidate.validation.outcomes, candidate.test.outcomes]
        findings.append(
            _finding(
                kind="SCORE_BAND",
                subject=f"{candidate.metric} >= {candidate.threshold:.2f}",
                detail=(
                    f"{candidate.stability} · direction {candidate.directionalStability} · "
                    f"expectancy {candidate.monetaryStability}"
                ),
                pooled=candidate.train.outcomes,
                periods=periods,
                stable=candidate.stable,
                reasons=candidate.reasons,
            )
        )
    for item in regimes:
        pooled = item.outcomes
        if pooled.sampleCount < settings.minDisplaySample:
            continue
        regime = item.key
        periods, baselines = _periods(split, _in_regime(regime), settings=settings)
        favourable = pooled.lower95 is not None and pooled.lower95 > 0.5
        adverse = pooled.upper95 is not None and pooled.upper95 < 0.5
        stable, reasons = (
            _out_of_sample(pooled, periods, baselines, favourable=favourable, settings=settings)
            if favourable or adverse
            else (False, ["NO_POOLED_EFFECT"])
        )
        findings.append(
            _finding(
                kind="SKIP_CONDITION" if adverse else "REGIME",
                subject=regime,
                detail=(f"wins={pooled.wins} losses={pooled.losses} draws={pooled.draws}"),
                pooled=pooled,
                periods=periods,
                stable=stable,
                reasons=reasons,
            )
        )
    for cell in strategy_cells:
        pooled = cell.outcomes
        if cell.agreed < settings.minDisplaySample:
            continue
        strategy, regime = cell.row, cell.column
        periods, baselines = _periods(split, _agreed_in(strategy, regime), settings=settings)
        favourable = pooled.lower95 is not None and pooled.lower95 > 0.5
        stable, reasons = (
            _out_of_sample(pooled, periods, baselines, favourable=True, settings=settings)
            if favourable
            else (False, ["NO_POOLED_EFFECT"])
        )
        findings.append(
            _finding(
                kind="STRATEGY_REGIME",
                subject=f"{strategy} in {regime}",
                detail=f"agreed n={cell.agreed} of {cell.samples} votes present",
                pooled=pooled,
                periods=periods,
                stable=stable,
                reasons=reasons,
            )
        )
    return findings[:MAX_FINDINGS]
