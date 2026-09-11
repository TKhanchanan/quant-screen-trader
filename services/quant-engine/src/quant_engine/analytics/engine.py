"""Orchestration: one dataset in, one snapshot out.

The snapshot is a pure function of the rows, the settings and the filters, so the same recorded
history analysed twice produces the same identifier and the same numbers — which is what makes
a snapshot something two people can argue about rather than something that moved between them.

This module composes; it decides nothing. Every metric it assembles is computed elsewhere, and
the only judgement it adds is which diagnostics a reader must be shown whether they asked for
them or not.
"""

from __future__ import annotations

from collections.abc import Sequence
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
    AnalyticsDataset,
    AnalyticsFilters,
    AnalyticsRow,
    AnalyticsSettings,
    AnalyticsSnapshot,
    CalibrationReport,
    Code,
    MatrixCell,
    ResearchFinding,
    SegmentMetrics,
    ThresholdCandidate,
)

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
            money_available=money.available,
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
    money_available: bool,
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
    if not money_available:
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


def research_findings(
    *,
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

    A finding is only ``stable`` when its Wilson bound clears an even split on a sample large
    enough to recommend from. Everything else is recorded as an observation.
    """
    findings: list[ResearchFinding] = []
    for candidate in candidates:
        findings.append(
            ResearchFinding(
                kind="SCORE_BAND",
                subject=f"{candidate.metric} >= {candidate.threshold:.2f}",
                detail=(
                    f"train n={candidate.train.count} "
                    f"validation n={candidate.validation.count} "
                    f"test n={candidate.test.count} · {candidate.stability}"
                ),
                sampleCount=candidate.train.count,
                winRate=candidate.train.outcomes.winRateExcludingDraws,
                lower95=candidate.train.outcomes.lower95,
                upper95=candidate.train.outcomes.upper95,
                stable=candidate.stable,
            )
        )
    for item in regimes:
        outcomes = item.outcomes
        if outcomes.sampleCount < settings.minDisplaySample:
            continue
        enough = outcomes.sampleCount >= settings.minRecommendationSample
        favourable = enough and outcomes.lower95 is not None and outcomes.lower95 > 0.5
        adverse = enough and outcomes.upper95 is not None and outcomes.upper95 < 0.5
        findings.append(
            ResearchFinding(
                kind="SKIP_CONDITION" if adverse else "REGIME",
                subject=item.key,
                detail=(
                    f"n={outcomes.sampleCount} "
                    f"wins={outcomes.wins} losses={outcomes.losses} draws={outcomes.draws}"
                ),
                sampleCount=outcomes.sampleCount,
                winRate=outcomes.winRateExcludingDraws,
                lower95=outcomes.lower95,
                upper95=outcomes.upper95,
                stable=favourable or adverse,
            )
        )
    for cell in strategy_cells:
        if cell.agreed < settings.minRecommendationSample:
            continue
        bound = cell.outcomes.lower95
        findings.append(
            ResearchFinding(
                kind="STRATEGY_REGIME",
                subject=f"{cell.row} in {cell.column}",
                detail=f"agreed n={cell.agreed} of {cell.samples} votes present",
                sampleCount=cell.agreed,
                winRate=cell.outcomes.winRateExcludingDraws,
                lower95=bound,
                upper95=cell.outcomes.upper95,
                stable=bound is not None and bound > 0.5,
            )
        )
    return findings[:MAX_FINDINGS]
