"""Local-only read API over the strategy layer.

Read-only by construction. There is no endpoint here that places, sizes, cancels or
schedules anything: the whole surface returns the analysis the engine has already produced.
"""

from typing import Annotated, cast

from fastapi import APIRouter, HTTPException, Path, Query, Request

from quant_engine.configuration import Platform
from quant_engine.market_api import MarketEngine, local_only
from quant_engine.strategy import (
    REGIME_VERSION,
    STRATEGY_VERSION,
    SUPPORTED_FEATURE_VERSION,
)
from quant_engine.strategy.engine import HISTORY_CAPACITY

router = APIRouter()

type SlotPath = Annotated[int, Path(ge=1, le=9)]

HISTORY_LIMIT = HISTORY_CAPACITY
"""Bounded to what the engine actually keeps in memory, so the cap cannot drift away from it.
Longer history belongs to the Parquet store, not to a read endpoint."""


def _engine(request: Request) -> MarketEngine:
    """Strategy state is read straight off the live engine, so it may only be read while no
    ingestion thread is running. 429 lets the caller simply retry, exactly as the market and
    feature readers do."""
    local_only(request)
    engine = cast(MarketEngine, request.app.state.market)
    if engine.busy:
        raise HTTPException(429, "Engine busy")
    return engine


def _versions() -> dict[str, str]:
    return {
        "featureVersion": SUPPORTED_FEATURE_VERSION,
        "regimeVersion": REGIME_VERSION,
        "strategyVersion": STRATEGY_VERSION,
    }


@router.get("/api/strategy/state")
async def strategy_state(request: Request) -> dict[str, object]:
    engine = _engine(request)
    return {
        **_versions(),
        "evaluated": engine.strategy.evaluated,
        "duplicates": engine.strategy.duplicates,
        "slots": [state.model_dump(mode="json") for state in engine.strategy.diagnostics()],
    }


@router.get("/api/strategy/{platform}/{slot_id}")
async def slot_strategy(
    platform: Platform, slot_id: SlotPath, request: Request
) -> dict[str, object]:
    engine = _engine(request)
    ensemble = engine.strategy.latest(platform, slot_id)
    if ensemble is None:
        raise HTTPException(404, "No strategy state for this slot")
    return {
        **_versions(),
        "regime": ensemble.regime.model_dump(mode="json"),
        "strategies": [item.model_dump(mode="json") for item in ensemble.strategies],
        "ensemble": ensemble.model_dump(mode="json"),
    }


@router.get("/api/strategy/{platform}/{slot_id}/history")
async def slot_strategy_history(
    platform: Platform,
    slot_id: SlotPath,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=HISTORY_LIMIT)] = 8,
) -> dict[str, object]:
    engine = _engine(request)
    return {
        **_versions(),
        "ensembles": [
            snapshot.model_dump(mode="json")
            for snapshot in engine.strategy.recent(platform, slot_id, limit)
        ],
    }
