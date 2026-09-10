"""Local-only read API over the ranking layer.

Read-only by construction. There is no endpoint here that places, sizes, cancels or schedules
anything: the whole surface returns boards the engine has already produced. A "selected"
candidate is the current top analysis, never an instruction to trade it.
"""

from typing import Annotated, cast

from fastapi import APIRouter, HTTPException, Path, Query, Request

from quant_engine.configuration import Platform
from quant_engine.market_api import MarketEngine, local_only
from quant_engine.opportunity import (
    PRIMARY_HORIZON,
    RANKING_VERSION,
    SUPPORTED_FEATURE_VERSION,
    SUPPORTED_REGIME_VERSION,
    SUPPORTED_STRATEGY_VERSION,
)
from quant_engine.opportunity.scoring import MIN_LEAD_MARGIN, MIN_SELECTION_SCORE

router = APIRouter()

type SlotPath = Annotated[int, Path(ge=1, le=9)]

HISTORY_LIMIT = 100
"""The bound the contract specifies. The engine keeps fewer boards than this in memory, so a
larger request simply returns everything it has; longer history belongs to the Parquet store."""


def _engine(request: Request) -> MarketEngine:
    """Ranking state is read straight off the live engine, so it may only be read while no
    ingestion thread is mutating it. 429 lets the caller retry, exactly as the market, feature
    and strategy readers do, rather than walking a half-updated cohort."""
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
        "rankingVersion": RANKING_VERSION,
        "minSelectionScore": MIN_SELECTION_SCORE,
        "minLeadMargin": MIN_LEAD_MARGIN,
    }


@router.get("/api/opportunities/state")
async def opportunity_state(request: Request) -> dict[str, object]:
    """Both platform boards, kept separate. There is deliberately no combined ranking: a
    five-second CapitalBear decision and a one-minute IQ Option decision do not share a
    horizon, and one 1-18 list would pretend they did."""
    engine = _engine(request)
    return {
        **_versions(),
        "ingested": engine.opportunities.ingested,
        "duplicates": engine.opportunities.duplicates,
        "outOfOrder": engine.opportunities.outOfOrder,
        "staleForEpoch": engine.opportunities.staleForEpoch,
        "finalized": engine.opportunities.finalized,
        "platforms": [
            {
                **diagnostics.model_dump(mode="json"),
                "primaryTimeframe": PRIMARY_HORIZON[diagnostics.platform],
                "expectedSlots": sorted(engine.expected_slots(diagnostics.platform)),
            }
            for diagnostics in engine.opportunities.diagnostics()
        ],
    }


@router.get("/api/opportunities/{platform}")
async def platform_board(platform: Platform, request: Request) -> dict[str, object]:
    engine = _engine(request)
    board = engine.opportunities.latest_board(platform)
    if board is None:
        raise HTTPException(404, "No opportunity board for this platform")
    return {
        **_versions(),
        "primaryTimeframe": PRIMARY_HORIZON[platform],
        "expectedSlots": sorted(engine.expected_slots(platform)),
        "board": board.model_dump(mode="json"),
    }


@router.get("/api/opportunities/{platform}/history")
async def platform_history(
    platform: Platform,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=HISTORY_LIMIT)] = 20,
) -> dict[str, object]:
    engine = _engine(request)
    return {
        **_versions(),
        "boards": [
            board.model_dump(mode="json")
            for board in engine.opportunities.recent_boards(platform, limit)
        ],
    }
