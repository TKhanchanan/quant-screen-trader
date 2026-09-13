"""Trusted local policy diagnostics and explicit lifecycle. No arbitrary evidence upload."""

from __future__ import annotations

import asyncio
import time
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from quant_engine.configuration import Model
from quant_engine.market_api import MarketEngine, local_only
from quant_engine.paths import AppPaths
from quant_engine.policy.models import (
    AdaptivePolicyDecision,
    PolicyEvent,
    PolicySettings,
    PolicySnapshot,
    WatchdogSnapshot,
)
from quant_engine.policy.service import PolicyBusy, PolicyService

router = APIRouter(prefix="/api/policy")


def now() -> int:
    return int(time.time() * 1000)


def service(request: Request, *, mutation: bool = False) -> PolicyService:
    local_only(request)
    if request.client is not None and request.client.host not in (
        "127.0.0.1",
        "::1",
        "localhost",
        "testclient",
    ):
        raise HTTPException(403, "Local clients only")
    market = cast(MarketEngine, request.app.state.market)
    if market.busy or market.policy.busy:
        raise HTTPException(409 if mutation else 429, "Analytical worker busy")
    return market.policy


class RebuildRequest(Model):
    evidenceCutoffTime: int | None = None
    settings: PolicySettings = PolicySettings()


class ActivateRequest(Model):
    snapshotId: UUID


@router.get("/state")
async def state(request: Request) -> dict[str, object]:
    return service(request).state(now())


@router.get("/active")
async def active(request: Request) -> PolicySnapshot | None:
    return service(request).active_at(now())[0]


@router.get("/history")
async def history(
    request: Request, limit: Annotated[int, Query(ge=1, le=500)] = 100
) -> list[PolicyEvent]:
    return service(request).repository.records("event", PolicyEvent, limit=limit)


@router.get("/decisions")
async def decisions(
    request: Request, limit: Annotated[int, Query(ge=1, le=500)] = 100
) -> list[AdaptivePolicyDecision]:
    return service(request).repository.records("decision", AdaptivePolicyDecision, limit=limit)


@router.get("/watchdog")
async def watchdog(request: Request) -> WatchdogSnapshot | None:
    return service(request).watchdog_at(now())


@router.post("/rebuild")
async def rebuild(body: RebuildRequest, request: Request) -> PolicySnapshot:
    worker = service(request, mutation=True)
    paths = cast(AppPaths, request.app.state.paths)
    at = now()
    try:
        return await asyncio.to_thread(
            worker.rebuild,
            paths.market_data,
            body.evidenceCutoffTime if body.evidenceCutoffTime is not None else at,
            at,
            body.settings,
        )
    except PolicyBusy as error:
        raise HTTPException(409, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.post("/activate-shadow")
async def activate_shadow(body: ActivateRequest, request: Request) -> PolicyEvent:
    try:
        return service(request, mutation=True).activate(body.snapshotId, "SHADOW", now())
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.post("/activate-paper")
async def activate_paper(body: ActivateRequest, request: Request) -> PolicyEvent:
    try:
        return service(request, mutation=True).activate(body.snapshotId, "PAPER_GATED", now())
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.post("/deactivate")
async def deactivate(request: Request) -> PolicyEvent:
    return service(request, mutation=True).deactivate(now())


@router.post("/rollback")
async def rollback(request: Request) -> PolicyEvent:
    try:
        return service(request, mutation=True).rollback(now())
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
