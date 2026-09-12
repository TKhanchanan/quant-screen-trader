"""Deterministic replay and backtest wire models (Phase 11).

Phase 11 answers one question the live pipeline cannot: *if the current frozen analytical
contracts had been running over a historical market record, in chronological order, what would
they have decided and what would those decisions have produced?*

It is an **input driver**, not a second brain. Every number a replay reports was produced by the
same Phase 5-10 code the live engine runs; this package supplies historical events, a market
clock and an evaluation window, and records what came out. There is no feature formula, no
regime rule, no strategy, no rank score and no outcome rule anywhere in it, and a test asserts
that it imports the production engines rather than restating them.

It is also **offline**. Nothing here reaches a broker control, an order, an execution mode, an
arm state or a live session limit — the replay owns an isolated analytical engine and an
isolated storage namespace, and a test asserts the package cannot even name an execution
surface. A replay result is research: it is persisted, and no code in this application reads it
back to change a decision.
"""

from __future__ import annotations

from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from quant_engine.analytics.models import AnalyticsSettings
from quant_engine.configuration import Model, Platform
from quant_engine.market_models import QualityState, SourceType
from quant_engine.paper.policy import PaperSettings
from quant_engine.session_guard.settings import SessionGuardSettings
from quant_engine.strategy.models import Regime

REPLAY_VERSION = "qst-replay-v1"
"""Source contract, event ordering, clock semantics, warm-up derivation, evaluation window,
settlement tail, walk-forward construction and purge/embargo rules. Any change to how a replay
reaches a number requires a new string, so two replay results can never be compared as if they
had been produced the same way."""

SUPPORTED_FEATURE_VERSION = "qfe-v2"
SUPPORTED_REGIME_VERSION = "qst-regime-v1"
SUPPORTED_STRATEGY_VERSION = "qst-strategy-v1"
SUPPORTED_RANKING_VERSION = "qst-ranking-v1"
SUPPORTED_PAPER_VERSION = "qst-paper-v1"
SUPPORTED_ANALYTICS_VERSION = "qst-analytics-v1"
"""Deliberately literals, not imports of the Phase 6-10 constants.

A replay result is evidence about one exact set of upstream contracts. Tracking the upstream
constants would let a later contract silently pool new replays with old ones and call the
mixture a backtest; a literal makes the mismatch arrive as an explicit refusal instead."""

MAX_WARNINGS = 32
MAX_ROWS = 512
MAX_FOLDS = 64
MAX_SCENARIOS = 16

type ReplayRunStatus = Literal["PENDING", "RUNNING", "COMPLETED", "CANCELLED", "FAILED"]
"""PENDING: accepted, not started. RUNNING: feeding events. COMPLETED: the whole window and its
settlement tail were replayed. CANCELLED: stopped on request, with progress kept. FAILED: the
replay could not finish, and no partial result is presented as a completed one."""

type ReplaySourceType = Literal["PARQUET_OBSERVATIONS", "IN_MEMORY_OBSERVATIONS"]

type ReplayEntryLayer = Literal["PHASE4_MARKET_OBSERVATION", "PHASE5_CANONICAL_SAMPLE"]
"""Which durable layer a replay actually begins from.

``PHASE4_MARKET_OBSERVATION`` is the raw accepted-or-rejected observation, so Phase 5's own
selection rules are re-tested by the replay. ``PHASE5_CANONICAL_SAMPLE`` would start after that
gate, and a replay that started there could not claim to have re-tested it. The layer is on the
record because the difference is the difference between two honest but unequal claims."""

type ReplaySourceMode = Literal["REPLAY", "SYNTHETIC"]
"""Real recorded history replayed, or a constructed fixture. Never mixed inside one run: a
synthetic behaviour test is not backtest performance, and a paper trade whose entry and expiry
came from two different worlds would be neither."""

type StabilityVerdict = Literal["STABLE", "UNSTABLE", "UNTESTED"]

WARNING_CODES = (
    "INSUFFICIENT_HISTORY",
    "LOW_RESOLUTION_RATE",
    "LOW_SELECTION_COUNT",
    "LOW_ASSET_SAMPLE",
    "SINGLE_REGIME_DOMINANCE",
    "LIMITED_REGIME_COVERAGE",
    "NARROW_TIME_COVERAGE",
    "ASSET_CONCENTRATION_WARNING",
    "HIGH_GAP_RATE",
    "MIXED_CURRENCY",
    "MONETARY_UNVERIFIED",
    "PAPER_ACCOUNTING_UNAVAILABLE",
    "MULTIPLE_TESTING_WARNING",
    "OUT_OF_SAMPLE_DEGRADATION",
    "PARAMETER_INSTABILITY",
    "INSUFFICIENT_FOLDS",
    "NO_STABLE_CANDIDATE",
    "SINGLE_PLATFORM",
    "STRATEGY_EVIDENCE_TRUNCATED",
    "SYNTHETIC_BEHAVIOR_TEST",
    "MALFORMED_INPUT_ROWS",
    "DUPLICATE_INPUT_ROWS",
)
"""Every caveat a replay can raise about itself. ``SYNTHETIC_BEHAVIOR_TEST`` is raised for every
synthetic run and never removed: a synthetic fixture proves software behaviour and is not
evidence about any market."""

type Code = str

MIN_SELECTION_SAMPLE = 50
"""Below this a replay is never described as evidence of performance. The same floor Phase 10
uses to refuse a threshold recommendation, restated here because a backtest is exactly the
place where a thin sample is most likely to be read as a result."""

MIN_TRADING_DATES = 5
MIN_HOURS_COVERED = 6
"""A hundred thousand events from one hour of one afternoon is not broad history."""

ASSET_CONCENTRATION_SHARE = 0.5
REGIME_DOMINANCE_SHARE = 0.9
HIGH_GAP_SHARE = 0.25


class WalkForwardSettings(Model):
    """How chronological folds are cut. Nothing here is searched or tuned.

    Two modes, both strictly chronological and neither shuffled. ``DURATION`` cuts rolling
    windows out of market time, which is the honest shape when there is enough history for it.
    ``COUNT`` cuts the ordered outcomes into equal contiguous segments, which is what a short
    record leaves available — it is a weaker statement and is labelled as one.
    """

    enabled: bool = True
    mode: Literal["COUNT", "DURATION"] = "COUNT"

    foldCount: int = Field(default=3, ge=1, le=MAX_FOLDS, strict=True)
    trainSegments: int = Field(default=3, ge=1, le=12, strict=True)
    """How many contiguous segments TRAIN spans in ``COUNT`` mode. VALIDATION and TEST are one
    segment each and always follow it, so fold *i* reads segments ``i .. i+trainSegments+1``
    and no fold can see a segment a later fold owns."""

    trainingDurationMs: int = Field(default=7 * 86_400_000, gt=0, le=365 * 86_400_000, strict=True)
    validationDurationMs: int = Field(default=86_400_000, gt=0, le=365 * 86_400_000, strict=True)
    testDurationMs: int = Field(default=86_400_000, gt=0, le=365 * 86_400_000, strict=True)
    stepDurationMs: int = Field(default=86_400_000, gt=0, le=365 * 86_400_000, strict=True)

    embargoMs: int | None = Field(default=None, ge=0, le=86_400_000, strict=True)
    """Gap inserted at every window boundary, and the purge width applied inside it. ``None``
    derives it from ``qst-paper-v1``: the longest a selection can stay alive is its maximum
    entry delay plus its horizon plus its maximum resolution lag, so nothing shorter than that
    can guarantee a trade does not straddle a boundary."""


class ReplaySourceFilter(Model):
    """A bounded read filter over the historical source. Field names only, no expressions."""

    assetNames: list[str] = Field(default_factory=list, max_length=32)
    slotIds: list[int] = Field(default_factory=list, max_length=9)
    contextIds: list[UUID] = Field(default_factory=list, max_length=64)

    @property
    def empty(self) -> bool:
        return not (self.assetNames or self.slotIds or self.contextIds)


class ReplayManifest(Model):
    """Everything a replay run is. Two identical manifests over identical history are one run.

    What is deliberately absent is as much of the contract as what is present: there is no
    credential, no browser session, no window identity, no control coordinate, no execution mode
    and no arm state anywhere in this model, and a test asserts the field set. A replay is
    offline compute over a recorded file, and a manifest that could name a broker control would
    be a manifest that could reach one.
    """

    includeCapitalBear: bool = True
    includeIqOption: bool = True
    """The platform selection, as two flags rather than a list: a list can contain a duplicate
    or an empty set that means two different things, and ``platforms`` derives the answer."""

    fromTime: int | None = Field(default=None, strict=True)
    toTime: int | None = Field(default=None, strict=True)
    """Evaluation window in canonical market time. ``None`` takes the earliest and latest the
    source actually contains, which is the only honest default for a record whose extent is a
    property of the record."""

    warmupDurationMs: int | None = Field(default=None, ge=0, le=30 * 86_400_000, strict=True)
    """``None`` derives it from ``qfe-v2``: fifty closed bars — the bar count below which
    nothing in the catalog reports READY — of the slowest timeframe each selected platform
    actually reads. Never a guessed small number."""

    sourceType: ReplaySourceType = "PARQUET_OBSERVATIONS"
    sourceMode: ReplaySourceMode = "REPLAY"
    sourcePathLabel: str = Field(default="", max_length=120)
    """A stable label for the source, never a machine-specific absolute path: a run id that
    moved when the record was copied to another disk would not be a run id."""

    sourceFilter: ReplaySourceFilter = Field(default_factory=ReplaySourceFilter)

    paperSettings: PaperSettings = Field(default_factory=PaperSettings)
    analyticsSettings: AnalyticsSettings = Field(default_factory=AnalyticsSettings)
    walkForward: WalkForwardSettings = Field(default_factory=WalkForwardSettings)

    latencyScenarios: list[int] = Field(default_factory=list, max_length=MAX_SCENARIOS)
    """Extra decision delays in milliseconds, evaluated as separate research replays. The
    baseline is always replayed at zero and is never replaced by one of these."""

    payoutScenarios: list[float] = Field(default_factory=list, max_length=MAX_SCENARIOS)
    """Fixed payout rates to re-price already-resolved outcomes against. Research only, and
    only when an explicit stake was configured: no broker payout is ever inferred."""

    paperStartingCapital: float | None = Field(default=None, gt=0, le=1_000_000_000)
    """An explicitly stated simulated starting balance, used for nothing but expressing a
    simulated drawdown as a fraction. There is no default and no inference: without one the
    relative drawdown is reported as unknown rather than measured against an invented account."""

    sessionGuardScenario: SessionGuardSettings | None = None
    """Optional sandbox only. The live Phase 9.5 guard is a different object in a different
    process state and is never constructed, read or written by a replay."""

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if not (self.includeCapitalBear or self.includeIqOption):
            raise ValueError("At least one platform is required")
        if self.fromTime is not None and self.toTime is not None and self.toTime <= self.fromTime:
            raise ValueError("Evaluation window must end after it starts")
        if any(delay < 0 or delay > 600_000 for delay in self.latencyScenarios):
            raise ValueError("Latency scenarios must be between 0 and 600000 ms")
        if len(set(self.latencyScenarios)) != len(self.latencyScenarios):
            raise ValueError("Latency scenarios must be unique")
        if any(rate < 0 or rate > 10 for rate in self.payoutScenarios):
            raise ValueError("Payout scenarios must be between 0 and 10")
        if len(set(self.payoutScenarios)) != len(self.payoutScenarios):
            raise ValueError("Payout scenarios must be unique")
        return self

    @property
    def platforms(self) -> tuple[Platform, ...]:
        selected: list[Platform] = []
        if self.includeCapitalBear:
            selected.append("capitalbear")
        if self.includeIqOption:
            selected.append("iqoption")
        return tuple(selected)


class ReplayDiagnostics(Model):
    """What the historical record itself turned out to contain.

    Reported before any performance number. A duplicate row, a row stored out of order and a
    row that could not be parsed are all facts about the record rather than about the market,
    and a backtest that hid them would be describing a dataset nobody could check.
    """

    rowsRead: int = Field(ge=0)
    accepted: int = Field(ge=0)
    duplicates: int = Field(ge=0)
    """Rows repeating an observation id already seen. Kept once, counted here."""
    identityCollisions: int = Field(ge=0)
    """Distinct ids claiming the same platform, slot, context and market instant."""
    outOfOrder: int = Field(ge=0)
    """Rows whose stored position was later than a row with an earlier market time. The Parquet
    record is an explicitly unordered durable set, so this is a diagnostic and not a fault; it
    is reported because a non-zero count is exactly why canonical sorting exists."""
    malformed: int = Field(ge=0)
    """Rows that could not be validated as a ``MarketObservation``. Skipped, never repaired: a
    price that cannot be read is not a price of zero."""
    filtered: int = Field(ge=0)
    outsideWindow: int = Field(ge=0)


class ReplayDatasetSummary(Model):
    """The input dataset, stated in full before any result built on it."""

    entryLayer: ReplayEntryLayer
    sourceType: ReplaySourceType
    sourceMode: ReplaySourceMode
    sourcePathLabel: str = Field(default="", max_length=120)
    inputFingerprint: str = Field(min_length=1, max_length=64)

    events: int = Field(ge=0)
    startTime: int | None = Field(default=None, strict=True)
    endTime: int | None = Field(default=None, strict=True)
    durationMs: int = Field(ge=0)

    platforms: list[Platform] = Field(default_factory=list, max_length=2)
    assetsSeen: int = Field(ge=0)
    contextsSeen: int = Field(ge=0)
    slotsSeen: int = Field(ge=0)
    assetNames: list[str] = Field(default_factory=list, max_length=64)

    qualityCounts: dict[QualityState, int] = Field(default_factory=dict)
    originSourceCounts: dict[SourceType, int] = Field(default_factory=dict)
    """Provenance as it was recorded. The replayed rows themselves carry REPLAY or SYNTHETIC so
    they can never masquerade as a live DOM read, and this is where the original is kept."""

    gapSeconds: int = Field(ge=0)
    """Whole seconds with no accepted observation inside an otherwise live context. Gaps stay
    gaps: nothing here interpolates a price or manufactures a candle."""
    coveredSeconds: int = Field(ge=0)
    gapShare: float | None = Field(default=None, ge=0, le=1)

    diagnostics: ReplayDiagnostics


class ReplayCausalityAudit(Model):
    """Evidence that no decision used information that did not exist yet.

    Every field is a maximum observed over the whole run, and every assertion is an inequality
    that must hold at every step. A replay that could not produce this audit would be a replay
    whose no-lookahead claim was prose.
    """

    checks: int = Field(ge=0)
    violations: int = Field(ge=0)

    latestInputTime: int | None = Field(default=None, strict=True)
    latestFeatureAsOf: int | None = Field(default=None, strict=True)
    latestEnsembleAsOf: int | None = Field(default=None, strict=True)
    latestBoardAsOf: int | None = Field(default=None, strict=True)
    currentMarketTime: int | None = Field(default=None, strict=True)

    featureAheadOfClock: int = Field(ge=0)
    ensembleAheadOfClock: int = Field(ge=0)
    boardAheadOfClock: int = Field(ge=0)
    entryBeforeDecision: int = Field(ge=0)
    expiryBeforeTarget: int = Field(ge=0)
    maxEntryLagMs: int | None = None
    maxExpiryLagMs: int | None = None

    @property
    def clean(self) -> bool:
        return self.violations == 0


class ReplayRun(Model):
    """One replay, its window, its counters and its outcome. Identified deterministically."""

    replayRunId: UUID
    replayVersion: str = Field(default=REPLAY_VERSION, min_length=1, max_length=40)
    status: ReplayRunStatus

    createdAt: int = Field(strict=True)
    """Wall clock, and the only wall clock on the record. It orders runs in a list and is used
    by nothing that reaches a number."""

    inputFingerprint: str = Field(min_length=1, max_length=64)
    settingsFingerprint: str = Field(min_length=1, max_length=64)

    platforms: list[Platform] = Field(default_factory=list, max_length=2)
    sourceType: ReplaySourceType
    sourcePathLabel: str = Field(default="", max_length=120)
    entryLayer: ReplayEntryLayer
    sourceMode: ReplaySourceMode

    warmupStart: int | None = Field(default=None, strict=True)
    evaluationStart: int | None = Field(default=None, strict=True)
    evaluationEnd: int | None = Field(default=None, strict=True)
    settlementEnd: int | None = Field(default=None, strict=True)
    warmupDurationMs: int = Field(default=0, ge=0)
    settlementTailMs: int = Field(default=0, ge=0)

    totalEvents: int = Field(default=0, ge=0)
    processedEvents: int = Field(default=0, ge=0)
    acceptedEvents: int = Field(default=0, ge=0)
    rejectedEvents: int = Field(default=0, ge=0)
    warmupEvents: int = Field(default=0, ge=0)

    featuresReadyAt: int | None = Field(default=None, strict=True)
    ensemblesProduced: int = Field(default=0, ge=0)
    boardsFinalized: int = Field(default=0, ge=0)
    boardsSelected: int = Field(default=0, ge=0)

    paperOpened: int = Field(default=0, ge=0)
    paperResolved: int = Field(default=0, ge=0)
    paperInvalid: int = Field(default=0, ge=0)
    paperCancelled: int = Field(default=0, ge=0)

    startedMarketTime: int | None = Field(default=None, strict=True)
    finishedMarketTime: int | None = Field(default=None, strict=True)
    startedRuntimeTime: int | None = Field(default=None, strict=True)
    finishedRuntimeTime: int | None = Field(default=None, strict=True)
    runtimeMs: int | None = Field(default=None, ge=0)
    eventsPerSecond: float | None = Field(default=None, ge=0)

    error: str | None = Field(default=None, max_length=300)

    featureVersion: str = Field(default=SUPPORTED_FEATURE_VERSION, min_length=1, max_length=40)
    regimeVersion: str = Field(default=SUPPORTED_REGIME_VERSION, min_length=1, max_length=40)
    strategyVersion: str = Field(default=SUPPORTED_STRATEGY_VERSION, min_length=1, max_length=40)
    rankingVersion: str = Field(default=SUPPORTED_RANKING_VERSION, min_length=1, max_length=40)
    paperVersion: str = Field(default=SUPPORTED_PAPER_VERSION, min_length=1, max_length=40)
    analyticsVersion: str = Field(default=SUPPORTED_ANALYTICS_VERSION, min_length=1, max_length=40)

    researchOnly: Literal[True] = True
    appliedToLiveExecution: Literal[False] = False

    @property
    def percent(self) -> float | None:
        if not self.totalEvents:
            return None
        return min(1.0, self.processedEvents / self.totalEvents)


class OutcomeTally(Model):
    """One platform's directional result. Never pooled across platforms without saying so."""

    platform: Platform | None = None
    """``None`` is the combined view, and only ever appears beside the per-platform ones."""
    durationMs: int = Field(default=0, ge=0)
    """The paper horizon this platform's outcomes were measured over, so two different
    questions can never be read as one number."""

    ensembles: int = Field(default=0, ge=0)
    boardsFinalized: int = Field(default=0, ge=0)
    boardsSelected: int = Field(default=0, ge=0)
    boardsNoOpportunity: int = Field(default=0, ge=0)
    boardsPartial: int = Field(default=0, ge=0)
    selectionCoverage: float | None = Field(default=None, ge=0, le=1)

    trades: int = Field(default=0, ge=0)
    resolved: int = Field(default=0, ge=0)
    wins: int = Field(default=0, ge=0)
    losses: int = Field(default=0, ge=0)
    draws: int = Field(default=0, ge=0)
    invalid: int = Field(default=0, ge=0)
    cancelled: int = Field(default=0, ge=0)

    winRateExcludingDraws: float | None = Field(default=None, ge=0, le=1)
    lower95: float | None = Field(default=None, ge=0, le=1)
    upper95: float | None = Field(default=None, ge=0, le=1)
    averagePriceDeltaBps: float | None = None

    tradesPerHour: float | None = Field(default=None, ge=0)
    tradesPerDay: float | None = Field(default=None, ge=0)

    maxWinStreak: int = Field(default=0, ge=0)
    maxLossStreak: int = Field(default=0, ge=0)

    moneyAvailable: bool = False
    currency: str | None = Field(default=None, max_length=8)
    monetaryTrades: int = Field(default=0, ge=0)
    mixedCurrency: bool = False
    excludedByCurrency: int = Field(default=0, ge=0)
    grossProfit: float | None = None
    grossLoss: float | None = None
    netPaperPnl: float | None = None
    profitFactor: float | None = None
    expectancyPerTrade: float | None = None
    payoffRatio: float | None = None
    expectancyLower95: float | None = None
    expectancyUpper95: float | None = None

    maxDrawdown: float | None = Field(default=None, ge=0)
    maxDrawdownRelative: float | None = Field(default=None, ge=0)
    """Relative to an explicitly configured simulated starting capital, and ``None`` without
    one. No account equity is invented anywhere in this package."""


class EquityPoint(Model):
    """One settled outcome on the simulated paper equity curve.

    ``SIMULATED PAPER EQUITY``. It is not a broker balance, it was never funded, and no order
    behind it was ever placed.
    """

    settledAt: int = Field(strict=True)
    tradeId: UUID
    platform: Platform
    assetName: str = Field(min_length=1, max_length=120)
    outcome: Literal["WIN", "LOSS", "DRAW"]
    pnl: float
    cumulativePnl: float
    peak: float
    drawdown: float = Field(ge=0)
    currency: str = Field(min_length=1, max_length=8)


class RollingWindow(Model):
    """One descriptive window over consecutive outcomes, to show whether an edge moved."""

    index: int = Field(ge=0)
    startTime: int = Field(strict=True)
    endTime: int = Field(strict=True)
    resolved: int = Field(ge=0)
    winRateExcludingDraws: float | None = Field(default=None, ge=0, le=1)
    expectancyPerTrade: float | None = None
    profitFactor: float | None = None


class ContributionRow(Model):
    """Descriptive contribution to the simulated result. Never a whitelist."""

    dimension: Literal["PLATFORM", "ASSET", "REGIME", "DIRECTION"]
    key: str = Field(min_length=1, max_length=160)
    resolved: int = Field(ge=0)
    share: float = Field(ge=0, le=1)
    wins: int = Field(ge=0)
    losses: int = Field(ge=0)
    draws: int = Field(ge=0)
    winRateExcludingDraws: float | None = Field(default=None, ge=0, le=1)
    netPaperPnl: float | None = None
    currency: str | None = Field(default=None, max_length=8)


class DailyPerformance(Model):
    """One local trading date, in the explicitly configured timezone."""

    localDate: str = Field(min_length=1, max_length=16)
    timezone: str = Field(min_length=1, max_length=64)
    resolved: int = Field(ge=0)
    wins: int = Field(ge=0)
    losses: int = Field(ge=0)
    draws: int = Field(ge=0)
    netPaperPnl: float | None = None
    currency: str | None = Field(default=None, max_length=8)


class DailyDistribution(Model):
    days: int = Field(ge=0)
    profitableDays: int = Field(ge=0)
    losingDays: int = Field(ge=0)
    flatDays: int = Field(ge=0)
    meanDailyPnl: float | None = None
    medianDailyPnl: float | None = None
    currency: str | None = Field(default=None, max_length=8)
    rows: list[DailyPerformance] = Field(default_factory=list, max_length=MAX_ROWS)


class ReplayCoverage(Model):
    """How much of a market this history actually saw.

    A profitable backtest over one regime, one asset and one afternoon is a description of one
    regime, one asset and one afternoon. These are the fields that make that visible.
    """

    regimeCounts: dict[Regime, int] = Field(default_factory=dict)
    dominantRegime: str | None = Field(default=None, max_length=40)
    dominantRegimeShare: float | None = Field(default=None, ge=0, le=1)

    assetShares: list[ContributionRow] = Field(default_factory=list, max_length=MAX_ROWS)
    topAsset: str | None = Field(default=None, max_length=120)
    topAssetShare: float | None = Field(default=None, ge=0, le=1)

    hoursCovered: int = Field(default=0, ge=0, le=24)
    weekdaysCovered: int = Field(default=0, ge=0, le=7)
    tradingDates: int = Field(default=0, ge=0)
    timezone: str = Field(default="UTC", min_length=1, max_length=64)


class LatencyScenario(Model):
    """One hypothetical decision delay. Research only, and never a production setting.

    The Phase 7 opinion and the Phase 8 board are byte-identical across every scenario — the
    same selections, the same ids — because only the moment the decision is treated as
    actionable moves. A delay that happens to look better here is a property of this history,
    not an instruction.
    """

    delayMs: int = Field(ge=0, le=600_000)
    selections: int = Field(ge=0)
    resolved: int = Field(ge=0)
    wins: int = Field(ge=0)
    losses: int = Field(ge=0)
    draws: int = Field(ge=0)
    invalid: int = Field(ge=0)
    winRateExcludingDraws: float | None = Field(default=None, ge=0, le=1)
    expectancyPerTrade: float | None = None
    averagePriceDeltaBps: float | None = None
    winRateDelta: float | None = None
    expectancyDelta: float | None = None
    priceDeltaBpsDelta: float | None = None
    simulationOnly: Literal[True] = True


class PayoutScenario(Model):
    """The same recorded outcomes re-priced at a stated payout. No direction is changed."""

    payoutRate: float = Field(ge=0, le=10)
    resolved: int = Field(ge=0)
    expectancyPerTrade: float | None = None
    netPaperPnl: float | None = None
    profitFactor: float | None = None
    breakEvenWinRate: float | None = Field(default=None, ge=0, le=1)
    currency: str | None = Field(default=None, max_length=8)
    aboveBreakEven: bool | None = None


class FoldMetrics(Model):
    """What one candidate did inside one period of one fold."""

    period: Literal["TRAIN", "VALIDATION", "TEST"]
    total: int = Field(ge=0)
    selected: int = Field(ge=0)
    coverage: float | None = Field(default=None, ge=0, le=1)
    resolved: int = Field(ge=0)
    winRateExcludingDraws: float | None = Field(default=None, ge=0, le=1)
    baselineWinRate: float | None = Field(default=None, ge=0, le=1)
    lift: float | None = None
    expectancyPerTrade: float | None = None
    profitFactor: float | None = None
    currency: str | None = Field(default=None, max_length=8)
    mixedCurrency: bool = False
    excludedByCurrency: int = Field(default=0, ge=0)
    monetaryTrades: int = Field(default=0, ge=0)


class WalkForwardFold(Model):
    """One chronological fold: discovered on TRAIN, checked on VALIDATION, judged on TEST.

    TEST never participates in choosing anything. The candidate is frozen the moment TRAIN has
    been searched, and a fold that finds nothing reports ``NO_STABLE_CANDIDATE`` rather than
    lowering a floor until something appears.
    """

    foldId: int = Field(ge=1)

    trainStart: int = Field(strict=True)
    trainEnd: int = Field(strict=True)
    validationStart: int = Field(strict=True)
    validationEnd: int = Field(strict=True)
    testStart: int = Field(strict=True)
    testEnd: int = Field(strict=True)

    purgeMs: int = Field(ge=0)
    embargoMs: int = Field(ge=0)
    purgedRows: int = Field(default=0, ge=0)
    embargoedRows: int = Field(default=0, ge=0)

    candidateSourceSnapshotId: UUID | None = None
    candidateMetric: str | None = Field(default=None, max_length=48)
    candidateOperator: Literal[">="] = ">="
    candidateThreshold: float | None = Field(default=None, ge=0, le=1)
    comparisonsEvaluated: int = Field(default=0, ge=0)

    train: FoldMetrics
    validation: FoldMetrics
    test: FoldMetrics

    directionalStable: bool = False
    monetaryStable: bool = False
    monetaryVerdict: StabilityVerdict = "UNTESTED"
    warnings: list[Code] = Field(default_factory=list, max_length=MAX_WARNINGS)
    appliedToLiveExecution: Literal[False] = False


class WalkForwardSummary(Model):
    """Every fold, read together. Deliberately without a best-fold field.

    There is no "pick the fold that worked" anywhere in this model, because a walk-forward study
    whose headline is its best fold is a walk-forward study that learned nothing.
    """

    mode: Literal["COUNT", "DURATION"]
    settings: WalkForwardSettings
    folds: int = Field(ge=0)
    foldsWithCandidate: int = Field(ge=0)
    foldsDirectionalPositive: int = Field(ge=0)
    foldsMonetaryPositive: int = Field(ge=0)

    medianTestWinRate: float | None = Field(default=None, ge=0, le=1)
    medianTestExpectancy: float | None = None
    medianTestCoverage: float | None = Field(default=None, ge=0, le=1)

    candidateMetrics: list[str] = Field(default_factory=list, max_length=MAX_SCENARIOS)
    candidateThresholds: list[float] = Field(default_factory=list, max_length=MAX_FOLDS)
    thresholdSpread: float | None = Field(default=None, ge=0)
    candidateStability: StabilityVerdict = "UNTESTED"

    totalComparisons: int = Field(default=0, ge=0)
    rows: list[WalkForwardFold] = Field(default_factory=list, max_length=MAX_FOLDS)
    warnings: list[Code] = Field(default_factory=list, max_length=MAX_WARNINGS)
    appliedToLiveExecution: Literal[False] = False


class SessionGuardDay(Model):
    localDate: str = Field(min_length=1, max_length=16)
    tradesBeforeStop: int = Field(ge=0)
    targetReached: bool = False
    lossLimitReached: bool = False
    timeToTriggerMs: int | None = Field(default=None, ge=0)
    pnlAtTrigger: float | None = None
    finalPnl: float | None = None
    currency: str | None = Field(default=None, max_length=8)


class SessionGuardScenarioReport(Model):
    """A sandbox study of one fixed operator configuration. Never a search for a better one.

    No objective in this package is a function of a daily target or limit, and nothing here
    proposes an amount. Phase 11 evaluates the configuration it was handed and reports what it
    would have done; choosing the number is the operator's, not the backtest's.
    """

    enabled: bool = False
    settings: SessionGuardSettings | None = None
    daysEvaluated: int = Field(default=0, ge=0)
    daysTargetReached: int = Field(default=0, ge=0)
    daysLossLimitReached: int = Field(default=0, ge=0)
    daysNeitherReached: int = Field(default=0, ge=0)
    entriesBlocked: int = Field(default=0, ge=0)
    rows: list[SessionGuardDay] = Field(default_factory=list, max_length=MAX_ROWS)
    optimized: Literal[False] = False


class ReplaySummary(Model):
    """The whole baseline backtest of one replay run, platform by platform.

    ``CURRENT_FROZEN_PIPELINE``: no threshold was changed, no Phase 10 finding was applied, no
    regime filter was imposed and no policy was adapted. This is what the code as it stands
    would have decided.
    """

    replayRunId: UUID
    replayVersion: str = Field(default=REPLAY_VERSION, min_length=1, max_length=40)
    label: Literal["CURRENT_FROZEN_PIPELINE"] = "CURRENT_FROZEN_PIPELINE"

    dataset: ReplayDatasetSummary
    causality: ReplayCausalityAudit

    analyticsSnapshotId: UUID | None = None
    datasetFingerprint: str = Field(default="", max_length=64)

    overall: OutcomeTally
    platforms: list[OutcomeTally] = Field(default_factory=list, max_length=2)
    coverage: ReplayCoverage
    daily: DailyDistribution
    contributions: list[ContributionRow] = Field(default_factory=list, max_length=MAX_ROWS)
    rolling: list[RollingWindow] = Field(default_factory=list, max_length=MAX_ROWS)
    equityPoints: int = Field(default=0, ge=0)

    latency: list[LatencyScenario] = Field(default_factory=list, max_length=MAX_SCENARIOS)
    payout: list[PayoutScenario] = Field(default_factory=list, max_length=MAX_SCENARIOS)
    walkForward: WalkForwardSummary | None = None
    sessionGuard: SessionGuardScenarioReport | None = None

    warnings: list[Code] = Field(default_factory=list, max_length=MAX_WARNINGS)
    researchOnly: Literal[True] = True
    appliedToLiveExecution: Literal[False] = False


class ReplayEvidence(Model):
    """The durable research artefact, written for a phase that does not exist yet.

    Persisted deliberately and wired to nothing. Phase 12 may one day read stable score bands,
    stable regimes and latency sensitivity out of this and adapt a policy with them; Phase 11
    ships no code that does, and a test searches the whole repository for a consumer and
    asserts there is none.
    """

    replayRunId: UUID
    replayVersion: str = Field(default=REPLAY_VERSION, min_length=1, max_length=40)
    inputFingerprint: str = Field(min_length=1, max_length=64)
    settingsFingerprint: str = Field(min_length=1, max_length=64)
    baselineAnalyticsSnapshotId: UUID | None = None

    sourceMode: ReplaySourceMode
    entryLayer: ReplayEntryLayer

    baseline: OutcomeTally
    platformBaselines: list[OutcomeTally] = Field(default_factory=list, max_length=2)
    walkForwardFolds: list[WalkForwardFold] = Field(default_factory=list, max_length=MAX_FOLDS)

    stableDirectionalCandidates: list[str] = Field(default_factory=list, max_length=MAX_FOLDS)
    stableMonetaryCandidates: list[str] = Field(default_factory=list, max_length=MAX_FOLDS)
    stableRegimes: list[str] = Field(default_factory=list, max_length=MAX_ROWS)
    stableStrategyRegimes: list[str] = Field(default_factory=list, max_length=MAX_ROWS)

    latencySensitivity: list[LatencyScenario] = Field(
        default_factory=list, max_length=MAX_SCENARIOS
    )
    coverage: ReplayCoverage
    warnings: list[Code] = Field(default_factory=list, max_length=MAX_WARNINGS)

    featureVersion: str = Field(default=SUPPORTED_FEATURE_VERSION, min_length=1, max_length=40)
    regimeVersion: str = Field(default=SUPPORTED_REGIME_VERSION, min_length=1, max_length=40)
    strategyVersion: str = Field(default=SUPPORTED_STRATEGY_VERSION, min_length=1, max_length=40)
    rankingVersion: str = Field(default=SUPPORTED_RANKING_VERSION, min_length=1, max_length=40)
    paperVersion: str = Field(default=SUPPORTED_PAPER_VERSION, min_length=1, max_length=40)
    analyticsVersion: str = Field(default=SUPPORTED_ANALYTICS_VERSION, min_length=1, max_length=40)

    researchOnly: Literal[True] = True
    appliedToLiveExecution: Literal[False] = False
