"""Controlled Phase 8 boards and canonical price samples for the paper simulation tests.

Boards are produced by the real ``OpportunityEngine`` rather than hand-written: Phase 9's
input contract *is* a finished Phase 8 board, so building one through the layer that owns it
means a ranking change has to be able to break these tests.

Price samples are built directly. Phase 9 consumes the canonical ``PriceSample`` stream, and
the no-lookahead rules have to be exercised at millisecond offsets that a real capture cadence
cannot be steered into producing on demand. The end-to-end test drives the real Phase 5-8
chain instead, so both the contract and the plumbing are covered.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import opportunity_fixtures as ranking
from features_fixtures import CONTEXT_A
from quant_engine.configuration import Platform
from quant_engine.market_models import DataQuality, PriceSample, QualityState, SourceType
from quant_engine.opportunity import OpportunityBoard, OpportunityEngine
from quant_engine.strategy.models import Direction

EPOCH = ranking.EPOCH
ASSETS = ranking.ASSETS
DECISION_LAG_MS = 1_200
"""How long after the bar closed the ninth slot finished being processed. Deliberately not
zero anywhere in these tests: an entry that silently used ``board.asOf`` would pass every
assertion if the fixtures pretended the decision was available the instant the bar ended."""


def quality(state: QualityState = "GOOD") -> DataQuality:
    clean = state in ("GOOD", "DEGRADED")
    return DataQuality(
        state=state,
        confidence=1.0 if clean else 0.2,
        freshness=1.0,
        completeness=1.0,
        sourceReliability=1.0,
        latencyMs=0.0,
    )


def sample(
    timestamp: int,
    price: float,
    *,
    platform: Platform = "capitalbear",
    slot: int = 1,
    asset: str | None = None,
    context: UUID = CONTEXT_A,
    source: SourceType = "SYNTHETIC",
    state: QualityState = "GOOD",
) -> PriceSample:
    return PriceSample(
        platform=platform,
        slotId=slot,
        assetName=asset if asset is not None else ASSETS[slot],
        contextId=context,
        calibrationProfileId=None,
        sourceType=source,
        timestamp=timestamp,
        price=price,
        quality=quality(state),
    )


def board(
    *,
    platform: Platform = "capitalbear",
    slot: int = 1,
    direction: Direction = "UP",
    as_of: int | None = None,
    confidence: float = 0.75,
    expected: set[int] | None = None,
    **changes: Any,
) -> OpportunityBoard:
    """A READY board whose leading analysis is the given slot and direction."""
    engine = OpportunityEngine()
    result = engine.ingest(
        ranking.ensemble(
            platform=platform,
            slot=slot,
            direction=direction,
            as_of=as_of if as_of is not None else EPOCH,
            confidence=confidence,
            **changes,
        ),
        expected if expected is not None else {slot},
    )
    assert result is not None
    return result.board


def decision_time(value: OpportunityBoard, lag_ms: int = DECISION_LAG_MS) -> int:
    """When the complete decision became available, which is never the bar's close time."""
    return value.asOf + lag_ms
