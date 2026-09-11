"""FastAPI application and health heartbeat contract."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, cast

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from quant_engine import __version__
from quant_engine.configuration_api import router as configuration_router
from quant_engine.feature_api import router as feature_router
from quant_engine.market_api import MarketEngine
from quant_engine.market_api import router as market_router
from quant_engine.market_storage import ParquetStorage
from quant_engine.opportunity_api import router as opportunity_router
from quant_engine.paper_api import router as paper_router
from quant_engine.paths import AppPaths, ensure_app_paths
from quant_engine.storage.database import database_is_healthy, initialize_database
from quant_engine.strategy_api import router as strategy_router

DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 1.0


class ServiceStatus(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"


class DatabaseStatus(StrEnum):
    OK = "ok"
    ERROR = "error"


class HealthMessage(BaseModel):
    """Stable message shared by HTTP readiness and WebSocket heartbeats."""

    type: Literal["health"] = "health"
    service: Literal["quant-engine"] = "quant-engine"
    version: str = __version__
    status: ServiceStatus
    database: DatabaseStatus
    timestamp: datetime
    sequence: int = Field(ge=0)


def _paths(application: FastAPI) -> AppPaths:
    return cast(AppPaths, application.state.paths)


async def _health_message(database_path: Path, sequence: int) -> HealthMessage:
    database_ok = await asyncio.to_thread(database_is_healthy, database_path)
    return HealthMessage(
        status=ServiceStatus.OK if database_ok else ServiceStatus.DEGRADED,
        database=DatabaseStatus.OK if database_ok else DatabaseStatus.ERROR,
        timestamp=datetime.now(UTC),
        sequence=sequence,
    )


def create_app(
    *,
    data_dir: Path | None = None,
    heartbeat_interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
) -> FastAPI:
    if heartbeat_interval_seconds <= 0:
        raise ValueError("heartbeat interval must be greater than zero")

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        paths = ensure_app_paths(data_dir)
        await asyncio.to_thread(initialize_database, paths.database_file)
        application.state.paths = paths
        market = MarketEngine(ParquetStorage(paths.market_data))
        # Phase 9 never silently forgets a pending or open paper trade across a restart.
        await asyncio.to_thread(market.restore_paper)
        application.state.market = market

        stopping = asyncio.Event()

        async def maintain_market() -> None:
            while not stopping.is_set():
                try:
                    await asyncio.wait_for(stopping.wait(), timeout=1)
                    return
                except TimeoutError:
                    pass
                if not market.busy:
                    market.busy = True
                    try:
                        await asyncio.to_thread(market.advance_live, int(time.time() * 1000))
                    except Exception:
                        market.storage_error = True
                    finally:
                        market.busy = False

        task = asyncio.create_task(maintain_market())
        try:
            yield
        finally:
            stopping.set()
            await task
            await asyncio.to_thread(market.storage.flush)

    application = FastAPI(
        title="QuantScreen Trader Quant Engine",
        version=__version__,
        lifespan=lifespan,
    )
    application.include_router(configuration_router)
    application.include_router(market_router)
    application.include_router(feature_router)
    application.include_router(strategy_router)
    application.include_router(opportunity_router)
    application.include_router(paper_router)

    @application.get("/health", response_model=HealthMessage)
    async def health(request: Request) -> HealthMessage:
        return await _health_message(_paths(request.app).database_file, sequence=0)

    @application.websocket("/ws/health")
    async def health_websocket(websocket: WebSocket) -> None:
        await websocket.accept()
        sequence = 0
        try:
            while True:
                message = await _health_message(
                    _paths(websocket.app).database_file,
                    sequence=sequence,
                )
                await websocket.send_json(message.model_dump(mode="json"))
                sequence += 1
                try:
                    event = await asyncio.wait_for(
                        websocket.receive(),
                        timeout=heartbeat_interval_seconds,
                    )
                    if event["type"] == "websocket.disconnect":
                        return
                except TimeoutError:
                    pass
        except WebSocketDisconnect:
            return

    return application
