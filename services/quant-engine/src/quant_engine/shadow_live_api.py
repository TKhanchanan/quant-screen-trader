"""Local operational evidence endpoints. No policy or execution mutation."""

import asyncio
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from quant_engine.configuration import Model, Platform
from quant_engine.market_api import MarketEngine, local_only
from quant_engine.shadow_live import ShadowLiveRecorder

router = APIRouter()


class CaptureSlot(Model):
    slotId: int = Field(ge=1, le=9)
    enabled: bool
    assetName: str = Field(max_length=120)
    contextId: UUID
    state: str = Field(max_length=24)
    observations: int = Field(ge=0)
    lastCaptureAttemptAt: int | None = Field(default=None, ge=0)
    dataUncertain: int = Field(ge=0)
    dropped: int = Field(ge=0)
    captureEligible: bool = False
    lastAttemptAt: int | None = Field(default=None, ge=0)
    lastParsedPriceAt: int | None = Field(default=None, ge=0)
    lastGoodPriceAt: int | None = Field(default=None, ge=0)
    attemptCount: int = Field(default=0, ge=0)
    parsedCount: int = Field(default=0, ge=0)
    goodCount: int = Field(default=0, ge=0)
    uncertainCount: int = Field(default=0, ge=0)
    secondSamples: int = Field(default=0, ge=0)
    s5Samples: int = Field(default=0, ge=0)
    m1Samples: int = Field(default=0, ge=0)


class ExecutionTicketSummary(Model):
    """One would-press (PAPER) or refused (BLOCKED) execution ticket. Never a real press."""

    id: str = Field(min_length=1, max_length=64)
    boardAsOf: int
    slotId: int = Field(ge=1, le=9)
    assetName: str = Field(max_length=120)
    direction: Literal["HIGHER", "LOWER"]
    state: Literal["PAPER", "BLOCKED"]
    reasons: list[Annotated[str, Field(max_length=64)]] = Field(max_length=12)
    requestedAt: str = Field(max_length=40)


class DesktopTelemetry(Model):
    platform: Platform
    instanceId: UUID
    healthRevision: int = Field(ge=0)
    captureRunning: bool
    surfaceAvailable: bool
    engineAvailable: bool
    captureRate: float = Field(default=0, ge=0)
    intervalMs: int = Field(ge=1, le=60000)
    queueDepth: int = Field(ge=0, le=1000)
    droppedBatches: int = Field(ge=0)
    http429s: int = Field(ge=0)
    armed: bool
    """A LIVE (AUTO) executor is armed: the only armed state that can press a broker control."""
    brokerPresses: int = Field(ge=0)
    executionMode: Literal["OFF", "PAPER", "AUTO"] = "OFF"
    paperArmed: bool = False
    boardsEvaluated: int = Field(default=0, ge=0)
    paperTickets: int = Field(default=0, ge=0)
    blockedTickets: int = Field(default=0, ge=0)
    recentTickets: list[ExecutionTicketSummary] = Field(default_factory=list, max_length=5)
    mainLoopDelayMs: float = Field(ge=0, allow_inf_nan=False)
    autoSyncEnabled: bool = False
    autoSyncRuns: int = Field(default=0, ge=0)
    autoSyncAppliedChanges: int = Field(default=0, ge=0)
    mainRssBytes: int = Field(default=0, ge=0)
    slots: list[CaptureSlot] = Field(max_length=9)


def recorder(request: Request) -> ShadowLiveRecorder:
    local_only(request)
    engine = cast(MarketEngine, request.app.state.market)
    if engine.shadow is None:
        raise HTTPException(409, "Start the application with QST_SHADOW_LIVE=1")
    return engine.shadow


@router.get("/api/shadow-live/state")
async def state(request: Request) -> dict[str, Any]:
    return recorder(request).report()


@router.post("/api/shadow-live/telemetry")
async def telemetry(value: DesktopTelemetry, request: Request) -> dict[str, bool]:
    recorder(request).telemetry(value.model_dump(mode="json"))
    return {"recorded": True}


@router.post("/api/shadow-live/checkpoint")
async def checkpoint(request: Request) -> dict[str, Any]:
    value = recorder(request)
    await asyncio.to_thread(value.flush)
    return value.report()


class RestartVerification(Model):
    """Explicit operator observations; never inferred from elapsed time or a new PID."""

    browserSessionPersisted: bool
    configurationPersisted: bool
    calibrationPersisted: bool
    assetPresetsPersisted: bool
    paperRestoredSafely: bool
    sessionGuardRestoredWithoutUnlock: bool
    policyJournalRestored: bool
    noOrphanEngine: bool
    liveExecutionNeverArmed: bool
    """AUTO was never armed in either workspace. PAPER may have been armed."""
    noBrokerPresses: bool


@router.post("/api/shadow-live/verify-restart")
async def verify_restart(value: RestartVerification, request: Request) -> dict[str, Any]:
    shadow = recorder(request)
    with shadow.lock:
        shadow.data["restartVerification"] = {
            "source": "OPERATOR_OBSERVATION",
            **value.model_dump(),
        }
        shadow.data["restartVerified"] = shadow.data["engineRestarts"] == 1 and all(
            value.model_dump().values()
        )
        shadow.data["executionVerified"] = (
            value.liveExecutionNeverArmed
            and value.noBrokerPresses
            and len(shadow.data["desktop"]) == 2
            and shadow.data["executionArmed"] is False
            and shadow.data["unexpectedBrokerPresses"] == 0
        )
    await asyncio.to_thread(shadow.flush)
    return shadow.report()


@router.post("/api/shadow-live/verify-storage")
async def verify_storage_endpoint(request: Request) -> dict[str, Any]:
    from quant_engine.shadow_live_verify import verify_storage

    shadow = recorder(request)
    engine = cast(MarketEngine, request.app.state.market)
    if engine.busy:
        raise HTTPException(429, "Stop capture and retry when the engine is idle")
    # Explicit audit holds ingestion's existing busy gate; it cannot build a live backlog.
    engine.busy = True
    try:
        await asyncio.to_thread(engine.storage.flush)
        await asyncio.to_thread(verify_storage, shadow, request.app.state.paths.root)
        await asyncio.to_thread(shadow.flush)
        return shadow.report()
    finally:
        engine.busy = False


class AutoSyncVerification(Model):
    platform: Platform
    reviewedAppliedChanges: int = Field(ge=0)
    unexpectedAutoSyncChanges: int = Field(ge=0)


@router.post("/api/shadow-live/verify-auto-sync")
async def verify_auto_sync(value: AutoSyncVerification, request: Request) -> dict[str, Any]:
    shadow = recorder(request)
    with shadow.lock:
        platform = shadow.data["platforms"][value.platform]
        if value.reviewedAppliedChanges != platform.get("autoSyncAppliedChanges", 0):
            raise HTTPException(409, "Review the current applied change count")
        platform["autoSyncVerification"] = {"source": "OPERATOR_OBSERVATION", **value.model_dump()}
        platform["unexpectedAutoSyncChanges"] = value.unexpectedAutoSyncChanges
    await asyncio.to_thread(shadow.flush)
    return shadow.report()
