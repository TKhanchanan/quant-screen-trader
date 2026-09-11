"""Local-only read API over the paper simulation layer.

Read-only by construction. Nothing here creates, modifies, cancels or settles a paper trade,
and there is deliberately no endpoint that writes a simulated stake or payout rate: those come
from the process environment, the project's existing trusted configuration channel, so no
browser-reachable surface can change what a running simulation is measuring.

A paper WIN says the market moved the way the analysis said it would. It is not an execution
ticket, it did not press anything, and it is never the same statement as a CONFIRMED order.
"""

from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, Query, Request

from quant_engine.configuration import Platform
from quant_engine.market_api import MarketEngine, local_only
from quant_engine.paper import (
    MAX_ENTRY_DELAY_MS,
    MAX_OPEN_PER_PLATFORM,
    MAX_RESOLUTION_LAG_MS,
    PAPER_DURATION_MS,
    PAPER_VERSION,
    SUPPORTED_FEATURE_VERSION,
    SUPPORTED_RANKING_VERSION,
    SUPPORTED_REGIME_VERSION,
    SUPPORTED_STRATEGY_VERSION,
)

router = APIRouter()

HISTORY_LIMIT = 500
DEFAULT_HISTORY = 100
"""Bounded on purpose. An unbounded history request would walk the whole in-memory tail on the
ingestion thread's data; longer history belongs to the Parquet store."""


def _engine(request: Request) -> MarketEngine:
    """Paper state is read straight off the live engine, so it may only be read while no
    ingestion thread is mutating it. 429 lets the caller retry, exactly as the market, feature,
    strategy and ranking readers do, rather than walking a half-updated lifecycle."""
    local_only(request)
    engine = cast(MarketEngine, request.app.state.market)
    if engine.busy:
        raise HTTPException(429, "Engine busy")
    return engine


def _versions() -> dict[str, object]:
    return {
        "featureVersion": SUPPORTED_FEATURE_VERSION,
        "regimeVersion": SUPPORTED_REGIME_VERSION,
        "strategyVersion": SUPPORTED_STRATEGY_VERSION,
        "rankingVersion": SUPPORTED_RANKING_VERSION,
        "paperVersion": PAPER_VERSION,
    }


@router.get("/api/paper/state")
async def paper_state(request: Request) -> dict[str, object]:
    engine = _engine(request)
    return {**_versions(), **engine.paper.state().model_dump(mode="json")}


@router.get("/api/paper/open")
async def paper_open(request: Request) -> dict[str, object]:
    """Every pending and open paper trade. An empty list is the honest answer whenever Phase 8
    has named no selection, which is most of the time."""
    engine = _engine(request)
    return {
        **_versions(),
        "trades": [trade.model_dump(mode="json") for trade in engine.paper.open_trades()],
    }


@router.get("/api/paper/history")
async def paper_history(
    request: Request,
    platform: Platform | None = None,
    limit: Annotated[int, Query(ge=1, le=HISTORY_LIMIT)] = DEFAULT_HISTORY,
) -> dict[str, object]:
    engine = _engine(request)
    return {
        **_versions(),
        "trades": [trade.model_dump(mode="json") for trade in engine.paper.recent(platform, limit)],
    }


@router.get("/api/paper/stats")
async def paper_stats(request: Request, platform: Platform | None = None) -> dict[str, object]:
    """Descriptive statistics over what was recorded. Phase 9 measures; it calibrates nothing,
    and no Phase 6-8 threshold may be moved on the strength of these numbers."""
    engine = _engine(request)
    return {**_versions(), "stats": engine.paper.stats(platform).model_dump(mode="json")}


@router.get("/api/paper/settlements")
async def paper_settlements(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=HISTORY_LIMIT)] = DEFAULT_HISTORY,
) -> dict[str, object]:
    """The Phase 9.5 hand-off, newest first. One settlement per resolved trade, ever."""
    engine = _engine(request)
    return {
        **_versions(),
        "settlements": [
            settlement.model_dump(mode="json")
            for settlement in engine.paper.recent_settlements(limit)
        ],
    }


@router.get("/api/paper/settings")
async def paper_settings(request: Request) -> dict[str, object]:
    """What the simulation is running under. The horizons and bounds are part of the version
    contract rather than operator preferences, and are reported so a stored outcome can be read
    against the policy that produced it."""
    engine = _engine(request)
    settings = engine.paper.settings
    return {
        **_versions(),
        "settings": settings.model_dump(mode="json"),
        "accountingConfigured": settings.accountingConfigured,
        "settingsError": engine.paper.settingsError,
        "durationMs": dict(PAPER_DURATION_MS),
        "maxEntryDelayMs": dict(MAX_ENTRY_DELAY_MS),
        "maxResolutionLagMs": dict(MAX_RESOLUTION_LAG_MS),
        "maxOpenPerPlatform": MAX_OPEN_PER_PLATFORM,
    }


@router.get("/api/paper/trades/{paper_trade_id}")
async def paper_trade(
    paper_trade_id: Annotated[UUID, Path()], request: Request
) -> dict[str, object]:
    engine = _engine(request)
    trade = engine.paper.find(paper_trade_id)
    if trade is None:
        raise HTTPException(404, "No paper trade with this id")
    return {**_versions(), "trade": trade.model_dump(mode="json")}
