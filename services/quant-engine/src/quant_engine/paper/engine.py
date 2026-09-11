"""Event-driven lifecycle for paper trades (Phase 9).

The engine owns no market data. Phase 5 owns the canonical series, Phase 6 the features,
Phase 7 the opinions and Phase 8 the ranking; this layer holds only the paper trades that are
currently alive, a bounded tail of the ones that finished, and one market clock per platform.

It has exactly three inputs — a finished Phase 8 board, a canonical ``PriceSample``, and a
canonical market-time watermark — and it never asks for any of them. Nothing here polls, and
nothing here reads a wall clock: ``datetime.now()`` cannot appear in a layer whose entire
output must replay identically from the same recorded events.

**Causal ordering.** The pipeline delivers a market sample to this engine only *after* that
sample has finished producing whatever Phase 5-8 work it caused. So when a sample closes the
bar that completes a Phase 8 cohort, the paper intent for that board already exists by the
time the same sample is offered as a price, and that sample is the first price at or after
``decisionAvailableAt``. The rule is therefore exact rather than scheduling-dependent: the
entry is the first canonical eligible sample whose event time is at or after the moment the
decision became available, and CPU scheduling cannot move it earlier or later.

**No broker surface.** There is no import, name or code path here that could reach a broker
control. Paper simulation asks what the market did; it never touches what the market is shown.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from uuid import UUID

from quant_engine.configuration import Platform
from quant_engine.market_models import PriceSample
from quant_engine.opportunity.models import OpportunityBoard
from quant_engine.paper.accounting import realized_pnl, snapshot_at_entry
from quant_engine.paper.models import (
    PAPER_VERSION,
    SUPPORTED_FEATURE_VERSION,
    SUPPORTED_RANKING_VERSION,
    SUPPORTED_REGIME_VERSION,
    SUPPORTED_STRATEGY_VERSION,
    Code,
    PaperEngineState,
    PaperEventType,
    PaperOutcome,
    PaperSettlement,
    PaperStats,
    PaperTrade,
    PaperTradeEvent,
    PaperTradeStatus,
    PlatformPaperDiagnostics,
)
from quant_engine.paper.policy import (
    MAX_OPEN_PER_PLATFORM,
    SELECTION_MEMORY,
    SETTLEMENT_HISTORY_CAPACITY,
    TRADE_HISTORY_CAPACITY,
    PaperSettings,
)
from quant_engine.paper.resolver import (
    accepts_entry,
    accepts_expiry,
    entry_deadline,
    outcome_for,
    paper_trade_id,
    price_delta_bps,
    resolution_deadline,
    same_identity,
    usable,
)

type SlotKey = tuple[Platform, int]
type SelectionKey = tuple[Platform, int]

LIVE_STATUSES: frozenset[str] = frozenset({"PENDING_ENTRY", "OPEN"})
PLATFORMS: tuple[Platform, ...] = ("capitalbear", "iqoption")


@dataclass(frozen=True, slots=True)
class PaperUpdate:
    """What one input did to the paper layer.

    ``trades`` are snapshots to persist — one per transition, append-only — and ``rejection``
    names why an arriving board produced nothing, so a paper layer that never opens a trade can
    always say which rule declined.
    """

    trades: tuple[PaperTrade, ...] = ()
    events: tuple[PaperTradeEvent, ...] = ()
    settlements: tuple[PaperSettlement, ...] = ()
    rejection: Code | None = None

    @property
    def empty(self) -> bool:
        return not (self.trades or self.events or self.settlements)


@dataclass(slots=True)
class _Collector:
    trades: list[PaperTrade] = field(default_factory=list)
    events: list[PaperTradeEvent] = field(default_factory=list)
    settlements: list[PaperSettlement] = field(default_factory=list)

    def update(self, rejection: Code | None = None) -> PaperUpdate:
        return PaperUpdate(
            trades=tuple(self.trades),
            events=tuple(self.events),
            settlements=tuple(self.settlements),
            rejection=rejection,
        )


class PaperEngine:
    """One instance owns every live paper trade and a bounded tail of finished ones."""

    def __init__(self, settings: PaperSettings | None = None) -> None:
        self.settings = settings if settings is not None else PaperSettings()
        self.settingsError: str | None = None
        self.live: dict[SlotKey, PaperTrade] = {}
        self.history: deque[PaperTrade] = deque(maxlen=TRADE_HISTORY_CAPACITY)
        self.settlements: deque[PaperSettlement] = deque(maxlen=SETTLEMENT_HISTORY_CAPACITY)
        self._settled: set[UUID] = set()
        self.settlementCount = 0
        self._settled_order: deque[UUID] = deque(maxlen=TRADE_HISTORY_CAPACITY)
        self._selections: set[SelectionKey] = set()
        self._selection_order: deque[SelectionKey] = deque(maxlen=SELECTION_MEMORY)
        self._clock: dict[Platform, int] = {}
        self.duplicateSelections = 0
        self.skippedAlreadyOpen = 0
        self.skippedPlatformLimit = 0
        self.entryTimeouts = 0
        self.resolutionTimeouts = 0
        self.contextCancellations = 0
        self.unsupportedVersions = 0
        self.resolvedCount = 0
        self.cancelledCount = 0
        self.invalidCount = 0

    # --- inputs ------------------------------------------------------------------------

    def on_board(self, board: OpportunityBoard, decision_available_at: int) -> PaperUpdate:
        """Register at most one paper intent for one finished Phase 8 selection.

        ``decision_available_at`` is the canonical market event time at which this board first
        became available, supplied by the pipeline. It is never derived here, and it is never
        assumed to be ``board.asOf``: the bar a cohort describes closes before the cohort is
        assembled, so entering at the close time would be a price the decision did not exist
        at yet.
        """
        collected = _Collector()
        if not self.settings.enabled:
            return collected.update("PAPER_DISABLED")
        if not self._versions_supported(board):
            self.unsupportedVersions += 1
            return collected.update("UNSUPPORTED_VERSION")
        if self.settings.requireReadyBoard and board.status != "READY":
            return collected.update("BOARD_INELIGIBLE")
        if board.status not in ("READY", "PARTIAL"):
            return collected.update("BOARD_INELIGIBLE")
        direction = board.selectedDirection
        slot_id = board.selectedSlotId
        if slot_id is None or direction is None:
            return collected.update("NO_SELECTION")
        if direction != "UP" and direction != "DOWN":
            return collected.update("NO_SELECTION")
        candidate = next((c for c in board.candidates if c.slotId == slot_id), None)
        if candidate is None:
            return collected.update("BOARD_INELIGIBLE")
        if decision_available_at < board.asOf:
            # The decision cannot have been available before the bar it describes closed.
            return collected.update("CHRONOLOGY_INVALID")

        selection: SelectionKey = (board.platform, board.asOf)
        if selection in self._selections:
            self.duplicateSelections += 1
            return collected.update("DUPLICATE")
        # Decided now, whatever is decided. A board arrives twice — once when its cohort
        # completes and again when the next epoch makes it immutable — and the second arrival
        # carries a later watermark. Recording the decision here means a selection that was
        # refused for concurrency stays refused, rather than being entered seconds late at a
        # restated decision time it never had.
        self._remember(selection)

        slot: SlotKey = (board.platform, slot_id)
        existing = self.live.get(slot)
        if existing is not None and existing.status in LIVE_STATUSES:
            self.skippedAlreadyOpen += 1
            return collected.update("SKIPPED_ALREADY_OPEN")
        if self._live_count(board.platform) >= MAX_OPEN_PER_PLATFORM:
            self.skippedPlatformLimit += 1
            return collected.update("SKIPPED_PLATFORM_LIMIT")

        trade = PaperTrade(
            paperTradeId=paper_trade_id(
                platform=board.platform,
                board_as_of=board.asOf,
                slot_id=slot_id,
                asset_name=candidate.assetName,
                context_id=candidate.contextId,
                ranking_version=board.rankingVersion,
                paper_version=PAPER_VERSION,
            ),
            platform=board.platform,
            slotId=slot_id,
            assetName=candidate.assetName,
            contextId=candidate.contextId,
            direction=direction,
            boardAsOf=board.asOf,
            decisionAvailableAt=decision_available_at,
            rank=candidate.rank,
            rankScore=candidate.rankScore,
            ensembleConfidence=candidate.ensembleConfidence,
            agreement=candidate.agreement,
            primaryRegime=candidate.primaryRegime,
            regimeConfidence=candidate.regimeConfidence,
            leadMargin=board.leadMargin,
            boardStatus=board.status,
            durationMs=self.settings.duration_ms(board.platform),
            status="PENDING_ENTRY",
            outcome="UNRESOLVED",
            featureVersion=board.featureVersion,
            regimeVersion=board.regimeVersion,
            strategyVersion=board.strategyVersion,
            rankingVersion=board.rankingVersion,
            paperVersion=PAPER_VERSION,
            createdAtMarketTime=decision_available_at,
            reasons=["BOARD_SELECTION"],
        )
        self.live[slot] = trade
        self._record(collected, trade, "PENDING_CREATED", decision_available_at, None)
        self._advance(board.platform, decision_available_at, collected)
        return collected.update()

    def on_market_time(self, platform: Platform, market_time: int) -> PaperUpdate:
        """Advance one platform's market clock without offering a price.

        The pipeline's own availability watermark is a canonical market event time, so a bar
        closing on a quiet slot still lets a stalled intent time out at the right moment
        instead of waiting for a price that is never coming."""
        collected = _Collector()
        if not self.settings.enabled:
            return collected.update()
        self._advance(platform, market_time, collected)
        return collected.update()

    def on_market_sample(self, sample: PriceSample) -> PaperUpdate:
        """Offer one canonical price to the paper trade that owns its slot.

        Timeouts are applied first, at this sample's own event time, so a price that arrives
        after a deadline expires the trade rather than filling it late.
        """
        collected = _Collector()
        if not self.settings.enabled:
            return collected.update()
        self._advance(sample.platform, sample.timestamp, collected)
        trade = self.live.get((sample.platform, sample.slotId))
        if trade is None or trade.status not in LIVE_STATUSES:
            return collected.update()
        if not same_identity(trade, sample):
            # The physical slot is now carrying a different asset or a new context. Whatever
            # this price describes, it is not the market this trade was taken in.
            self.contextCancellations += 1
            reason: Code = (
                "ASSET_CHANGED" if sample.assetName != trade.assetName else "CONTEXT_CHANGED"
            )
            self._terminate(collected, trade, "CANCELLED", "UNRESOLVED", reason, sample.timestamp)
            return collected.update()
        if not usable(sample):
            return collected.update()
        if trade.status == "PENDING_ENTRY":
            self._try_entry(collected, trade, sample)
        else:
            self._try_expiry(collected, trade, sample)
        return collected.update()

    def reset_slot(self, platform: Platform, slot_ids: Iterable[int]) -> PaperUpdate:
        """Phase 5's reset chain reaches here last. A live trade on a reset slot is cancelled;
        resolved history is never deleted, because it is evidence about a market that really
        did move that way."""
        collected = _Collector()
        for slot_id in slot_ids:
            trade = self.live.get((platform, slot_id))
            if trade is None or trade.status not in LIVE_STATUSES:
                continue
            self._terminate(
                collected,
                trade,
                "CANCELLED",
                "UNRESOLVED",
                "SLOT_RESET",
                self._event_time(trade, platform),
            )
        return collected.update()

    def restore(self, trades: Iterable[PaperTrade]) -> PaperUpdate:
        """Rebuild state from persisted trades on start-up.

        Resolved, cancelled and invalid trades come back as history so statistics survive a
        restart. A PENDING_ENTRY or OPEN trade cannot: the canonical series it was waiting on
        is gone, and the capture layer mints a new ``contextId`` for every slot when it starts,
        so the exact identity it needs provably cannot return. Those are cancelled explicitly
        with RESTART_UNRESOLVABLE rather than being silently forgotten.
        """
        collected = _Collector()
        ordered = sorted(
            trades,
            key=lambda t: (
                t.resolvedAtMarketTime
                if t.resolvedAtMarketTime is not None
                else t.createdAtMarketTime,
                str(t.paperTradeId),
            ),
        )
        for trade in ordered:
            self._remember((trade.platform, trade.boardAsOf))
            if trade.status in LIVE_STATUSES:
                self.live[(trade.platform, trade.slotId)] = trade
                self._terminate(
                    collected,
                    trade,
                    "CANCELLED",
                    "UNRESOLVED",
                    "RESTART_UNRESOLVABLE",
                    self._event_time(trade, trade.platform),
                )
                continue
            if trade.status == "RESOLVED":
                self.resolvedCount += 1
                self._settle_once(trade)
            elif trade.status == "CANCELLED":
                self.cancelledCount += 1
            elif trade.status == "INVALID":
                self.invalidCount += 1
            self.history.append(trade)
        return collected.update()

    # --- lifecycle ---------------------------------------------------------------------

    def _try_entry(self, collected: _Collector, trade: PaperTrade, sample: PriceSample) -> None:
        deadline = entry_deadline(trade, self.settings.max_entry_delay_ms(trade.platform))
        if not accepts_entry(trade, sample, deadline=deadline):
            return  # pre-decision price, or one this trade may not use. Keep waiting.
        accounting = snapshot_at_entry(self.settings)
        opened = trade.model_copy(
            update={
                "status": "OPEN",
                "entryTime": sample.timestamp,
                "entryPrice": sample.price,
                "entrySource": sample.sourceType,
                "entryQuality": sample.quality.state,
                "expiryTargetTime": sample.timestamp + trade.durationMs,
                "paperCurrency": accounting.currency,
                "paperStake": accounting.stake,
                "paperPayoutRate": accounting.payoutRate,
                "reasons": _with(trade.reasons, "ENTRY_FILLED"),
            }
        )
        self.live[(trade.platform, trade.slotId)] = opened
        self._record(collected, opened, "OPENED", sample.timestamp, None)

    def _try_expiry(self, collected: _Collector, trade: PaperTrade, sample: PriceSample) -> None:
        if trade.expiryTargetTime is None or trade.entryPrice is None:
            return
        deadline = resolution_deadline(
            trade.expiryTargetTime, self.settings.max_resolution_lag_ms(trade.platform)
        )
        if not accepts_expiry(trade, sample, deadline=deadline):
            return
        outcome = outcome_for(trade.direction, trade.entryPrice, sample.price)
        resolved = trade.model_copy(
            update={
                "status": "RESOLVED",
                "outcome": outcome,
                "expiryTime": sample.timestamp,
                "expiryPrice": sample.price,
                "expirySource": sample.sourceType,
                "expiryQuality": sample.quality.state,
                "priceDelta": sample.price - trade.entryPrice,
                "priceDeltaBps": price_delta_bps(trade.entryPrice, sample.price),
                "realizedPaperPnl": realized_pnl(
                    outcome, stake=trade.paperStake, payout_rate=trade.paperPayoutRate
                ),
                "resolvedAtMarketTime": sample.timestamp,
                "reasons": _with(trade.reasons, "EXPIRY_FILLED"),
            }
        )
        self.resolvedCount += 1
        self._finish(resolved)
        self._record(collected, resolved, "RESOLVED", sample.timestamp, None)
        settlement = self._settle_once(resolved)
        if settlement is not None:
            collected.settlements.append(settlement)

    def _advance(self, platform: Platform, market_time: int, collected: _Collector) -> None:
        """Move one platform's market clock forward and expire whatever it passed.

        Any canonical event on a platform is evidence that platform's market time has reached
        that point, including one on another slot. That is deterministic in replay, and it is
        what lets a trade on a slot that has gone quiet time out at the correct moment."""
        current = self._clock.get(platform)
        if current is None or market_time > current:
            self._clock[platform] = market_time
        clock = self._clock[platform]
        for key, trade in list(self.live.items()):
            if key[0] != platform or trade.status not in LIVE_STATUSES:
                continue
            if trade.status == "PENDING_ENTRY":
                deadline = entry_deadline(trade, self.settings.max_entry_delay_ms(platform))
                if clock > deadline:
                    self.entryTimeouts += 1
                    self._terminate(collected, trade, "INVALID", "INVALID", "ENTRY_TIMEOUT", clock)
                continue
            if trade.expiryTargetTime is None:
                continue
            deadline = resolution_deadline(
                trade.expiryTargetTime, self.settings.max_resolution_lag_ms(platform)
            )
            if clock > deadline:
                self.resolutionTimeouts += 1
                self._terminate(collected, trade, "INVALID", "INVALID", "RESOLUTION_TIMEOUT", clock)

    def _terminate(
        self,
        collected: _Collector,
        trade: PaperTrade,
        status: PaperTradeStatus,
        outcome: PaperOutcome,
        reason: Code,
        market_time: int,
    ) -> None:
        invalid = list(trade.invalidReasons)
        if status == "INVALID":
            invalid = _with(invalid, reason)
        ended = trade.model_copy(
            update={
                "status": status,
                "outcome": outcome,
                "resolvedAtMarketTime": market_time,
                "reasons": _with(trade.reasons, reason),
                "invalidReasons": invalid,
            }
        )
        if status == "CANCELLED":
            self.cancelledCount += 1
        elif status == "INVALID":
            self.invalidCount += 1
        self._finish(ended)
        event: PaperEventType = "CANCELLED" if status == "CANCELLED" else "INVALIDATED"
        self._record(collected, ended, event, market_time, reason)

    def _finish(self, trade: PaperTrade) -> None:
        self.live.pop((trade.platform, trade.slotId), None)
        self.history.append(trade)

    def _record(
        self,
        collected: _Collector,
        trade: PaperTrade,
        event_type: PaperEventType,
        market_time: int,
        reason: Code | None,
    ) -> None:
        collected.trades.append(trade)
        price = trade.expiryPrice if event_type == "RESOLVED" else trade.entryPrice
        collected.events.append(
            PaperTradeEvent(
                paperTradeId=trade.paperTradeId,
                eventType=event_type,
                eventTime=market_time,
                platform=trade.platform,
                slotId=trade.slotId,
                assetName=trade.assetName,
                contextId=trade.contextId,
                status=trade.status,
                outcome=trade.outcome,
                price=price,
                reason=reason,
                paperVersion=trade.paperVersion,
            )
        )

    def _settle_once(self, trade: PaperTrade) -> PaperSettlement | None:
        """One settlement per resolved trade, ever. Phase 9.5 will size a daily guard off
        these, and a trade counted twice would be a fabricated result."""
        if trade.outcome not in ("WIN", "LOSS", "DRAW"):
            return None
        if trade.paperTradeId in self._settled:
            return None
        if len(self._settled_order) == self._settled_order.maxlen:
            self._settled.discard(self._settled_order[0])
        self._settled_order.append(trade.paperTradeId)
        self._settled.add(trade.paperTradeId)
        self.settlementCount += 1
        settlement = PaperSettlement(
            tradeId=trade.paperTradeId,
            platform=trade.platform,
            assetName=trade.assetName,
            settledAt=trade.resolvedAtMarketTime or trade.boardAsOf,
            outcome=trade.outcome,
            currency=trade.paperCurrency,
            stake=trade.paperStake,
            payoutRate=trade.paperPayoutRate,
            realizedPnl=trade.realizedPaperPnl,
            paperVersion=trade.paperVersion,
        )
        self.settlements.append(settlement)
        return settlement

    # --- helpers -----------------------------------------------------------------------

    @staticmethod
    def _versions_supported(board: OpportunityBoard) -> bool:
        return (
            board.featureVersion == SUPPORTED_FEATURE_VERSION
            and board.regimeVersion == SUPPORTED_REGIME_VERSION
            and board.strategyVersion == SUPPORTED_STRATEGY_VERSION
            and board.rankingVersion == SUPPORTED_RANKING_VERSION
        )

    def _remember(self, selection: SelectionKey) -> None:
        if selection in self._selections:
            return
        if len(self._selection_order) == self._selection_order.maxlen:
            self._selections.discard(self._selection_order[0])
        self._selection_order.append(selection)
        self._selections.add(selection)

    def _live_count(self, platform: Platform) -> int:
        return sum(
            1
            for key, trade in self.live.items()
            if key[0] == platform and trade.status in LIVE_STATUSES
        )

    def _event_time(self, trade: PaperTrade, platform: Platform) -> int:
        """Market time for a transition nothing in the market triggered — a slot reset or a
        restart. The platform clock if it has one, otherwise the trade's own timeline. Never a
        wall clock: an outcome-bearing record may not carry a timestamp a replay cannot
        reproduce."""
        clock = self._clock.get(platform)
        own = trade.entryTime if trade.entryTime is not None else trade.decisionAvailableAt
        return max(clock, own) if clock is not None else own

    # --- reads -------------------------------------------------------------------------

    def unresolved(self) -> int:
        """How many paper trades have not finished.

        The session guard needs this to know whether a stopped day may close yet, and it must
        come from here rather than from execution tickets: a ticket describes a press, and a
        paper trade describes a measurement that is still running.
        """
        return sum(1 for trade in self.live.values() if trade.status in LIVE_STATUSES)

    def market_time(self) -> int | None:
        """The latest canonical market event time seen on any platform, or ``None`` before the
        first one. The paper layer has no other clock and neither does anything reading this."""
        return max(self._clock.values()) if self._clock else None

    def open_trades(self) -> list[PaperTrade]:
        """Every live trade, oldest decision first."""
        return sorted(self.live.values(), key=lambda t: (t.decisionAvailableAt, t.slotId))

    def recent(self, platform: Platform | None, limit: int) -> list[PaperTrade]:
        """Finished trades, newest first, bounded by what the process kept."""
        if limit <= 0:
            return []
        rows = [t for t in reversed(self.history) if platform is None or t.platform == platform]
        return rows[:limit]

    def find(self, trade_id: UUID) -> PaperTrade | None:
        for trade in self.live.values():
            if trade.paperTradeId == trade_id:
                return trade
        for trade in reversed(self.history):
            if trade.paperTradeId == trade_id:
                return trade
        return None

    def recent_settlements(self, limit: int) -> list[PaperSettlement]:
        return list(reversed(self.settlements))[: max(0, limit)]

    def stats(self, platform: Platform | None = None) -> PaperStats:
        """Descriptive only. Streaks run over finished trades in resolution order, and any
        non-win ends a win streak — a DRAW is not a win, so it does not extend one."""
        rows = [t for t in self.history if platform is None or t.platform == platform]
        outcomes = [t.outcome for t in rows if t.status == "RESOLVED"]
        wins = outcomes.count("WIN")
        losses = outcomes.count("LOSS")
        draws = outcomes.count("DRAW")
        resolved = len(outcomes)
        deltas = [t.priceDeltaBps for t in rows if t.priceDeltaBps is not None]
        money = [t.realizedPaperPnl for t in rows if t.realizedPaperPnl is not None]
        profit = sum(value for value in money if value > 0)
        loss = sum(value for value in money if value < 0)
        return PaperStats(
            platform=platform,
            paperVersion=PAPER_VERSION,
            resolved=resolved,
            wins=wins,
            losses=losses,
            draws=draws,
            invalid=sum(1 for t in rows if t.status == "INVALID"),
            cancelled=sum(1 for t in rows if t.status == "CANCELLED"),
            winRateExcludingDraws=wins / (wins + losses) if wins + losses else None,
            winRateIncludingDraws=wins / resolved if resolved else None,
            currentWinStreak=_streak(outcomes, "WIN", current=True),
            currentLossStreak=_streak(outcomes, "LOSS", current=True),
            maxWinStreak=_streak(outcomes, "WIN", current=False),
            maxLossStreak=_streak(outcomes, "LOSS", current=False),
            averagePriceDeltaBps=sum(deltas) / len(deltas) if deltas else None,
            grossPaperProfit=profit if money else None,
            grossPaperLoss=loss if money else None,
            netPaperPnl=sum(money) if money else None,
        )

    def state(self) -> PaperEngineState:
        return PaperEngineState(
            paperVersion=PAPER_VERSION,
            enabled=self.settings.enabled,
            accountingConfigured=self.settings.accountingConfigured,
            settingsError=self.settingsError,
            pending=sum(1 for t in self.live.values() if t.status == "PENDING_ENTRY"),
            open=sum(1 for t in self.live.values() if t.status == "OPEN"),
            resolved=self.resolvedCount,
            cancelled=self.cancelledCount,
            invalid=self.invalidCount,
            duplicateSelections=self.duplicateSelections,
            skippedAlreadyOpen=self.skippedAlreadyOpen,
            skippedPlatformLimit=self.skippedPlatformLimit,
            entryTimeouts=self.entryTimeouts,
            resolutionTimeouts=self.resolutionTimeouts,
            contextCancellations=self.contextCancellations,
            unsupportedVersions=self.unsupportedVersions,
            settlements=self.settlementCount,
            platforms=[self._diagnostics(platform) for platform in PLATFORMS],
        )

    def _diagnostics(self, platform: Platform) -> PlatformPaperDiagnostics:
        rows = [t for t in self.history if t.platform == platform]
        live = [t for t in self.live.values() if t.platform == platform]
        outcomes = [t.outcome for t in rows if t.status == "RESOLVED"]
        return PlatformPaperDiagnostics(
            platform=platform,
            pending=sum(1 for t in live if t.status == "PENDING_ENTRY"),
            open=sum(1 for t in live if t.status == "OPEN"),
            resolved=len(outcomes),
            wins=outcomes.count("WIN"),
            losses=outcomes.count("LOSS"),
            draws=outcomes.count("DRAW"),
            invalid=sum(1 for t in rows if t.status == "INVALID"),
            cancelled=sum(1 for t in rows if t.status == "CANCELLED"),
            durationMs=self.settings.duration_ms(platform),
            marketTime=self._clock.get(platform),
        )


def _with(codes: Sequence[Code], code: Code) -> list[Code]:
    """Append a code once, keeping the list inside its bound."""
    if code in codes:
        return list(codes)
    return [*codes, code][-8:]


def _streak(outcomes: Sequence[PaperOutcome], target: PaperOutcome, *, current: bool) -> int:
    if current:
        count = 0
        for outcome in reversed(outcomes):
            if outcome != target:
                break
            count += 1
        return count
    best = run = 0
    for outcome in outcomes:
        run = run + 1 if outcome == target else 0
        best = max(best, run)
    return best


__all__ = ["PaperEngine", "PaperUpdate"]
