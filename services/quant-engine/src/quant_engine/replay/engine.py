"""The replay driver: historical events into the real analytical pipeline (Phase 11).

**One brain.** This module contains no indicator, no regime rule, no strategy, no rank score and
no outcome rule. It constructs an isolated ``MarketEngine`` — the same object the live process
runs — hands it historical observations in canonical market order, and records what the Phase
5-9 chain produced. If a replay and the live path ever disagree on identical canonical events,
that is a bug in one of them rather than a difference between two implementations, because there
is only one implementation.

**Isolated.** The engine, its storage, its paper layer, its session guard and its analytics are
all constructed here and thrown away with the run. The live engine is never read, never written,
never locked and never constructed; the live market record is opened read-only as input and
every output goes under the run's own namespace. There is no import in this package that could
reach a broker control, an order, an execution mode or an arm state, and a test asserts it.

**Offline speed.** Replay advances on events, never on the host's clock, and never sleeps: a
week of market time costs whatever the CPU takes, not a week.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid5

from quant_engine.analytics import AnalyticsEngine, AnalyticsSnapshot, build
from quant_engine.analytics.models import AnalyticsDataset
from quant_engine.configuration import Platform
from quant_engine.features.models import FeatureSnapshot
from quant_engine.market_api import MarketEngine, ObservationBatch
from quant_engine.market_models import Candle, Timeframe
from quant_engine.market_storage import Category, ParquetStorage, Record
from quant_engine.opportunity.models import OpportunityBoard
from quant_engine.paper.engine import PaperEngine, PaperUpdate
from quant_engine.paper.models import PaperTrade
from quant_engine.paper.policy import PaperSettings
from quant_engine.replay.clock import ReplayClock, settlement_tail_ms, warmup_duration_ms
from quant_engine.replay.models import (
    REPLAY_VERSION,
    ReplayCausalityAudit,
    ReplayDatasetSummary,
    ReplayManifest,
    ReplayRun,
    ReplayRunStatus,
)
from quant_engine.replay.source import REPLAY_ENTRY_LAYER, ReplaySource
from quant_engine.session_guard.engine import SessionGuard
from quant_engine.session_guard.models import DailySession
from quant_engine.session_guard.settings import SessionGuardSettings
from quant_engine.strategy.models import EnsembleSnapshot, RegimeSnapshot, StrategyEvaluation

REPLAY_NAMESPACE = UUID("5b2f7c14-9d38-4e6a-91c0-7a4d2e8b6f03")
"""Fixed UUID5 namespace for replay run identity. Never regenerated: a new namespace would give
the same history under the same settings a different id and orphan every stored result."""

OUTSIDE_EVALUATION_WINDOW = "REPLAY_OUTSIDE_EVALUATION_WINDOW"
SESSION_GUARD_BLOCKED = "REPLAY_SESSION_GUARD_BLOCKED"
"""Replay driver refusals, deliberately prefixed. They are not ``qst-paper-v1`` rejection codes:
the paper contract has no idea an evaluation window exists, and a code that looked like one of
its own would make a replay-only rule read as a change to the recorded contract."""

CANCEL_INTERVAL = 2_048
PROGRESS_INTERVAL = 512
EVALUATION_MEMORY = 250_000
TRADE_ROW_MEMORY = 200_000
SESSION_ROW_MEMORY = 20_000

RETAINED: frozenset[str] = frozenset(
    {
        "paper_trades",
        "paper_trade_events",
        "opportunity_boards",
        "opportunity_candidates",
        "ensembles",
        "regimes",
        "strategy_evaluations",
        "daily_sessions",
        "session_guard_events",
    }
)
"""What a replay keeps on disk. The derived series — observations, samples, seconds, candles and
feature snapshots — are exactly reproducible from the source and the version contract, so
writing hundreds of megabytes of them per run would spend disk on something that is never the
record of anything. What is kept is the evidence: the decisions and their outcomes."""


class WindowedPaperEngine(PaperEngine):
    """``qst-paper-v1``, with two replay-only refusals placed in front of it.

    Every timing rule, entry rule, expiry rule and outcome rule is inherited unchanged — this
    class cannot reach one, let alone restate one. It decides only *whether a selection is
    allowed to become a paper intent at all*, which is the one question a replay has that the
    live layer does not: warm-up decisions must never become measured trades, decisions after
    the evaluation window closes must never become measured trades, and a sandbox session guard
    scenario must be able to withdraw permission without the real one existing.

    ``decisionDelayMs`` is the latency research knob. It shifts only the moment the decision is
    treated as actionable, so the board, the selection and the deterministic trade id are
    identical to the baseline's and only the hypothetical entry moves.
    """

    def __init__(
        self,
        settings: PaperSettings,
        *,
        opens_from: int,
        opens_until: int,
        decision_delay_ms: int = 0,
        permits: Callable[[], bool] | None = None,
    ) -> None:
        super().__init__(settings)
        self.opensFrom = opens_from
        self.opensUntil = opens_until
        self.decisionDelayMs = decision_delay_ms
        self.permits = permits
        self.outsideWindow = 0
        self.guardBlocked = 0

    def on_board(self, board: OpportunityBoard, decision_available_at: int) -> PaperUpdate:
        if not self.opensFrom <= decision_available_at <= self.opensUntil:
            # Warm-up and settlement-tail decisions are refused here rather than filtered later,
            # so a warm-up trade never exists to be counted and the tail can only ever finish
            # what the window started.
            self.outsideWindow += 1
            return PaperUpdate(rejection=OUTSIDE_EVALUATION_WINDOW)
        if self.permits is not None and not self.permits():
            self.guardBlocked += 1
            return PaperUpdate(rejection=SESSION_GUARD_BLOCKED)
        return super().on_board(board, decision_available_at + self.decisionDelayMs)


class _Audit:
    """Running proof that nothing downstream ever saw past the replay clock."""

    __slots__ = (
        "boardAhead",
        "boards",
        "checks",
        "clock",
        "ensembleAhead",
        "ensembles",
        "entryBefore",
        "expiryBefore",
        "featureAhead",
        "features",
        "maxEntryLag",
        "maxExpiryLag",
        "readyAt",
        "violations",
    )

    def __init__(self, clock: ReplayClock) -> None:
        self.clock = clock
        self.checks = 0
        self.violations = 0
        self.features: int | None = None
        self.ensembles: int | None = None
        self.boards: int | None = None
        self.ensembleAhead = 0
        self.boardAhead = 0
        self.featureAhead = 0
        self.entryBefore = 0
        self.expiryBefore = 0
        self.maxEntryLag: int | None = None
        self.maxExpiryLag: int | None = None
        self.readyAt: int | None = None

    def observe(self, category: Category, record: Record) -> None:
        now = self.clock.now
        if now is None:
            return
        self.checks += 1
        if category == "features" and isinstance(record, FeatureSnapshot):
            stamp = record.featureTime
            self.features = stamp if self.features is None else max(self.features, stamp)
            if stamp > now:
                self.featureAhead += 1
                self.violations += 1
            if record.status == "READY" and (self.readyAt is None or stamp < self.readyAt):
                # When indicators first matured anywhere in the run, recorded as it happens so
                # the answer does not depend on how long a diagnostic tail happens to be.
                self.readyAt = stamp
            return
        if category == "ensembles" and isinstance(record, EnsembleSnapshot):
            self.ensembles = (
                record.asOf if self.ensembles is None else max(self.ensembles, record.asOf)
            )
            if record.asOf > now:
                self.ensembleAhead += 1
                self.violations += 1
            return
        if category == "opportunity_boards" and isinstance(record, OpportunityBoard):
            self.boards = record.asOf if self.boards is None else max(self.boards, record.asOf)
            if record.asOf > now:
                self.boardAhead += 1
                self.violations += 1
            return
        if category == "paper_trades" and isinstance(record, PaperTrade):
            self._trade(record, now)

    def _trade(self, trade: PaperTrade, now: int) -> None:
        if trade.entryTime is not None:
            lag = trade.entryTime - trade.decisionAvailableAt
            if lag < 0:
                self.entryBefore += 1
                self.violations += 1
            self.maxEntryLag = lag if self.maxEntryLag is None else max(self.maxEntryLag, lag)
            if trade.entryTime > now:
                self.violations += 1
        if trade.expiryTime is not None and trade.expiryTargetTime is not None:
            lag = trade.expiryTime - trade.expiryTargetTime
            if lag < 0:
                self.expiryBefore += 1
                self.violations += 1
            self.maxExpiryLag = lag if self.maxExpiryLag is None else max(self.maxExpiryLag, lag)
            if trade.expiryTime > now:
                self.violations += 1

    def report(self, latest_input: int | None) -> ReplayCausalityAudit:
        return ReplayCausalityAudit(
            checks=self.checks,
            violations=self.violations,
            latestInputTime=latest_input,
            latestFeatureAsOf=self.features,
            latestEnsembleAsOf=self.ensembles,
            latestBoardAsOf=self.boards,
            currentMarketTime=self.clock.now,
            featureAheadOfClock=self.featureAhead,
            ensembleAheadOfClock=self.ensembleAhead,
            boardAheadOfClock=self.boardAhead,
            entryBeforeDecision=self.entryBefore,
            expiryBeforeTarget=self.expiryBefore,
            maxEntryLagMs=self.maxEntryLag,
            maxExpiryLagMs=self.maxExpiryLag,
        )


class ReplayStorage(ParquetStorage):
    """The run's own sink: evidence to disk, counters in memory, nothing to the live record.

    A subclass rather than a wrapper so the isolated ``MarketEngine`` is handed exactly the type
    it expects and no call site anywhere has to know a replay is running. It refuses to hydrate
    warm-up candles from stored history on purpose: a replay's warm-up must come from the events
    the replay actually fed, or the first evaluated decision would rest on bars the run never
    saw.
    """

    def __init__(
        self,
        root: Path,
        clock: ReplayClock,
        *,
        persist: bool = True,
        capture: frozenset[str] = frozenset(),
    ) -> None:
        super().__init__(root)
        self.audit = _Audit(clock)
        self.persist = persist
        self.capture = capture
        """Categories to keep in memory in full, for a caller that wants to compare the whole
        derived series rather than the evidence. Off by default and never enabled by a run the
        operator started: keeping every feature snapshot of a long replay would cost more memory
        than the replay itself."""
        self.captured: dict[str, list[Record]] = {}
        self.counts: dict[str, int] = {}
        self.trades: deque[PaperTrade] = deque(maxlen=TRADE_ROW_MEMORY)
        self.evaluations: deque[StrategyEvaluation] = deque(maxlen=EVALUATION_MEMORY)
        self.evaluationRows = 0
        self.boards: dict[Platform, dict[str, int]] = {}
        self.sessions: deque[DailySession] = deque(maxlen=SESSION_ROW_MEMORY)
        self.ensemblesByPlatform: dict[Platform, int] = {}
        self.regimes: dict[str, int] = {}
        """Every regime the replayed history was classified into, not only the ones that led to
        a trade. A backtest that only counted the regimes it traded in could never show that it
        never saw one."""

    def append(self, category: Category, record: Record) -> None:
        self.audit.observe(category, record)
        self.counts[category] = self.counts.get(category, 0) + 1
        if category in self.capture:
            self.captured.setdefault(category, []).append(record)
        if category == "paper_trades" and isinstance(record, PaperTrade):
            self.trades.append(record)
        elif category == "strategy_evaluations" and isinstance(record, StrategyEvaluation):
            self.evaluationRows += 1
            self.evaluations.append(record)
        elif category == "ensembles" and isinstance(record, EnsembleSnapshot):
            self.ensemblesByPlatform[record.platform] = (
                self.ensemblesByPlatform.get(record.platform, 0) + 1
            )
        elif category == "daily_sessions" and isinstance(record, DailySession):
            self.sessions.append(record)
        elif category == "regimes" and isinstance(record, RegimeSnapshot):
            self.regimes[record.primaryRegime] = self.regimes.get(record.primaryRegime, 0) + 1
        elif category == "opportunity_boards" and isinstance(record, OpportunityBoard):
            tally = self.boards.setdefault(record.platform, {})
            tally[record.status] = tally.get(record.status, 0) + 1
            if record.selectedSlotId is not None:
                tally["SELECTED"] = tally.get("SELECTED", 0) + 1
        if self.persist and category in RETAINED:
            super().append(category, record)

    def flush(self) -> None:
        if self.persist:
            super().flush()
        else:
            self.pending.clear()

    def load_history(
        self,
        platform: str,
        asset_name: str,
        timeframe: Timeframe,
        *,
        before: int,
        limit: int = 0,
    ) -> list[Candle]:
        """No cross-run hydration, ever. A replay's warm-up is the events it actually fed.

        The live engine seeds a fresh series from trusted stored candles so a restart does not
        start blind. A replay must not: bars from another run — or worse, from the live record —
        would put history into an evaluation that never replayed it, and the first decisions of
        the window would rest on data the run cannot show you.
        """
        return []

    @property
    def truncated(self) -> bool:
        return self.evaluationRows > EVALUATION_MEMORY


@dataclass(slots=True)
class ReplayProgress:
    """Runtime-only progress. Nothing here is read by anything that reaches a number."""

    status: ReplayRunStatus = "PENDING"
    cancelled: bool = False
    totalEvents: int = 0
    processedEvents: int = 0
    acceptedEvents: int = 0
    warmupEvents: int = 0
    currentMarketTime: int | None = None
    startedRuntimeTime: int | None = None
    finishedRuntimeTime: int | None = None
    error: str | None = None

    @property
    def percent(self) -> float | None:
        if not self.totalEvents:
            return None
        return min(1.0, self.processedEvents / self.totalEvents)


@dataclass(frozen=True, slots=True)
class ReplayWindow:
    """The three boundaries a replay is defined by, plus the tail that finishes it."""

    warmupStart: int
    evaluationStart: int
    evaluationEnd: int
    settlementEnd: int
    warmupDurationMs: int
    settlementTailMs: int

    @property
    def empty(self) -> bool:
        return self.evaluationEnd <= self.evaluationStart


@dataclass(slots=True)
class ReplayResult:
    """Everything one replay produced, before any of it is described or persisted."""

    run: ReplayRun
    manifest: ReplayManifest
    dataset: ReplayDatasetSummary
    window: ReplayWindow
    causality: ReplayCausalityAudit
    snapshot: AnalyticsSnapshot | None = None
    analysis: AnalyticsDataset | None = None
    trades: tuple[PaperTrade, ...] = ()
    boards: dict[Platform, dict[str, int]] = field(default_factory=dict)
    ensembles: int = 0
    ensemblesByPlatform: dict[Platform, int] = field(default_factory=dict)
    sessions: tuple[DailySession, ...] = ()
    regimes: dict[str, int] = field(default_factory=dict)
    hourBuckets: frozenset[int] = frozenset()
    guard: SessionGuard | None = None
    truncated: bool = False


def settings_fingerprint(manifest: ReplayManifest) -> str:
    """A content hash of everything that could change a result, and nothing that could not."""
    payload = manifest.model_dump(mode="json")
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def replay_run_id(
    *,
    input_fingerprint: str,
    settings_hash: str,
    evaluation_start: int,
    evaluation_end: int,
    platforms: Sequence[Platform],
) -> UUID:
    """Deterministic identity: the same history, settings and window are the same run.

    UUID5 over the content rather than uuid4, and never over a modification time: a run id that
    changed when the file was copied, or stayed put when an event was corrected, would identify
    the disk rather than the evidence.
    """
    key = "|".join(
        (
            REPLAY_VERSION,
            input_fingerprint,
            settings_hash,
            str(evaluation_start),
            str(evaluation_end),
            ",".join(sorted(platforms)),
        )
    )
    return uuid5(REPLAY_NAMESPACE, key)


def derive_window(manifest: ReplayManifest, dataset: ReplayDatasetSummary) -> ReplayWindow:
    """Where warm-up, evaluation and settlement begin and end, in canonical market time.

    With no explicit window the record's own head funds the warm-up and evaluation begins once
    the indicators could have matured; an explicit ``fromTime`` is taken as the evaluation start
    and the warm-up is read from before it. Either way the settlement tail is added afterwards,
    because a selection made inside the window is evidence about the window however long the
    market takes to answer it.
    """
    platforms = manifest.platforms
    warmup = (
        warmup_duration_ms(platforms)
        if manifest.warmupDurationMs is None
        else manifest.warmupDurationMs
    )
    tail = settlement_tail_ms(platforms, manifest.paperSettings)
    start = dataset.startTime if dataset.startTime is not None else 0
    end = dataset.endTime if dataset.endTime is not None else start
    evaluation_start = start + warmup if manifest.fromTime is None else manifest.fromTime
    evaluation_end = end if manifest.toTime is None else manifest.toTime
    return ReplayWindow(
        warmupStart=evaluation_start - warmup,
        evaluationStart=evaluation_start,
        evaluationEnd=evaluation_end,
        settlementEnd=evaluation_end + tail,
        warmupDurationMs=warmup,
        settlementTailMs=tail,
    )


class ReplayEngine:
    """One replay of one manifest over one source, into one isolated analytical pipeline."""

    def __init__(
        self,
        manifest: ReplayManifest,
        source: ReplaySource,
        *,
        root: Path,
        persist: bool = True,
        cancelled: Callable[[], bool] | None = None,
        decision_delay_ms: int = 0,
        batch_size: int = 4_096,
        capture: frozenset[str] = frozenset(),
    ) -> None:
        self.manifest = manifest
        self.source = source
        self.root = root
        self.persist = persist
        self.cancelled = cancelled
        self.decisionDelayMs = decision_delay_ms
        self.batchSize = batch_size
        self.captureCategories = capture
        self.clock = ReplayClock()
        self.progress = ReplayProgress()
        self.engine: MarketEngine | None = None
        """The isolated analytical engine this run built, kept after the run so a test can look
        at the feature, strategy and ranking state the replay actually left behind."""
        self.storage: ReplayStorage | None = None
        self.hourBuckets: set[int] = set()
        """Whole hours of market time the evaluation window actually received data in. Bounded
        by the span of the record, and the only way to say whether a hundred thousand events
        were a week of history or one afternoon of it."""

    # --- run ---------------------------------------------------------------------------

    def run(self) -> ReplayResult:
        """Replay the whole window and its settlement tail, then analyse what came out."""
        self.progress.startedRuntimeTime = int(time.time() * 1000)
        self.progress.status = "RUNNING"
        dataset = self.source.prepare()
        window = derive_window(self.manifest, dataset)
        fingerprint = settings_fingerprint(self.manifest)
        run_id = replay_run_id(
            input_fingerprint=dataset.inputFingerprint,
            settings_hash=fingerprint,
            evaluation_start=window.evaluationStart,
            evaluation_end=window.evaluationEnd,
            platforms=self.manifest.platforms,
        )
        self.progress.totalEvents = dataset.events
        storage = ReplayStorage(
            self.root / str(run_id) / "market",
            self.clock,
            persist=self.persist,
            capture=self.captureCategories,
        )
        engine = self._engine(storage, window)
        self.engine = engine
        self.storage = storage
        status: ReplayRunStatus = "COMPLETED"
        error: str | None = None
        try:
            self._feed(engine, window)
            if self.progress.cancelled:
                status = "CANCELLED"
        except Exception as failure:  # reported as FAILED, never presented as a result
            status = "FAILED"
            error = f"{type(failure).__name__}: {failure}"[:300]
        try:
            storage.flush()
        except Exception as failure:
            if status == "COMPLETED":
                status = "FAILED"
                error = f"Replay storage unavailable: {failure}"[:300]
        self.progress.status = status
        self.progress.finishedRuntimeTime = int(time.time() * 1000)
        snapshot, analysis = self._analyse(engine, storage, status)
        run = self._run_model(
            run_id=run_id,
            dataset=dataset,
            window=window,
            fingerprint=fingerprint,
            storage=storage,
            engine=engine,
            status=status,
            error=error,
        )
        return ReplayResult(
            run=run,
            manifest=self.manifest,
            dataset=dataset.model_copy(
                update={
                    "diagnostics": dataset.diagnostics.model_copy(
                        update={"outsideWindow": self.source.outsideWindow}
                    )
                }
            ),
            window=window,
            causality=storage.audit.report(self.clock.now),
            snapshot=snapshot,
            analysis=analysis,
            trades=tuple(storage.trades),
            boards=storage.boards,
            ensembles=storage.counts.get("ensembles", 0),
            ensemblesByPlatform=dict(storage.ensemblesByPlatform),
            sessions=tuple(storage.sessions),
            regimes=dict(storage.regimes),
            hourBuckets=frozenset(self.hourBuckets),
            guard=engine.guard,
            truncated=storage.truncated,
        )

    # --- pipeline ----------------------------------------------------------------------

    def _engine(self, storage: ReplayStorage, window: ReplayWindow) -> MarketEngine:
        """An isolated analytical engine. The live one is never touched, read or constructed."""
        guard = self.manifest.sessionGuardScenario
        engine = MarketEngine(
            storage,
            paper=self.manifest.paperSettings,
            guard=guard if guard is not None else SessionGuardSettings(),
        )
        engine.paper = WindowedPaperEngine(
            self.manifest.paperSettings,
            opens_from=window.evaluationStart,
            opens_until=window.evaluationEnd,
            decision_delay_ms=self.decisionDelayMs,
            permits=self._permits(engine) if guard is not None else None,
        )
        return engine

    @staticmethod
    def _permits(engine: MarketEngine) -> Callable[[], bool]:
        """The sandbox guard's own permission, read from the run's own guard instance.

        The live Phase 9.5 guard is a different object owned by a different engine in a
        different process state. Nothing here can reach it, and nothing here writes a session,
        a daily total, a target, a limit or a lock anywhere outside this run's namespace.
        """

        def allowed() -> bool:
            session = engine.guard.current
            return True if session is None else session.canOpenNewEntry

        return allowed

    def _feed(self, engine: MarketEngine, window: ReplayWindow) -> None:
        """Every historical event, in canonical order, one at a time.

        One event per ``ingest`` call and one watermark advance per event, so the semantic
        result cannot depend on how the source happened to chunk its batches: a run fed one
        event at a time and the same run fed a hundred at a time do exactly the same work in
        exactly the same order.
        """
        processed = 0
        for batch in self.source.stream(
            start=window.warmupStart, end=window.settlementEnd, batch_size=self.batchSize
        ):
            for event in batch:
                if (
                    self.cancelled is not None
                    and processed % CANCEL_INTERVAL == 0
                    and self.cancelled()
                ):
                    self.progress.cancelled = True
                    self.progress.status = "CANCELLED"
                    return
                self.clock.advance(event.marketTime)
                self._tick(engine)
                accepted = engine.ingest(
                    ObservationBatch(observations=[event.observation]), event.marketTime
                )
                processed += 1
                self.progress.acceptedEvents += accepted
                if event.marketTime < window.evaluationStart:
                    self.progress.warmupEvents += 1
                elif event.marketTime <= window.evaluationEnd:
                    self.hourBuckets.add(event.marketTime // 3_600_000)
                if processed % PROGRESS_INTERVAL == 0:
                    self.progress.processedEvents = processed
                    self.progress.currentMarketTime = self.clock.now
        self.progress.processedEvents = processed
        self.progress.currentMarketTime = self.clock.now
        self._tick(engine)

    def _tick(self, engine: MarketEngine) -> None:
        """Advance every series to the availability watermark the replayed clock has reached.

        The live engine does exactly this on its background maintenance pass, from its own
        clock; a replay does it from the record's. Knowing that market time has reached *T* is
        knowledge available at *T*, so a quiet slot's bar closes at the point in the series it
        would have closed at live instead of whenever that slot next happens to report.
        """
        watermark = self.clock.watermark
        if watermark is None:
            return
        for builder in engine.builders.values():
            builder.advance(watermark)
            engine.persist_events(builder)

    # --- results -----------------------------------------------------------------------

    def _analyse(
        self, engine: MarketEngine, storage: ReplayStorage, status: ReplayRunStatus
    ) -> tuple[AnalyticsSnapshot | None, AnalyticsDataset | None]:
        """Hand the run's own outcomes to ``qst-analytics-v1``. No second calculator exists.

        The dataset and the snapshot are built by ``qst-analytics-v1`` itself, from the run's
        own trades and its own strategy votes. Nothing reads the live durable record, and the
        live engine's cached analysis is neither refreshed nor replaced — a replay that
        recomputed the operator's panel would be a replay that changed what they are looking at.
        """
        del engine
        if status == "FAILED":
            return (None, None)
        trades: Iterable[PaperTrade] = tuple(storage.trades)
        evaluations = tuple(storage.evaluations)
        settings = self.manifest.analyticsSettings
        analysis = build(trades, evaluations, settings=settings)
        return (AnalyticsEngine(settings).analyze(analysis), analysis)

    def _run_model(
        self,
        *,
        run_id: UUID,
        dataset: ReplayDatasetSummary,
        window: ReplayWindow,
        fingerprint: str,
        storage: ReplayStorage,
        engine: MarketEngine,
        status: ReplayRunStatus,
        error: str | None,
    ) -> ReplayRun:
        started = self.progress.startedRuntimeTime
        finished = self.progress.finishedRuntimeTime
        runtime = None if started is None or finished is None else max(0, finished - started)
        paper = engine.paper.state()
        selected = sum(tally.get("SELECTED", 0) for tally in storage.boards.values())
        return ReplayRun(
            replayRunId=run_id,
            status=status,
            createdAt=started if started is not None else 0,
            inputFingerprint=dataset.inputFingerprint,
            settingsFingerprint=fingerprint,
            platforms=list(self.manifest.platforms),
            sourceType=dataset.sourceType,
            sourcePathLabel=dataset.sourcePathLabel,
            entryLayer=REPLAY_ENTRY_LAYER,
            sourceMode=dataset.sourceMode,
            warmupStart=window.warmupStart,
            evaluationStart=window.evaluationStart,
            evaluationEnd=window.evaluationEnd,
            settlementEnd=window.settlementEnd,
            warmupDurationMs=window.warmupDurationMs,
            settlementTailMs=window.settlementTailMs,
            totalEvents=dataset.events,
            processedEvents=self.progress.processedEvents,
            acceptedEvents=self.progress.acceptedEvents,
            rejectedEvents=max(0, self.progress.processedEvents - self.progress.acceptedEvents),
            warmupEvents=self.progress.warmupEvents,
            featuresReadyAt=storage.audit.readyAt,
            ensemblesProduced=storage.counts.get("ensembles", 0),
            boardsFinalized=storage.counts.get("opportunity_boards", 0),
            boardsSelected=selected,
            paperOpened=len({trade.paperTradeId for trade in storage.trades}),
            paperResolved=paper.resolved,
            paperInvalid=paper.invalid,
            paperCancelled=paper.cancelled,
            startedMarketTime=self.clock.started,
            finishedMarketTime=self.clock.now,
            startedRuntimeTime=started,
            finishedRuntimeTime=finished,
            runtimeMs=runtime,
            eventsPerSecond=(
                self.progress.processedEvents / (runtime / 1000)
                if runtime is not None and runtime > 0
                else None
            ),
            error=error,
        )
