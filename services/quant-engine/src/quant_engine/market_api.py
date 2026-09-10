"""Bounded HTTP batches, local-user trust boundary, one canonical builder per slot."""

import asyncio
import time
from typing import Annotated, Literal, Self, cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field, model_validator

from quant_engine.configuration import Model, Platform
from quant_engine.features import FeatureEngine
from quant_engine.features.engine import PRIMARY_TIMEFRAME
from quant_engine.features.models import FeatureSnapshot
from quant_engine.market_builder import TimeSeriesBuilder
from quant_engine.market_models import Candle, MarketObservation, Timeframe
from quant_engine.market_storage import ParquetStorage
from quant_engine.opportunity import OpportunityEngine
from quant_engine.strategy import StrategyEngine
from quant_engine.strategy.models import EnsembleSnapshot

router = APIRouter()


class ObservationBatch(Model):
    observations: list[MarketObservation] = Field(min_length=1, max_length=18)


class SlotSeriesStatus(Model):
    platform: Platform
    slotId: int
    contextId: UUID
    secondSamples: int
    s5Samples: int
    s5State: Literal["FORMING", "CLOSED"] | None
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
        self.features = FeatureEngine(hydrator=self._history)
        self.strategy = StrategyEngine()
        self.opportunities = OpportunityEngine()
        self.busy = False
        self.rejected = 0
        self.storage_error = False

    def _history(
        self, platform: Platform, asset_name: str, timeframe: Timeframe, before: int
    ) -> list[Candle]:
        """Best-effort warm-up source. WARMING is always a valid outcome, so a storage
        problem must never stop live ingestion."""
        try:
            return self.storage.load_history(platform, asset_name, timeframe, before=before)
        except Exception:
            return []

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
        """Canonical Phase 5 events feed the feature engine exactly once, as they are stored."""
        while builder.emitted:
            record = builder.emitted[0]
            if isinstance(record, Candle):
                self.storage.append("candles", record)
                snapshot = self.features.ingest_candle(record)
                if snapshot is not None:
                    self.storage.append("features", snapshot)
                    self.evaluate_primary_close(snapshot)
            else:
                self.storage.append("seconds", record)
                self.features.ingest_second(record)
            builder.emitted.popleft()

    def evaluate_primary_close(self, snapshot: FeatureSnapshot) -> None:
        """Run and store the whole Phase 7 chain for one closed PRIMARY snapshot.

        Phase 7 is triggered by a closed primary bar and never by a UI poll.

        CapitalBear decides on S5 and IQ Option on M1, so a closed M5 or M10 updates the
        context a later primary close will read as-of and produces no ensemble of its own.
        The bundle is the one the feature engine has already joined, so nothing here can see
        a timeframe that had not finished at the primary's close time.
        """
        if snapshot.timeframe != PRIMARY_TIMEFRAME[snapshot.platform]:
            return
        bundle = self.features.latest_bundle(snapshot.platform, snapshot.slotId)
        if bundle is None:
            return
        before = self.strategy.evaluated
        ensemble = self.strategy.evaluate(bundle)
        if self.strategy.evaluated == before:
            return  # already evaluated at this as-of time; one primary close, one ensemble
        self.storage.append("regimes", ensemble.regime)
        for evaluation in ensemble.strategies:
            self.storage.append("strategy_evaluations", evaluation)
        self.storage.append("ensembles", ensemble)
        self.rank_opportunity(ensemble)

    def expected_slots(self, platform: Platform) -> set[int]:
        """Which slots this platform's live observation pipeline is actually carrying.

        A builder exists only for a slot that has produced observations, so a disabled or
        unassigned slot is simply absent and can never hold a ranking cohort at COLLECTING.
        A slot whose identity changed is dropped with its builder and re-enters the cohort
        the moment it reports again under its new identity.
        """
        return {slot_id for name, slot_id in self.builders if name == platform}

    def rank_opportunity(self, ensemble: EnsembleSnapshot) -> None:
        """Rank one NEW Phase 7 ensemble against its platform's current cohort.

        Only new ones reach here — a repeated primary close returned above — so one close
        contributes to its ranking epoch exactly once. A board is written when the next epoch
        supersedes it, which is the moment it stops being able to change.
        """
        result = self.opportunities.ingest(ensemble, self.expected_slots(ensemble.platform))
        if result is None or result.finalized is None:
            return
        for candidate in result.finalized.candidates:
            self.storage.append("opportunity_candidates", candidate)
        self.storage.append("opportunity_boards", result.finalized)

    def reset_slots(self, platform: Platform, slot_ids: list[int]) -> int:
        """Drop live series and every derived feature when a configured identity changes."""
        self.features.reset_slot(platform, slot_ids)
        self.strategy.reset_slot(platform, slot_ids)
        self.opportunities.reset_slot(platform, slot_ids)
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
            s5 = builder.forming.get("S5") or next(
                (c for c in reversed(builder.candles) if c.timeframe == "S5"), None
            )
            slots.append(
                SlotSeriesStatus(
                    platform=sample.platform,
                    slotId=sample.slotId,
                    contextId=sample.contextId,
                    secondSamples=len(builder.seconds),
                    s5Samples=s5.sampleCount if s5 else 0,
                    s5State=s5.state if s5 else None,
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
