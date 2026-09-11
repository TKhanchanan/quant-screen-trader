"""Local-only API over the daily session guard.

Reads are read-only. The two writes — changing the operator's own limits, and stopping the
session — sit behind the same local-user trust boundary as the workspace configuration
endpoint: a browser origin is refused outright, and there is no remotely reachable way to lift
a limit or restart a locked day.

Nothing here can start anything. The guard publishes one permission and the surrounding
application may treat it as a veto; no endpoint in this file asks for an entry, names a
direction, or touches a broker.
"""

from __future__ import annotations

import asyncio
import time
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, Query, Request

from quant_engine.configuration import Model
from quant_engine.market_api import MarketEngine, local_only
from quant_engine.paths import AppPaths
from quant_engine.session_guard import (
    MAX_HISTORY,
    REJECTION_CODES,
    SESSION_GUARD_VERSION,
    SUPPORTED_PAPER_VERSION,
    SessionGuardSettings,
    summarize,
)
from quant_engine.storage.session_guard_repository import save_settings

router = APIRouter()

DEFAULT_HISTORY = 30
EVENT_LIMIT = 500
DEFAULT_EVENTS = 50
STOP_WAIT_SECONDS = 2.0
"""How long a stop will wait for the ingestion thread before going ahead regardless.

A stop is the control an operator reaches for when something is wrong, so it is not allowed to
fail because the engine happened to be mid-batch. It waits for a clean moment, and then takes
the one it has."""


class SettingsCommand(SessionGuardSettings):
    operation: Literal["settings"]


class StopCommand(Model):
    operation: Literal["stop"]


type GuardCommand = SettingsCommand | StopCommand


def _engine(request: Request) -> MarketEngine:
    """Session state is read straight off the live engine, so it may only be read while no
    ingestion thread is mutating it. 429 lets the caller retry, exactly as every other reader
    in this engine does, rather than reporting a half-applied settlement as a daily total."""
    local_only(request)
    engine = cast(MarketEngine, request.app.state.market)
    if engine.busy:
        raise HTTPException(429, "Engine busy")
    return engine


def _now() -> int:
    """Wall clock, for the one thing that genuinely needs it: which trading day it is now.

    No accounting decision is taken from this. Which session a settlement belongs to comes from
    its own ``settledAt``, so a replay of the same outcomes lands in the same days whatever the
    clock says while it runs."""
    return int(time.time() * 1000)


def _versions() -> dict[str, object]:
    return {
        "sessionGuardVersion": SESSION_GUARD_VERSION,
        "paperVersion": SUPPORTED_PAPER_VERSION,
    }


@router.get("/api/session-guard/state")
async def guard_state(request: Request) -> dict[str, object]:
    engine = _engine(request)
    return {
        **_versions(),
        **engine.guard.state(_now()).model_dump(mode="json"),
        "paperAccountingConfigured": engine.paper.settings.accountingConfigured,
    }


@router.get("/api/session-guard/settings")
async def guard_settings(request: Request) -> dict[str, object]:
    engine = _engine(request)
    return {
        **_versions(),
        "settings": engine.guard.settings.model_dump(mode="json"),
        "settingsError": engine.guard.settingsError,
        "enforcing": engine.guard.settings.enforcing,
        "paperAccountingConfigured": engine.paper.settings.accountingConfigured,
        "paperCurrency": engine.paper.settings.paperCurrency,
        "rejectionCodes": list(REJECTION_CODES),
        "historyLimit": MAX_HISTORY,
    }


@router.get("/api/session-guard/history")
async def guard_history(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=MAX_HISTORY)] = DEFAULT_HISTORY,
) -> dict[str, object]:
    """Finished trading days, newest first. Yesterday is never overwritten by today."""
    engine = _engine(request)
    return {
        **_versions(),
        "sessions": [
            summarize(session).model_dump(mode="json") for session in engine.guard.recent(limit)
        ],
    }


@router.get("/api/session-guard/events")
async def guard_events(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=EVENT_LIMIT)] = DEFAULT_EVENTS,
) -> dict[str, object]:
    engine = _engine(request)
    return {
        **_versions(),
        "events": [event.model_dump(mode="json") for event in engine.guard.events(limit)],
    }


@router.post("/api/session-guard/command")
async def guard_command(command: GuardCommand, request: Request) -> dict[str, object]:
    """Change the operator's own limits, or end the trading day.

    Settings are refused while the engine is mid-batch, because a limit that lands halfway
    through a settlement is a limit nobody can reason about. A stop is not: it waits for a clean
    moment and then proceeds either way, since a risk control an operator cannot reach when the
    engine is busy is not a risk control.
    """
    local_only(request)
    engine = cast(MarketEngine, request.app.state.market)
    paths = cast(AppPaths, request.app.state.paths)
    if isinstance(command, StopCommand):
        deadline = time.monotonic() + STOP_WAIT_SECONDS
        while engine.busy and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        engine.persist_session(
            engine.guard.stop_session(_now(), unresolved=engine.paper.unresolved())
        )
        return {**_versions(), **engine.guard.state(_now()).model_dump(mode="json")}
    if engine.busy:
        raise HTTPException(429, "Engine busy")
    settings = SessionGuardSettings.model_validate(command.model_dump(exclude={"operation"}))
    await asyncio.to_thread(save_settings, paths.database_file, settings)
    engine.guard.settingsError = None
    engine.persist_session(
        engine.guard.update_settings(settings, _now(), unresolved=engine.paper.unresolved())
    )
    return {**_versions(), **engine.guard.state(_now()).model_dump(mode="json")}


@router.get("/api/session-guard/sessions/{session_id}")
async def guard_session(session_id: Annotated[UUID, Path()], request: Request) -> dict[str, object]:
    engine = _engine(request)
    session = engine.guard.find(session_id)
    if session is None:
        raise HTTPException(404, "No session with this id")
    return {
        **_versions(),
        "session": session.model_dump(mode="json"),
        "summary": summarize(session).model_dump(mode="json"),
    }
