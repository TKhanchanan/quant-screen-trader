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
from quant_engine.market_models import Candle, MarketObservation, PriceSample, Timeframe
from quant_engine.market_storage import ParquetStorage
from quant_engine.opportunity import OpportunityBoard, OpportunityEngine
from quant_engine.paper import PaperEngine, PaperSettings, PaperTrade, settings_from_environment
from quant_engine.paper.engine import PaperUpdate
from quant_engine.session_guard import (
    DailySession,
    SessionGuard,
    SessionGuardEvent,
    SessionGuardSettings,
)
from quant_engine.session_guard.engine import GuardUpdate
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
    def __init__(
        self,
        storage: ParquetStorage,
        paper: PaperSettings | None = None,
        guard: SessionGuardSettings | None = None,
    ) -> None:
        self.storage = storage
        self.builders: dict[tuple[str, int], TimeSeriesBuilder] = {}
        self.features = FeatureEngine(hydrator=self._history)
        self.strategy = StrategyEngine()
        self.opportunities = OpportunityEngine()
        self.availability: dict[tuple[str, int], int] = {}
        settings, error = (paper, None) if paper is not None else _paper_settings()
        self.paper = PaperEngine(settings)
        self.paper.settingsError = error
        # Phase 9.5 sits downstream of Phase 9 and upstream of nothing. It reads settled
        # outcomes and publishes one permission; it cannot reach a board, a strategy or a press.
        self.guard = SessionGuard(guard)
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
            if sample:
                self.offer_paper_price(sample)
        self.storage_error = False
        return accepted

    def persist_events(self, builder: TimeSeriesBuilder) -> None:
        """Canonical Phase 5 events feed the feature engine exactly once, as they are stored.

        Each emission carries the watermark that made it available, and that time travels with
        it all the way to Phase 9: a paper entry may never be priced at a bar's close time when
        the decision built on that bar did not exist until later.
        """
        while builder.emitted:
            emission = builder.emitted[0]
            record, available_at = emission.record, emission.availableAt
            if isinstance(record, Candle):
                self.storage.append("candles", record)
                snapshot = self.features.ingest_candle(record)
                if snapshot is not None:
                    self.storage.append("features", snapshot)
                    self.evaluate_primary_close(snapshot, available_at)
            else:
                self.storage.append("seconds", record)
                self.features.ingest_second(record)
            # Canonical market time advanced on this platform, whether or not anything was
            # decided: a stalled paper intent times out on market events, never on a clock.
            self.persist_paper(self.paper.on_market_time(record.platform, available_at))
            builder.emitted.popleft()

    def offer_paper_price(self, sample: PriceSample) -> None:
        """Offer one canonical sample to Phase 9, after everything it caused has run.

        Ordering is the point. The Phase 5-8 chain for this sample has already finished by the
        time it reaches here, so a paper intent created *by* this sample already exists and this
        sample is honestly the first canonical price at or after the decision became available.
        Only accepted samples are offered: the per-second record carries the same timestamp and
        price, and replaying it would re-offer a price the paper layer has already seen.
        """
        self.persist_paper(self.paper.on_market_sample(sample))

    def persist_paper(self, update: PaperUpdate) -> None:
        for trade in update.trades:
            self.storage.append("paper_trades", trade)
        for event in update.events:
            self.storage.append("paper_trade_events", event)
        # Settlements are handed to the session guard as they are produced, in the order Phase 9
        # produced them. Nothing polls paper history to rebuild a daily total: a running sum that
        # is recomputed from a bounded tail would quietly start disagreeing with the durable one.
        for settlement in update.settlements:
            self.persist_session(
                self.guard.apply_settlement(settlement, unresolved=self.paper.unresolved())
            )
        if update.trades:
            at = self.paper.market_time()
            if at is not None:
                self.persist_session(self.guard.observe(self.paper.unresolved(), at))

    def persist_session(self, update: GuardUpdate) -> None:
        for session in update.sessions:
            self.storage.append("daily_sessions", session)
        for event in update.events:
            self.storage.append("session_guard_events", event)

    def restore_session_guard(self, now_ms: int) -> int:
        """Rebuild today's accounting at start-up.

        Best effort, like the paper restore beside it: an unreadable history must not stop the
        engine. What it must never do is come back as a fresh day — a session that already hit
        its loss limit and reopened with permission restored would be the exact failure this
        layer exists to prevent — so a failure here leaves the guard with no session at all
        rather than an empty one.
        """
        try:
            sessions = [
                row
                for row in self.storage.reload("daily_sessions")
                if isinstance(row, DailySession)
            ]
            events = [
                row
                for row in self.storage.reload("session_guard_events")
                if isinstance(row, SessionGuardEvent)
            ]
        except Exception:
            return 0
        self.persist_session(
            self.guard.restore(sessions, events, now_ms, unresolved=self.paper.unresolved())
        )
        return len(sessions)

    def restore_paper(self) -> int:
        """Rebuild Phase 9 state from the durable record at start-up.

        Best effort by design: an unreadable paper history must never stop the engine from
        starting, because live capture is the thing that cannot be recovered later. What it may
        never do is silently forget an open trade, so anything that cannot be safely continued
        is cancelled explicitly and that cancellation is itself persisted.
        """
        try:
            rows = self.storage.reload("paper_trades")
        except Exception:
            return 0
        latest: dict[str, PaperTrade] = {}
        for row in rows:
            if not isinstance(row, PaperTrade):
                continue
            key = str(row.paperTradeId)
            previous = latest.get(key)
            if previous is None or _state_rank(row) >= _state_rank(previous):
                latest[key] = row
        update = self.paper.restore(latest.values())
        self.persist_paper(update)
        return len(latest)

    def evaluate_primary_close(self, snapshot: FeatureSnapshot, available_at: int) -> None:
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
        # When this slot's opinion for this close first existed. A cohort is only as available
        # as its latest member, so the board's own availability is derived from these.
        self.availability[(ensemble.platform, ensemble.slotId)] = available_at
        self.rank_opportunity(ensemble, available_at)

    def expected_slots(self, platform: Platform) -> set[int]:
        """Which slots this platform's live observation pipeline is actually carrying.

        A builder exists only for a slot that has produced observations, so a disabled or
        unassigned slot is simply absent and can never hold a ranking cohort at COLLECTING.
        A slot whose identity changed is dropped with its builder and re-enters the cohort
        the moment it reports again under its new identity.
        """
        return {slot_id for name, slot_id in self.builders if name == platform}

    def rank_opportunity(self, ensemble: EnsembleSnapshot, available_at: int) -> None:
        """Rank one NEW Phase 7 ensemble against its platform's current cohort.

        Only new ones reach here — a repeated primary close returned above — so one close
        contributes to its ranking epoch exactly once. A board is written when the next epoch
        supersedes it, which is the moment it stops being able to change.
        """
        result = self.opportunities.ingest(ensemble, self.expected_slots(ensemble.platform))
        if result is None:
            return
        # Phase 9 sees the board that just became immutable before the live one, which is the
        # chronological order in which they became available. Both are offered under the same
        # watermark because that watermark is the moment each of them first existed: a
        # superseded board becomes final exactly when the next epoch opens.
        if result.finalized is not None:
            self.persist_paper(
                self.paper.on_board(
                    result.finalized, self.board_available_at(result.finalized, available_at)
                )
            )
        self.persist_paper(
            self.paper.on_board(result.board, self.board_available_at(result.board, available_at))
        )
        if result.finalized is None:
            return
        for candidate in result.finalized.candidates:
            self.storage.append("opportunity_candidates", candidate)
        self.storage.append("opportunity_boards", result.finalized)

    def board_available_at(self, board: OpportunityBoard, watermark: int) -> int:
        """The canonical market time at which this whole board first existed.

        A cohort is exactly as available as its slowest member. The ninth slot's bar closes on
        its own sample, which can be hundreds of milliseconds after the close time every member
        shares, so the completing arrival's watermark alone would understate when the finished
        decision could first have been acted on. Taking the maximum over the members that are
        actually on the board — never below the board's own close time — can only move an entry
        later, which is the only direction a measurement layer is allowed to be wrong in.
        """
        times = [
            self.availability[(board.platform, candidate.slotId)]
            for candidate in board.candidates
            if (board.platform, candidate.slotId) in self.availability
        ]
        return max(watermark, board.asOf, *times)

    def reset_slots(self, platform: Platform, slot_ids: list[int]) -> int:
        """Drop live series and every derived feature when a configured identity changes."""
        self.features.reset_slot(platform, slot_ids)
        self.strategy.reset_slot(platform, slot_ids)
        self.opportunities.reset_slot(platform, slot_ids)
        for slot_id in slot_ids:
            self.availability.pop((platform, slot_id), None)
        # Phase 9 is last in the reset chain. A live paper trade on a reset slot is cancelled;
        # resolved history is evidence about a market that really moved, and is never deleted.
        self.persist_paper(self.paper.reset_slot(platform, slot_ids))
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
        # The one place the guard is told what time it is. A trading day is born here, on the
        # background tick, and never inside a read: opening a diagnostics panel must not be able
        # to roll the calendar.
        self.persist_session(self.guard.tick(now, unresolved=self.paper.unresolved()))
        for builder in self.builders.values():
            if builder.samples and builder.samples[-1].sourceType in ("DOM", "VISUAL"):
                # A 3-second watermark allows batches to arrive before closure.
                builder.advance(now - 3000)
                self.persist_events(builder)


def _paper_settings() -> tuple[PaperSettings, str | None]:
    """Settings from the environment, or safe defaults plus the reason they could not be read.

    A malformed simulated stake must not stop the engine: refusing to record directional
    outcomes over a typo would lose real evidence and protect nothing. Accounting simply stays
    off and the reason is reported on the state endpoint.
    """
    try:
        return settings_from_environment(), None
    except ValueError as error:
        return PaperSettings(), str(error)[:200]


def _state_rank(trade: PaperTrade) -> tuple[int, int]:
    """Newest-state-wins ordering for reloaded rows of one trade.

    Terminal states are absorbing, so status precedence alone orders the transitions; the
    resolution time breaks ties between rows written at the same stage.
    """
    order = {"PENDING_ENTRY": 0, "OPEN": 1, "CANCELLED": 2, "INVALID": 2, "RESOLVED": 3}
    return (order[trade.status], trade.resolvedAtMarketTime or trade.createdAtMarketTime)


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
