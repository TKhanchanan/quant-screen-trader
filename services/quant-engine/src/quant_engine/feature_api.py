"""Local-only read API over the quantitative feature engine. No trading surface."""

from typing import Annotated, cast

from fastapi import APIRouter, HTTPException, Path, Query, Request

from quant_engine.configuration import Platform
from quant_engine.features import FEATURE_VERSION
from quant_engine.market_api import MarketEngine, local_only
from quant_engine.market_models import TIMEFRAMES, Timeframe

router = APIRouter()

type SlotPath = Annotated[int, Path(ge=1, le=9)]


def _engine(request: Request) -> MarketEngine:
    local_only(request)
    return cast(MarketEngine, request.app.state.market)


@router.get("/api/features/state")
async def feature_state(request: Request) -> dict[str, object]:
    engine = _engine(request)
    return {
        "featureVersion": FEATURE_VERSION,
        "rejected": engine.features.rejected,
        "slots": [state.model_dump(mode="json") for state in engine.features.diagnostics()],
    }


@router.get("/api/features/{platform}/{slot_id}")
async def slot_features(
    platform: Platform,
    slot_id: SlotPath,
    request: Request,
    timeframe: Annotated[Timeframe | None, Query()] = None,
) -> dict[str, object]:
    engine = _engine(request)
    bundle = engine.features.latest_bundle(platform, slot_id)
    if bundle is None:
        raise HTTPException(404, "No feature state for this slot")
    wanted: tuple[Timeframe, ...] = (timeframe,) if timeframe else tuple(TIMEFRAMES)
    snapshots = {
        name: snapshot.model_dump(mode="json")
        for name in wanted
        if (snapshot := engine.features.latest_snapshot(platform, slot_id, name)) is not None
    }
    return {
        "featureVersion": FEATURE_VERSION,
        "snapshots": snapshots,
        "bundle": bundle.model_dump(mode="json"),
    }
