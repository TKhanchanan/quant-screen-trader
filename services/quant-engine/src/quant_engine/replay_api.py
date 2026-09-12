"""Local-only replay and backtest API (Phase 11).

Offline compute, exposed locally. Starting a replay reads a recorded file and writes under the
replay namespace; it opens no browser, touches no platform session, reads no wallet and has no
path to an execution surface. The only mutating verbs here start and cancel an *offline* run,
and cancelling one stops that run and nothing else — not capture, not the live daily session,
not an arm state.

There is deliberately no "apply" of any kind. A threshold a fold reports describes outcomes that
already happened; Phase 11 ships no endpoint, no parameter and no code path that turns one into
behaviour, and ``ReplayEvidence`` is written for a phase that does not exist yet.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from quant_engine.market_api import local_only
from quant_engine.paths import AppPaths
from quant_engine.replay import repository
from quant_engine.replay.models import (
    REPLAY_VERSION,
    SUPPORTED_ANALYTICS_VERSION,
    SUPPORTED_FEATURE_VERSION,
    SUPPORTED_PAPER_VERSION,
    SUPPORTED_RANKING_VERSION,
    SUPPORTED_REGIME_VERSION,
    SUPPORTED_STRATEGY_VERSION,
    WARNING_CODES,
    ReplayManifest,
)
from quant_engine.replay.service import ReplayBusy, ReplayJob, ReplayService

router = APIRouter()

NOT_APPLIED = (
    "Research output. Nothing in this response is applied to live execution, "
    "and Phase 11 provides no way to apply it."
)


def _versions() -> dict[str, object]:
    return {
        "replayVersion": REPLAY_VERSION,
        "featureVersion": SUPPORTED_FEATURE_VERSION,
        "regimeVersion": SUPPORTED_REGIME_VERSION,
        "strategyVersion": SUPPORTED_STRATEGY_VERSION,
        "rankingVersion": SUPPORTED_RANKING_VERSION,
        "paperVersion": SUPPORTED_PAPER_VERSION,
        "analyticsVersion": SUPPORTED_ANALYTICS_VERSION,
    }


def _service(request: Request) -> ReplayService:
    local_only(request)
    service = getattr(request.app.state, "replay", None)
    if service is None:
        raise HTTPException(503, "Replay service unavailable")
    return cast(ReplayService, service)


def _paths(request: Request) -> AppPaths:
    return cast(AppPaths, request.app.state.paths)


def _job_payload(job: ReplayJob) -> dict[str, Any]:
    return {
        "jobId": str(job.jobId),
        "replayRunId": None if job.replayRunId is None else str(job.replayRunId),
        "status": job.status,
        "phase": job.phase,
        "totalEvents": job.totalEvents,
        "processedEvents": job.processedEvents,
        "percent": job.percent,
        "currentMarketTime": job.currentMarketTime,
        "startedAt": job.startedAt,
        "finishedAt": job.finishedAt,
        "error": job.error,
        "platforms": list(job.manifest.platforms),
    }


@router.post("/api/replay/runs", status_code=202)
async def start_replay(manifest: ReplayManifest, request: Request) -> dict[str, Any]:
    """Accept one replay. A second heavy run is refused rather than queued.

    Refused with 409 on purpose: two replays competing for the same cores would cost the live
    capture loop its samples, and there is no result worth that.
    """
    service = _service(request)
    try:
        job = service.start(manifest)
    except ReplayBusy as busy:
        raise HTTPException(409, "A replay is already running") from busy
    return {**_versions(), **_job_payload(job), "researchOnly": True, "notice": NOT_APPLIED}


@router.get("/api/replay/runs")
async def list_replays(
    request: Request, limit: Annotated[int, Query(ge=1, le=50)] = 20
) -> dict[str, Any]:
    service = _service(request)
    stored = await asyncio.to_thread(service.runs, limit)
    return {
        **_versions(),
        "busy": service.busy,
        "jobs": [_job_payload(job) for job in service.jobs()],
        "runs": [run.model_dump(mode="json") for run in stored],
        "researchOnly": True,
        "appliedToLiveExecution": False,
        "notice": NOT_APPLIED,
    }


@router.get("/api/replay/runs/{identifier}")
async def replay_status(identifier: UUID, request: Request) -> dict[str, Any]:
    service = _service(request)
    job = service.job(identifier)
    if job is not None:
        return {**_versions(), **_job_payload(job), "researchOnly": True, "notice": NOT_APPLIED}
    run = await asyncio.to_thread(repository.load_run, _paths(request).market_data, identifier)
    if run is None:
        raise HTTPException(404, "Replay run not found")
    return {
        **_versions(),
        "replayRunId": str(run.replayRunId),
        "status": run.status,
        "run": run.model_dump(mode="json"),
        "researchOnly": True,
        "notice": NOT_APPLIED,
    }


@router.get("/api/replay/runs/{identifier}/summary")
async def replay_summary(identifier: UUID, request: Request) -> dict[str, Any]:
    service = _service(request)
    job = service.job(identifier)
    if job is not None and job.summary is not None:
        summary = job.summary
    else:
        run_id = job.replayRunId if job is not None and job.replayRunId is not None else identifier
        loaded = await asyncio.to_thread(
            repository.load_summary, _paths(request).market_data, run_id
        )
        if loaded is None:
            raise HTTPException(404, "Replay summary not found")
        summary = loaded
    return {
        **_versions(),
        "summary": summary.model_dump(mode="json"),
        "warningCodes": list(WARNING_CODES),
        "researchOnly": True,
        "appliedToLiveExecution": False,
        "notice": NOT_APPLIED,
    }


@router.get("/api/replay/runs/{identifier}/walk-forward")
async def replay_walk_forward(identifier: UUID, request: Request) -> dict[str, Any]:
    service = _service(request)
    job = service.job(identifier)
    if job is not None and job.summary is not None and job.summary.walkForward is not None:
        return {
            **_versions(),
            "walkForward": job.summary.walkForward.model_dump(mode="json"),
            "researchOnly": True,
            "notice": NOT_APPLIED,
        }
    run_id = job.replayRunId if job is not None and job.replayRunId is not None else identifier
    payload = await asyncio.to_thread(
        repository.load_walk_forward, _paths(request).market_data, run_id
    )
    if payload is None:
        raise HTTPException(404, "Walk-forward report not found")
    return {**_versions(), "walkForward": payload, "researchOnly": True, "notice": NOT_APPLIED}


@router.get("/api/replay/runs/{identifier}/equity")
async def replay_equity(
    identifier: UUID, request: Request, limit: Annotated[int, Query(ge=1, le=20_000)] = 2_000
) -> dict[str, Any]:
    """The simulated paper equity path. Never a broker balance, and labelled as such."""
    service = _service(request)
    job = service.job(identifier)
    run_id = job.replayRunId if job is not None and job.replayRunId is not None else identifier
    points = await asyncio.to_thread(repository.load_equity, _paths(request).market_data, run_id)
    return {
        **_versions(),
        "label": "SIMULATED PAPER EQUITY",
        "points": points[:limit],
        "truncated": len(points) > limit,
        "researchOnly": True,
        "notice": NOT_APPLIED,
    }


@router.post("/api/replay/runs/{identifier}/cancel")
async def cancel_replay(identifier: UUID, request: Request) -> dict[str, Any]:
    """Stop the offline replay. Capture, the live daily session and execution are untouched."""
    service = _service(request)
    cancelled = service.cancel(identifier)
    if not cancelled:
        raise HTTPException(404, "No running replay with that identifier")
    job = service.job(identifier)
    return {
        **_versions(),
        "cancelled": True,
        "scope": "REPLAY_ONLY",
        **({} if job is None else _job_payload(job)),
        "notice": NOT_APPLIED,
    }
