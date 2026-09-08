"""Bounded HTTP batches, local-user trust boundary, one canonical builder per slot."""

import asyncio
import time
from typing import Annotated, Literal, Self, cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field, model_validator

from quant_engine.configuration import Model, Platform
from quant_engine.market_builder import TimeSeriesBuilder
from quant_engine.market_models import Candle, MarketObservation
from quant_engine.market_storage import ParquetStorage

router = APIRouter()


class ObservationBatch(Model):
    observations: list[MarketObservation] = Field(min_length=1, max_length=18)


class SlotSeriesStatus(Model):
    platform: Platform
    slotId: int
    contextId: UUID
    secondSamples: int
    m1Samples: int
    m1State: Literal["FORMING", "CLOSED"] | None


class BatchResult(Model):
    accepted: int
    queueDepth: int
    rejected: int
    slots: list[SlotSeriesStatus]


type SlotId = Annotated[int, Field(ge=1, le=9, strict=True)]


class ResetSlotsRequest(Model):
    platform: Platform
    slotIds: list[SlotId] = Field(min_length=1, max_length=9)

    @model_validator(mode="after")
    def unique_slots(self) -> Self:
        if len(set(self.slotIds)) != len(self.slotIds):
            raise ValueError("Slot IDs must be unique")
        return self


class ResetSlotsResult(Model):
    reset: int = Field(ge=0, le=9)


class MarketEngine:
    def __init__(self, storage: ParquetStorage) -> None:
        self.storage = storage
        self.builders: dict[tuple[str, int], TimeSeriesBuilder] = {}
        self.busy = False
        self.rejected = 0
        self.storage_error = False

    def ingest(self, batch: ObservationBatch, now: int) -> int:
        accepted = 0
        for observation in batch.observations:
            stamp = int(observation.observedAt.timestamp() * 1000)
            if observation.sourceType in ("DOM", "VISUAL") and not 0 <= now - stamp <= 3000:
                self.rejected += 1
                continue
            builder = self.builders.setdefault(
                (observation.platform, observation.slotId), TimeSeriesBuilder()
            )
            self.storage.append("observations", observation)
            sample = builder.ingest(observation)
            if sample:
                self.storage.append("samples", sample)
                accepted += 1
            else:
                self.rejected += 1
            self.persist_events(builder)
        self.storage_error = False
        return accepted

    def persist_events(self, builder: TimeSeriesBuilder) -> None:
        while builder.emitted:
            record = builder.emitted[0]
            self.storage.append("candles" if isinstance(record, Candle) else "seconds", record)
            builder.emitted.popleft()

    def reset_slots(self, platform: Platform, slot_ids: list[int]) -> int:
        """Drop live series immediately when their configured identity changes."""
        return sum(self.builders.pop((platform, slot_id), None) is not None for slot_id in slot_ids)

    def status(self) -> list[SlotSeriesStatus]:
        slots = []
        for builder in self.builders.values():
            if not builder.samples:
                continue
            sample = builder.samples[-1]
            candle = builder.forming.get("M1") or next(
                (c for c in reversed(builder.candles) if c.timeframe == "M1"), None
            )
            slots.append(
                SlotSeriesStatus(
                    platform=sample.platform,
                    slotId=sample.slotId,
                    contextId=sample.contextId,
                    secondSamples=len(builder.seconds),
                    m1Samples=candle.sampleCount if candle else 0,
                    m1State=candle.state if candle else None,
                )
            )
        return slots

    def advance_live(self, now: int) -> None:
        for builder in self.builders.values():
            if builder.samples and builder.samples[-1].sourceType in ("DOM", "VISUAL"):
                # A 3-second watermark allows batches to arrive before closure.
                builder.advance(now - 3000)
                self.persist_events(builder)


def local_only(request: Request) -> None:
    if (
        request.headers.get("origin") is not None
        or request.headers.get("sec-fetch-site") is not None
    ):
        raise HTTPException(403, "Browser requests are not permitted")


@router.post("/api/market/observations")
async def observations(batch: ObservationBatch, request: Request) -> BatchResult:
    local_only(request)
    engine = cast(MarketEngine, request.app.state.market)
    if engine.busy:
        raise HTTPException(429, "Engine busy; keep latest observations")
    engine.busy = True
    try:
        accepted = await asyncio.to_thread(engine.ingest, batch, int(time.time() * 1000))
        return BatchResult(
            accepted=accepted, queueDepth=0, rejected=engine.rejected, slots=engine.status()
        )
    except Exception as error:
        engine.storage_error = True
        raise HTTPException(503, "Market storage unavailable") from error
    finally:
        engine.busy = False


@router.post("/api/market/slots/reset")
async def reset_slots(command: ResetSlotsRequest, request: Request) -> ResetSlotsResult:
    local_only(request)
    engine = cast(MarketEngine, request.app.state.market)
    if engine.busy:
        raise HTTPException(429, "Engine busy")
    engine.busy = True
    try:
        return ResetSlotsResult(reset=engine.reset_slots(command.platform, command.slotIds))
    finally:
        engine.busy = False


@router.get("/api/market/state")
async def state(request: Request) -> dict[str, object]:
    local_only(request)
    engine = cast(MarketEngine, request.app.state.market)
    if engine.busy:
        raise HTTPException(429, "Engine busy")
    return {
        "storageError": engine.storage_error,
        "queueDepth": int(engine.busy),
        "rejected": engine.rejected,
        "slots": [
            {
                "platform": key[0],
                "slotId": key[1],
                "seconds": len(builder.seconds),
                "missingSeconds": builder.missingSeconds,
                "candles": [
                    c.model_dump(mode="json")
                    for c in [*builder.candles, *builder.forming.values()][-20:]
                ],
            }
            for key, builder in engine.builders.items()
        ],
    }
