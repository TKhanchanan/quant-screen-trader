"""Local-only read API over the outcome analytics layer.

Read-only by construction, and read-only in a stronger sense than the other diagnostics
surfaces: there is no endpoint here that could change anything even in principle. Nothing
writes a threshold, a score gate, a session limit or an execution setting, and there is
deliberately no "apply" of any kind — a candidate threshold this API reports is a research
observation about recorded history, and Phase 10 ships no mechanism to turn one into behaviour.

Every response carries its own sample size, because a win rate without one is not a measurement.
Every response also carries the analytics version, so two numbers produced under two definitions
can never be compared by accident.
"""

from __future__ import annotations

import asyncio
import csv
import io
from typing import Annotated, Any, cast

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from quant_engine.analytics import (
    ANALYTICS_VERSION,
    EXPORT_COLUMNS,
    MIN_ASSET_SAMPLE,
    MIN_DISPLAY_SAMPLE,
    MIN_RECOMMENDATION_SAMPLE,
    SUPPORTED_FEATURE_VERSION,
    SUPPORTED_PAPER_VERSION,
    SUPPORTED_RANKING_VERSION,
    SUPPORTED_REGIME_VERSION,
    SUPPORTED_STRATEGY_VERSION,
    WARNING_CODES,
    AnalyticsFilters,
    AnalyticsService,
    AnalyticsSnapshot,
    export_mapping,
)
from quant_engine.configuration import Platform
from quant_engine.market_api import MarketEngine, local_only
from quant_engine.paper.models import PaperDirection
from quant_engine.strategy.models import Regime

router = APIRouter()

EXPORT_LIMIT = 20_000
DEFAULT_EXPORT = 2_000

NOT_APPLIED = (
    "Research output. Nothing in this response is applied to live execution, "
    "and Phase 10 provides no way to apply it."
)


def _engine(request: Request) -> MarketEngine:
    local_only(request)
    engine = cast(MarketEngine, request.app.state.market)
    if engine.busy:
        raise HTTPException(429, "Engine busy")
    if engine.analytics.rebuilding:
        # Half of a replaced dataset is not a smaller answer, it is a wrong one.
        raise HTTPException(429, "Analytics rebuilding")
    return engine


def _versions() -> dict[str, object]:
    return {
        "analyticsVersion": ANALYTICS_VERSION,
        "featureVersion": SUPPORTED_FEATURE_VERSION,
        "regimeVersion": SUPPORTED_REGIME_VERSION,
        "strategyVersion": SUPPORTED_STRATEGY_VERSION,
        "rankingVersion": SUPPORTED_RANKING_VERSION,
        "paperVersion": SUPPORTED_PAPER_VERSION,
    }


def _filters(
    platform: Platform | None,
    asset_name: str | None,
    regime: Regime | None,
    direction: PaperDirection | None,
    from_time: int | None,
    to_time: int | None,
) -> AnalyticsFilters:
    return AnalyticsFilters(
        platform=platform,
        assetName=asset_name,
        regime=regime,
        direction=direction,
        fromTime=from_time,
        toTime=to_time,
    )


async def _analysis(
    request: Request, filters: AnalyticsFilters, refresh: bool
) -> tuple[AnalyticsService, AnalyticsSnapshot]:
    """The current analysis, rebuilt from the durable record when it is missing or asked for.

    The rebuild runs off the request thread and sets the busy flag while it does, so a second
    caller is told to retry rather than handed a partially replaced dataset.
    """
    engine = _engine(request)
    service = engine.analytics
    if refresh or not service.loaded:
        await asyncio.to_thread(service.refresh)
    snapshot = service.view(filters)
    if snapshot is None:
        raise HTTPException(503, "Analytics history unavailable")
    return (service, snapshot)


def _envelope(service: AnalyticsService, snapshot: AnalyticsSnapshot) -> dict[str, Any]:
    return {
        **_versions(),
        "snapshotId": str(snapshot.snapshotId),
        "datasetFingerprint": snapshot.datasetFingerprint,
        "sampleCount": snapshot.totalResolved,
        "sampleStart": snapshot.sampleStart,
        "sampleEnd": snapshot.sampleEnd,
        "sampleLabel": snapshot.overallMetrics.sampleLabel,
        "timezone": snapshot.timezone,
        "warnings": snapshot.warnings,
        "loadError": service.loadError,
        "researchOnly": True,
        "appliedToLiveExecution": False,
    }


@router.get("/api/analytics/summary")
async def analytics_summary(
    request: Request,
    platform: Platform | None = None,
    assetName: Annotated[str | None, Query(max_length=120)] = None,
    regime: Regime | None = None,
    direction: PaperDirection | None = None,
    from_time: Annotated[int | None, Query(alias="from")] = None,
    to_time: Annotated[int | None, Query(alias="to")] = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """Data quality first, then outcomes, then money. In that order deliberately.

    A win rate over the fifth of decisions that happened to resolve is not a win rate, and the
    resolution rate is the only thing that lets a reader see which one they are looking at.
    """
    filters = _filters(platform, assetName, regime, direction, from_time, to_time)
    service, snapshot = await _analysis(request, filters, refresh)
    return {
        **_envelope(service, snapshot),
        "quality": snapshot.quality.model_dump(mode="json"),
        "overall": snapshot.overallMetrics.model_dump(mode="json"),
        "money": snapshot.overallMoney.model_dump(mode="json"),
        "platforms": [item.model_dump(mode="json") for item in snapshot.platformMetrics],
        "correlations": [item.model_dump(mode="json") for item in snapshot.correlations],
        "temporalSplit": snapshot.temporalSplit.model_dump(mode="json"),
        "thresholds": {
            "minDisplaySample": MIN_DISPLAY_SAMPLE,
            "minRecommendationSample": MIN_RECOMMENDATION_SAMPLE,
            "minAssetSample": MIN_ASSET_SAMPLE,
        },
        "warningCodes": list(WARNING_CODES),
    }


@router.get("/api/analytics/calibration/rank")
async def analytics_rank_calibration(
    request: Request,
    platform: Platform | None = None,
    assetName: Annotated[str | None, Query(max_length=120)] = None,
    regime: Regime | None = None,
    direction: PaperDirection | None = None,
    from_time: Annotated[int | None, Query(alias="from")] = None,
    to_time: Annotated[int | None, Query(alias="to")] = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """The empirical outcome curve for Phase 8's ``rankScore``. Not a probability calibration."""
    filters = _filters(platform, assetName, regime, direction, from_time, to_time)
    service, snapshot = await _analysis(request, filters, refresh)
    return {
        **_envelope(service, snapshot),
        "calibration": snapshot.rankCalibration.model_dump(mode="json"),
    }


@router.get("/api/analytics/calibration/confidence")
async def analytics_confidence_calibration(
    request: Request,
    platform: Platform | None = None,
    assetName: Annotated[str | None, Query(max_length=120)] = None,
    regime: Regime | None = None,
    direction: PaperDirection | None = None,
    from_time: Annotated[int | None, Query(alias="from")] = None,
    to_time: Annotated[int | None, Query(alias="to")] = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """The same curve for Phase 7's ``ensembleConfidence``, including when it is inverted."""
    filters = _filters(platform, assetName, regime, direction, from_time, to_time)
    service, snapshot = await _analysis(request, filters, refresh)
    return {
        **_envelope(service, snapshot),
        "calibration": snapshot.confidenceCalibration.model_dump(mode="json"),
        "agreementBins": [item.model_dump(mode="json") for item in snapshot.agreementBins],
        "leadMarginBins": [item.model_dump(mode="json") for item in snapshot.leadMarginBins],
        "rankConfidenceMatrix": snapshot.rankConfidenceMatrix.model_dump(mode="json"),
    }


@router.get("/api/analytics/regimes")
async def analytics_regimes(
    request: Request,
    platform: Platform | None = None,
    assetName: Annotated[str | None, Query(max_length=120)] = None,
    regime: Regime | None = None,
    direction: PaperDirection | None = None,
    from_time: Annotated[int | None, Query(alias="from")] = None,
    to_time: Annotated[int | None, Query(alias="to")] = None,
    refresh: bool = False,
) -> dict[str, Any]:
    filters = _filters(platform, assetName, regime, direction, from_time, to_time)
    service, snapshot = await _analysis(request, filters, refresh)
    return {
        **_envelope(service, snapshot),
        "regimes": [item.model_dump(mode="json") for item in snapshot.regimeMetrics],
        "regimeDirection": snapshot.regimeDirectionMatrix.model_dump(mode="json"),
        "regimeConfidenceBins": [
            item.model_dump(mode="json") for item in snapshot.regimeConfidenceBins
        ],
    }


@router.get("/api/analytics/strategies")
async def analytics_strategies(
    request: Request,
    platform: Platform | None = None,
    assetName: Annotated[str | None, Query(max_length=120)] = None,
    regime: Regime | None = None,
    direction: PaperDirection | None = None,
    from_time: Annotated[int | None, Query(alias="from")] = None,
    to_time: Annotated[int | None, Query(alias="to")] = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """What each Phase 7 strategy's vote was worth, read exactly as Phase 7 recorded it."""
    filters = _filters(platform, assetName, regime, direction, from_time, to_time)
    service, snapshot = await _analysis(request, filters, refresh)
    return {
        **_envelope(service, snapshot),
        "strategies": [item.model_dump(mode="json") for item in snapshot.strategyMetrics],
        "strategyRegime": snapshot.strategyRegimeMatrix.model_dump(mode="json"),
        "joinedTrades": snapshot.quality.strategyJoinedTrades,
    }


@router.get("/api/analytics/assets")
async def analytics_assets(
    request: Request,
    platform: Platform | None = None,
    assetName: Annotated[str | None, Query(max_length=120)] = None,
    regime: Regime | None = None,
    direction: PaperDirection | None = None,
    from_time: Annotated[int | None, Query(alias="from")] = None,
    to_time: Annotated[int | None, Query(alias="to")] = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """Per platform and asset. ``rankable`` marks the ones with enough sample to be ordered."""
    filters = _filters(platform, assetName, regime, direction, from_time, to_time)
    service, snapshot = await _analysis(request, filters, refresh)
    return {
        **_envelope(service, snapshot),
        "assets": [item.model_dump(mode="json") for item in snapshot.assetMetrics],
        "minAssetSample": MIN_ASSET_SAMPLE,
        "quality": [item.model_dump(mode="json") for item in snapshot.qualityMetrics],
    }


@router.get("/api/analytics/time")
async def analytics_time(
    request: Request,
    platform: Platform | None = None,
    assetName: Annotated[str | None, Query(max_length=120)] = None,
    regime: Regime | None = None,
    direction: PaperDirection | None = None,
    from_time: Annotated[int | None, Query(alias="from")] = None,
    to_time: Annotated[int | None, Query(alias="to")] = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """Hour and weekday in the configured timezone, which is named in the response."""
    filters = _filters(platform, assetName, regime, direction, from_time, to_time)
    service, snapshot = await _analysis(request, filters, refresh)
    return {
        **_envelope(service, snapshot),
        "hours": [item.model_dump(mode="json") for item in snapshot.hourMetrics],
        "weekdays": [item.model_dump(mode="json") for item in snapshot.weekdayMetrics],
    }


@router.get("/api/analytics/thresholds")
async def analytics_thresholds(
    request: Request,
    platform: Platform | None = None,
    assetName: Annotated[str | None, Query(max_length=120)] = None,
    regime: Regime | None = None,
    direction: PaperDirection | None = None,
    from_time: Annotated[int | None, Query(alias="from")] = None,
    to_time: Annotated[int | None, Query(alias="to")] = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """Candidate thresholds discovered on TRAIN and evaluated out of sample.

    Research output. There is no companion endpoint that applies one, because there is no code
    in this application that would read it if there were.
    """
    filters = _filters(platform, assetName, regime, direction, from_time, to_time)
    service, snapshot = await _analysis(request, filters, refresh)
    return {
        **_envelope(service, snapshot),
        "candidates": [item.model_dump(mode="json") for item in snapshot.thresholdCandidates],
        "temporalSplit": snapshot.temporalSplit.model_dump(mode="json"),
        "comparisonsEvaluated": snapshot.comparisonsEvaluated,
        "research": [item.model_dump(mode="json") for item in snapshot.research],
        "notice": NOT_APPLIED,
    }


@router.get("/api/analytics/snapshot")
async def analytics_snapshot(
    request: Request,
    platform: Platform | None = None,
    assetName: Annotated[str | None, Query(max_length=120)] = None,
    regime: Regime | None = None,
    direction: PaperDirection | None = None,
    from_time: Annotated[int | None, Query(alias="from")] = None,
    to_time: Annotated[int | None, Query(alias="to")] = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """The whole analysis in one document, with its deterministic identity."""
    filters = _filters(platform, assetName, regime, direction, from_time, to_time)
    service, snapshot = await _analysis(request, filters, refresh)
    return {
        **_envelope(service, snapshot),
        "notice": NOT_APPLIED,
        "snapshot": snapshot.model_dump(mode="json"),
    }


@router.get("/api/analytics/export")
async def analytics_export(
    request: Request,
    platform: Platform | None = None,
    assetName: Annotated[str | None, Query(max_length=120)] = None,
    regime: Regime | None = None,
    direction: PaperDirection | None = None,
    from_time: Annotated[int | None, Query(alias="from")] = None,
    to_time: Annotated[int | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=EXPORT_LIMIT)] = DEFAULT_EXPORT,
    export_format: Annotated[str, Query(alias="format", pattern="^(json|csv)$")] = "json",
    refresh: bool = False,
) -> Any:
    """A local export of the analysed rows.

    Market measurements only. The column list is an explicit allow-list, so no session, cookie,
    credential, window or calibration identity can leave through this endpoint even if one were
    added to the row later.
    """
    filters = _filters(platform, assetName, regime, direction, from_time, to_time)
    service, _ = await _analysis(request, filters, refresh)
    dataset = service.rows_for(filters)
    if dataset is None:
        raise HTTPException(503, "Analytics history unavailable")
    rows = [export_mapping(row) for row in dataset.rows[-limit:]]
    if export_format == "csv":
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=list(EXPORT_COLUMNS), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        return PlainTextResponse(buffer.getvalue(), media_type="text/csv")
    return {
        **_versions(),
        "sampleCount": len(dataset.rows),
        "returned": len(rows),
        "columns": list(EXPORT_COLUMNS),
        "rows": rows,
    }
