"""Outcome-analytics wire models (Phase 10).

Phase 6 produces facts, Phase 7 interprets one market, Phase 8 compares those opinions, Phase 9
records what the market actually did next, and this layer asks the only question none of them
can: *did the scores correspond to better outcomes?*

It measures. It does not act. Nothing here changes a feature formula, a strategy threshold, a
ranking gate, a session limit or an execution setting, and there is no code path from this
package to any of them — a threshold that looks good in this file is a research observation
about recorded history, never a production change.

Two words are used carefully throughout. ``rankScore`` and ``ensembleConfidence`` are relative
orderings produced by layers that had never observed an outcome, so a bin's ``winRate`` is an
*empirical outcome curve* and never a *probability calibration*; there is deliberately no Brier
score anywhere in this package, because a Brier score over an uncalibrated score would put a
number on a claim nobody has earned.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, model_validator

from quant_engine.configuration import Model, Platform
from quant_engine.market_models import QualityState, SourceType
from quant_engine.opportunity.models import BoardStatus
from quant_engine.paper.models import PaperDirection
from quant_engine.strategy.models import Direction, Regime

ANALYTICS_VERSION = "qst-analytics-v1"
"""Dataset rules, binning convention, interval method, split ratios and the threshold research
score. Any change to how a number here is reached requires a new string, so two snapshots can
never be compared as if they meant the same thing."""

SUPPORTED_FEATURE_VERSION = "qfe-v2"
SUPPORTED_REGIME_VERSION = "qst-regime-v1"
SUPPORTED_STRATEGY_VERSION = "qst-strategy-v1"
SUPPORTED_RANKING_VERSION = "qst-ranking-v1"
SUPPORTED_PAPER_VERSION = "qst-paper-v1"
"""Deliberately literals, not imports of the Phase 6-9 constants.

An outcome is evidence about one exact set of upstream contracts. Tracking the upstream
constants would let a later contract silently pool new decisions with old outcomes and call the
mixture a calibration; a literal makes that arrive as an explicit exclusion instead."""

MIN_DISPLAY_SAMPLE = 20
"""Below this a segment is still reported with its raw counts, labelled LOW_SAMPLE. Hiding it
would be worse: a reader who cannot see the thin slices cannot see how thin the evidence is."""

MIN_RECOMMENDATION_SAMPLE = 50
"""Below this no threshold candidate may be produced at all, however good the rate looks."""

MIN_ASSET_SAMPLE = 30
"""Below this an asset is never placed in a performance ordering. A 100% rate over four trades
is not a good asset, it is four trades."""

MIN_RESOLUTION_RATE = 0.5
MIN_COVERAGE = 0.10
WEAK_CORRELATION = 0.10
MODERATE_CORRELATION = 0.30
STRONG_CORRELATION = 0.60

MAX_SEGMENTS = 256
MAX_CELLS = 512
MAX_BINS = 50
MAX_CANDIDATES = 64
MAX_WARNINGS = 24
MAX_FINDINGS = 64

type SampleLabel = Literal["OK", "LOW_SAMPLE", "INSUFFICIENT_SAMPLE"]
"""OK: at or above the display threshold. LOW_SAMPLE: reported, never recommended on.
INSUFFICIENT_SAMPLE: nothing usable was measured at all."""

type ResolvedOutcome = Literal["WIN", "LOSS", "DRAW"]
"""The only outcomes this layer analyses. PENDING_ENTRY, OPEN, CANCELLED and INVALID trades are
counted in the data-quality report and never in a win rate: a cancelled trade is not a loss,
and an unresolved one is not evidence of anything yet."""

type Stability = Literal["STABLE", "UNSTABLE", "UNTESTED"]
type SplitName = Literal["TRAIN", "VALIDATION", "TEST"]
type CorrelationStrength = Literal["NONE", "NEGLIGIBLE", "WEAK", "MODERATE", "STRONG"]
type VoteStance = Literal["AGREED", "DISAGREED", "ABSTAINED"]

WARNING_CODES = (
    "INSUFFICIENT_SAMPLE",
    "LOW_RESOLUTION_RATE",
    "LOW_SAMPLE_SEGMENTS",
    "NON_MONOTONIC_RANK_SCORE",
    "NON_MONOTONIC_CONFIDENCE",
    "INVERSE_RANK_SCORE",
    "INVERSE_CONFIDENCE",
    "THRESHOLD_UNSTABLE",
    "MULTIPLE_TESTING_WARNING",
    "VERSION_MIXED",
    "MIXED_CURRENCY",
    "PAPER_ACCOUNTING_UNAVAILABLE",
    "NO_STRATEGY_EVIDENCE",
    "SINGLE_PLATFORM",
)
"""Every diagnostic a snapshot can raise about itself.

INVERSE_RANK_SCORE and INVERSE_CONFIDENCE exist because the most valuable thing this layer can
report is that a score does not work. A calibration layer that could only describe success
would be a layer that quietly reinterprets its metric until the metric looks good."""

type Code = str


@dataclass(frozen=True, slots=True)
class StrategyVoteRow:
    """One Phase 7 strategy's opinion, joined back onto the outcome it helped produce.

    A frozen value rather than a wire model: a hundred thousand rows carry six of these each,
    and nothing outside this package ever receives one individually.
    """

    strategyId: str
    direction: Direction
    confidence: float
    eligible: bool


@dataclass(frozen=True, slots=True)
class AnalyticsRow:
    """One resolved Phase 9 outcome with every dimension it can be grouped by.

    The canonical analysis unit. Built once per resolved paper trade, sorted deterministically,
    and never mutated: every metric, bin, segment and threshold in this package is a pure
    function of a tuple of these, which is what makes two runs over the same history produce
    byte-identical results.
    """

    paperTradeId: UUID
    platform: Platform
    slotId: int
    assetName: str
    contextId: UUID

    direction: PaperDirection

    boardAsOf: int
    decisionAvailableAt: int
    entryTime: int
    expiryTime: int

    rank: int | None
    rankScore: float
    ensembleConfidence: float
    agreement: float
    primaryRegime: Regime
    regimeConfidence: float
    leadMargin: float | None
    boardStatus: BoardStatus

    entryQuality: QualityState | None
    expiryQuality: QualityState | None
    entrySource: SourceType | None
    expirySource: SourceType | None

    outcome: ResolvedOutcome
    priceDeltaBps: float | None

    paperStake: float | None
    paperPayoutRate: float | None
    realizedPaperPnl: float | None
    paperCurrency: str | None

    featureVersion: str
    regimeVersion: str
    strategyVersion: str
    rankingVersion: str
    paperVersion: str

    hourOfDay: int
    dayOfWeek: int
    """0 is Monday, matching ``datetime.weekday``."""
    localDate: str
    timezone: str

    analyticsVersion: str = ANALYTICS_VERSION
    strategyVotes: tuple[StrategyVoteRow, ...] = ()
    strategyCount: int = 0
    directionalBreadth: float | None = None
    """Share of the votes present that named UP or DOWN. ``None`` when no Phase 7 evaluation
    could be joined to this trade at all, which is not the same as a panel that all abstained."""

    @property
    def won(self) -> bool:
        return self.outcome == "WIN"

    @property
    def binary(self) -> bool:
        """Whether this row can enter a win/loss comparison. Draws cannot: they are neither."""
        return self.outcome in ("WIN", "LOSS")


class AnalyticsSettings(Model):
    """How the analysis is parameterized. Every value is a research knob, never a live one.

    Nothing in this model can reach the running application: changing a bin count changes how
    recorded history is described and changes no decision, no gate and no order.
    """

    timezone: str = Field(default="Asia/Bangkok", min_length=1, max_length=64)
    """Explicit rather than the host's. An hour-of-day table that silently followed the machine
    would describe a different trading session on a laptop that crossed a border."""

    binCount: int = Field(default=10, ge=2, le=MAX_BINS, strict=True)
    agreementBinCount: int = Field(default=5, ge=2, le=MAX_BINS, strict=True)
    leadMarginBinCount: int = Field(default=5, ge=2, le=MAX_BINS, strict=True)
    regimeConfidenceBinCount: int = Field(default=5, ge=2, le=MAX_BINS, strict=True)
    gridBinCount: int = Field(default=4, ge=2, le=8, strict=True)
    """Side of the rank x confidence grid. Kept small: a ten-by-ten grid over a few hundred
    outcomes is a hundred cells of noise."""

    trainRatio: float = Field(default=0.6, gt=0, lt=1)
    validationRatio: float = Field(default=0.2, gt=0, lt=1)

    minDisplaySample: int = Field(default=MIN_DISPLAY_SAMPLE, ge=1, le=100_000, strict=True)
    minRecommendationSample: int = Field(
        default=MIN_RECOMMENDATION_SAMPLE, ge=1, le=100_000, strict=True
    )
    minAssetSample: int = Field(default=MIN_ASSET_SAMPLE, ge=1, le=100_000, strict=True)
    minCoverage: float = Field(default=MIN_COVERAGE, ge=0, le=1)
    minResolutionRate: float = Field(default=MIN_RESOLUTION_RATE, ge=0, le=1)

    thresholdStep: float = Field(default=0.05, gt=0, le=0.5)
    thresholdFloor: float = Field(default=0.05, ge=0, lt=1)
    thresholdCeiling: float = Field(default=0.95, gt=0, le=1)

    bootstrapIterations: int = Field(default=0, ge=0, le=10_000, strict=True)
    """Off by default; a deterministic seeded resample is available for mean P/L when asked."""
    bootstrapSeed: int = Field(default=20_260_912, ge=0, le=2**31 - 1, strict=True)

    @model_validator(mode="after")
    def ratios_leave_a_test_split(self) -> Self:
        if self.trainRatio + self.validationRatio >= 1:
            raise ValueError("Train and validation ratios must leave a test split")
        if self.thresholdFloor >= self.thresholdCeiling:
            raise ValueError("Threshold floor must sit below the ceiling")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError(f"Unknown timezone: {self.timezone}") from error
        return self

    @property
    def testRatio(self) -> float:
        return 1.0 - self.trainRatio - self.validationRatio


class AnalyticsFilters(Model):
    """A bounded read filter. Field names only; there is no expression language here."""

    platform: Platform | None = None
    assetName: str | None = Field(default=None, min_length=1, max_length=120)
    regime: Regime | None = None
    direction: PaperDirection | None = None
    fromTime: int | None = Field(default=None, strict=True)
    toTime: int | None = Field(default=None, strict=True)

    @property
    def empty(self) -> bool:
        return all(value is None for value in self.model_dump().values())


class DataQualityReport(Model):
    """What the durable record actually contains, before any performance claim is made.

    Reported first and always. A 60% win rate over the fifth of decisions that happened to
    resolve is not a 60% win rate, and the only defence against reading it as one is showing
    how many never resolved at all.
    """

    totalTrades: int = Field(ge=0)
    eligibleTrades: int = Field(ge=0)
    """Distinct trades carrying versions this analysis supports."""
    resolved: int = Field(ge=0)
    wins: int = Field(ge=0)
    losses: int = Field(ge=0)
    draws: int = Field(ge=0)
    invalid: int = Field(ge=0)
    cancelled: int = Field(ge=0)
    pendingEntry: int = Field(ge=0)
    open: int = Field(ge=0)

    entryTimeouts: int = Field(ge=0)
    resolutionTimeouts: int = Field(ge=0)
    contextCancellations: int = Field(ge=0)

    unsupportedVersions: int = Field(ge=0)
    unsupportedVersionLabels: list[str] = Field(default_factory=list, max_length=32)
    versionsObserved: list[str] = Field(default_factory=list, max_length=32)
    malformed: int = Field(ge=0)
    """Rows that claimed RESOLVED without the entry or expiry time such a row must carry."""

    monetaryTrades: int = Field(ge=0)
    strategyJoinedTrades: int = Field(ge=0)

    resolvedRate: float | None = Field(default=None, ge=0, le=1)
    """``resolved / eligibleTrades``. ``None`` when nothing eligible was recorded."""


class OutcomeMetrics(Model):
    """Directional performance for one set of rows. Never a claim about future performance."""

    resolved: int = Field(ge=0)
    wins: int = Field(ge=0)
    losses: int = Field(ge=0)
    draws: int = Field(ge=0)

    winRateExcludingDraws: float | None = Field(default=None, ge=0, le=1)
    winRateIncludingDraws: float | None = Field(default=None, ge=0, le=1)
    drawRate: float | None = Field(default=None, ge=0, le=1)

    lower95: float | None = Field(default=None, ge=0, le=1)
    upper95: float | None = Field(default=None, ge=0, le=1)
    """Wilson score interval on ``winRateExcludingDraws``. Wilson rather than the normal
    approximation because the normal one produces intervals outside [0, 1] on exactly the small
    samples this layer spends most of its life reporting."""

    averagePriceDeltaBps: float | None = None
    medianPriceDeltaBps: float | None = None

    sampleCount: int = Field(ge=0)
    sampleLabel: SampleLabel = "INSUFFICIENT_SAMPLE"


class MoneyMetrics(Model):
    """Simulated money, or an honest unavailable.

    Every field is ``None`` unless Phase 9 was running with an explicit stake, payout rate and
    currency. ``None`` means not known and is never collapsed to zero, which would read as
    break-even on outcomes nobody priced.
    """

    available: bool = False
    monetaryTrades: int = Field(ge=0)
    """Priced outcomes **in ``currency``**. Never a count across currencies."""
    currency: str | None = Field(default=None, max_length=8)
    """The one currency every number below is denominated in. Never ``None`` while
    ``available`` is true: an unlabelled money total is a number with no meaning."""

    currenciesObserved: list[str] = Field(default_factory=list, max_length=8)
    excludedByCurrency: int = Field(default=0, ge=0)
    """Priced outcomes in some *other* currency. They contribute their direction to the
    outcome metrics and nothing at all here. Nothing in this application converts between
    currencies, and a total that silently pooled two of them would be the one number an
    operator is most likely to act on and least able to check."""
    mixedCurrency: bool = False

    grossProfit: float | None = None
    grossLoss: float | None = None
    """A positive magnitude. Stated so ``profitFactor`` reads as a ratio of two positives."""
    netPaperPnl: float | None = None

    averageWin: float | None = None
    averageLoss: float | None = None
    profitFactor: float | None = None
    expectancyPerTrade: float | None = None
    payoffRatio: float | None = None
    """All four are ``None`` when their denominator is zero. An infinite profit factor is not a
    perfect strategy, it is a sample with no losses in it yet."""

    lowerMean95: float | None = None
    upperMean95: float | None = None
    """Deterministic seeded bootstrap on mean P/L, and only when bootstrapping was asked for."""


class SegmentMetrics(Model):
    """One slice of the dataset, always carrying the size of the slice."""

    key: str = Field(min_length=1, max_length=160)
    label: str = Field(min_length=1, max_length=160)
    platform: Platform | None = None
    outcomes: OutcomeMetrics
    money: MoneyMetrics
    averageRankScore: float | None = Field(default=None, ge=0, le=1)
    averageConfidence: float | None = Field(default=None, ge=0, le=1)
    averageRegimeConfidence: float | None = Field(default=None, ge=0, le=1)
    averageAgreement: float | None = Field(default=None, ge=0, le=1)
    rankable: bool = False
    """Whether this segment met the sample floor for being placed in a performance ordering."""


class ScoreBin(Model):
    """One score band and what happened inside it.

    The convention is half-open upward and closed at the very top — ``[0.0, 0.1)`` through
    ``[0.9, 1.0]`` — so every score in [0, 1] lands in exactly one bin and a score of exactly
    1.0 is not silently dropped.
    """

    index: int = Field(ge=0)
    lowerBound: float = Field(ge=0, le=1)
    upperBound: float = Field(ge=0, le=1)
    inclusiveUpper: bool
    label: str = Field(min_length=1, max_length=32)
    scoreMean: float | None = None
    outcomes: OutcomeMetrics
    money: MoneyMetrics


class Correlation(Model):
    """Rank correlation between one score and the binary outcome.

    Spearman rather than Pearson: none of these scores is claimed to be linear in anything, and
    the only question being asked is whether higher tends to be better.
    """

    metric: str = Field(min_length=1, max_length=48)
    coefficient: float | None = Field(default=None, ge=-1, le=1)
    sampleCount: int = Field(ge=0)
    drawsExcluded: int = Field(ge=0)
    strength: CorrelationStrength = "NONE"
    note: str = Field(default="", max_length=200)


class CalibrationReport(Model):
    """The empirical outcome curve for one score.

    Deliberately not called a probability calibration. Phase 7 confidence and Phase 8 rank score
    were produced by layers that had never seen an outcome, so a bin whose midpoint is 0.70 has
    never claimed to win 70% of the time and is not being scored against that claim.
    """

    metric: str = Field(min_length=1, max_length=48)
    binCount: int = Field(ge=2, le=MAX_BINS)
    bins: list[ScoreBin] = Field(default_factory=list, max_length=MAX_BINS)
    correlation: Correlation
    monotonic: bool = False
    monotonicityCoefficient: float | None = Field(default=None, ge=-1, le=1)
    populatedBins: int = Field(ge=0)
    sampleCount: int = Field(ge=0)
    sampleLabel: SampleLabel = "INSUFFICIENT_SAMPLE"
    warnings: list[Code] = Field(default_factory=list, max_length=MAX_WARNINGS)
    note: str = Field(default="", max_length=240)


class MatrixCell(Model):
    row: str = Field(min_length=1, max_length=80)
    column: str = Field(min_length=1, max_length=80)
    samples: int = Field(ge=0)
    agreed: int = Field(ge=0)
    """Only meaningful on the strategy matrix; zero elsewhere."""
    outcomes: OutcomeMetrics
    money: MoneyMetrics
    sampleLabel: SampleLabel = "INSUFFICIENT_SAMPLE"


class Matrix(Model):
    name: str = Field(min_length=1, max_length=64)
    rowLabel: str = Field(min_length=1, max_length=48)
    columnLabel: str = Field(min_length=1, max_length=48)
    rows: list[str] = Field(default_factory=list, max_length=64)
    columns: list[str] = Field(default_factory=list, max_length=64)
    cells: list[MatrixCell] = Field(default_factory=list, max_length=MAX_CELLS)
    sampleCount: int = Field(ge=0)
    note: str = Field(default="", max_length=200)


class StrategyContribution(Model):
    """What one Phase 7 strategy's vote was worth, as evidence rather than as a decision.

    The strategy internals are never re-interpreted here. A vote is read exactly as Phase 7
    recorded it, and the only thing measured is whether outcomes differed when it agreed with
    the selection that was actually taken.
    """

    strategyId: str = Field(min_length=1, max_length=48)
    votesPresent: int = Field(ge=0)
    agreed: int = Field(ge=0)
    disagreed: int = Field(ge=0)
    abstained: int = Field(ge=0)
    neutralVotes: int = Field(ge=0)
    skippedVotes: int = Field(ge=0)
    agreementRate: float | None = Field(default=None, ge=0, le=1)
    whenAgreed: OutcomeMetrics
    whenDisagreed: OutcomeMetrics
    whenAbstained: OutcomeMetrics
    moneyWhenAgreed: MoneyMetrics
    sampleLabel: SampleLabel = "INSUFFICIENT_SAMPLE"


class SplitMetrics(Model):
    """One threshold's behaviour inside one chronological split."""

    split: SplitName
    total: int = Field(ge=0)
    count: int = Field(ge=0)
    coverage: float | None = Field(default=None, ge=0, le=1)
    """``count / total``. A threshold that wins nine times out of ten on three trades has a
    coverage of 0.003 and is not a finding."""
    outcomes: OutcomeMetrics
    money: MoneyMetrics
    baselineWinRate: float | None = Field(default=None, ge=0, le=1)
    """What the whole split did, so a threshold is judged against its own period rather than
    against the pooled average of periods it never traded in."""
    lift: float | None = None


class ThresholdCandidate(Model):
    """A research observation about recorded history. Never a setting.

    Discovered on TRAIN only and then *evaluated* on VALIDATION and TEST. Nothing in this
    application reads this model to decide anything, and Phase 10 ships no way to apply one.
    """

    metric: str = Field(min_length=1, max_length=48)
    operator: Literal[">="] = ">="
    threshold: float = Field(ge=0, le=1)
    train: SplitMetrics
    validation: SplitMetrics
    test: SplitMetrics
    stable: bool = False
    stability: Stability = "UNTESTED"
    directionalStability: Stability = "UNTESTED"
    """Whether the *win rate* held up out of sample."""
    monetaryStability: Stability = "UNTESTED"
    """Whether the *expectancy* did. Deliberately separate, because they disagree: at a 0.8
    payout a rule needs roughly 56% to break even, so a threshold can lift the win rate from
    52% to 55% in every period and still lose money in all three. A candidate that is
    directionally stable and monetarily unstable is not a finding, and collapsing the two into
    one flag would hide exactly that case. ``UNTESTED`` when Phase 9 priced nothing."""
    researchScore: float | None = None
    """A documented ordering for the research table only. Not an expected value, not an edge,
    and explicitly not optimized toward any daily profit or loss target."""
    reasons: list[Code] = Field(default_factory=list, max_length=MAX_WARNINGS)
    appliedToLiveExecution: Literal[False] = False


class TemporalSplitReport(Model):
    """The chronological partition. Never shuffled, and the model says so on the record."""

    ordering: Literal["CHRONOLOGICAL"] = "CHRONOLOGICAL"
    shuffled: Literal[False] = False
    total: int = Field(ge=0)
    train: int = Field(ge=0)
    validation: int = Field(ge=0)
    test: int = Field(ge=0)
    trainRatio: float = Field(gt=0, lt=1)
    validationRatio: float = Field(gt=0, lt=1)
    testRatio: float = Field(gt=0, lt=1)
    trainStart: int | None = Field(default=None, strict=True)
    trainEnd: int | None = Field(default=None, strict=True)
    validationStart: int | None = Field(default=None, strict=True)
    validationEnd: int | None = Field(default=None, strict=True)
    testStart: int | None = Field(default=None, strict=True)
    testEnd: int | None = Field(default=None, strict=True)


class ResearchFinding(Model):
    """One durable research output, kept so a later adaptive phase has something to read.

    Persisted deliberately and wired to nothing. Phase 12 may one day consume these; Phase 10
    ships no code that does, and ``appliedToLiveExecution`` is a literal false on every one.
    """

    kind: Literal["SCORE_BAND", "REGIME", "STRATEGY_REGIME", "SKIP_CONDITION"]
    subject: str = Field(min_length=1, max_length=160)
    detail: str = Field(min_length=1, max_length=240)
    sampleCount: int = Field(ge=0)
    winRate: float | None = Field(default=None, ge=0, le=1)
    lower95: float | None = Field(default=None, ge=0, le=1)
    upper95: float | None = Field(default=None, ge=0, le=1)

    trainCount: int = Field(default=0, ge=0)
    validationCount: int = Field(default=0, ge=0)
    testCount: int = Field(default=0, ge=0)
    trainWinRate: float | None = Field(default=None, ge=0, le=1)
    validationWinRate: float | None = Field(default=None, ge=0, le=1)
    testWinRate: float | None = Field(default=None, ge=0, le=1)
    """The same chronological split the threshold search uses.

    A regime or a strategy pairing is a slice of history chosen after seeing the history, which
    is the same selection bias a threshold search has — so it is held to the same standard. A
    pooled Wilson bound over all-time data is not out-of-sample evidence, however tight it
    looks."""

    stable: bool = False
    """Only when the effect held in *every* period against that period's own baseline, with
    enough sample in each. Everything else is recorded as an observation."""
    reasons: list[Code] = Field(default_factory=list, max_length=MAX_WARNINGS)
    appliedToLiveExecution: Literal[False] = False


class AnalyticsSnapshot(Model):
    """One complete analysis of one exact set of recorded outcomes.

    Identified by a deterministic id over the dataset fingerprint, the settings and the version:
    the same history analysed twice is the same snapshot, and one changed outcome is a different
    one.
    """

    snapshotId: UUID
    analyticsVersion: str = Field(min_length=1, max_length=40)
    generatedFrom: str = Field(min_length=1, max_length=64)
    datasetFingerprint: str = Field(min_length=1, max_length=64)
    settingsFingerprint: str = Field(min_length=1, max_length=64)

    sampleStart: int | None = Field(default=None, strict=True)
    sampleEnd: int | None = Field(default=None, strict=True)
    totalResolved: int = Field(ge=0)
    timezone: str = Field(min_length=1, max_length=64)
    filters: AnalyticsFilters
    settings: AnalyticsSettings

    quality: DataQualityReport
    overallMetrics: OutcomeMetrics
    overallMoney: MoneyMetrics

    platformMetrics: list[SegmentMetrics] = Field(default_factory=list, max_length=8)
    rankCalibration: CalibrationReport
    confidenceCalibration: CalibrationReport
    correlations: list[Correlation] = Field(default_factory=list, max_length=16)

    regimeMetrics: list[SegmentMetrics] = Field(default_factory=list, max_length=MAX_SEGMENTS)
    regimeDirectionMatrix: Matrix
    regimeConfidenceBins: list[ScoreBin] = Field(default_factory=list, max_length=MAX_BINS)

    strategyMetrics: list[StrategyContribution] = Field(default_factory=list, max_length=32)
    strategyRegimeMatrix: Matrix

    agreementBins: list[ScoreBin] = Field(default_factory=list, max_length=MAX_BINS)
    leadMarginBins: list[ScoreBin] = Field(default_factory=list, max_length=MAX_BINS)
    rankConfidenceMatrix: Matrix

    assetMetrics: list[SegmentMetrics] = Field(default_factory=list, max_length=MAX_SEGMENTS)
    hourMetrics: list[SegmentMetrics] = Field(default_factory=list, max_length=24)
    weekdayMetrics: list[SegmentMetrics] = Field(default_factory=list, max_length=7)
    qualityMetrics: list[SegmentMetrics] = Field(default_factory=list, max_length=MAX_SEGMENTS)

    temporalSplit: TemporalSplitReport
    thresholdCandidates: list[ThresholdCandidate] = Field(
        default_factory=list, max_length=MAX_CANDIDATES
    )
    comparisonsEvaluated: int = Field(ge=0)
    """How many threshold/segment comparisons were searched to produce this snapshot. Reported
    because the best slice of a large search is the best slice of a large search."""

    research: list[ResearchFinding] = Field(default_factory=list, max_length=MAX_FINDINGS)
    warnings: list[Code] = Field(default_factory=list, max_length=MAX_WARNINGS)

    researchOnly: Literal[True] = True
    appliedToLiveExecution: Literal[False] = False
    """Both are literals, not defaults an analysis could flip. Phase 10 measures and reports;
    there is no code anywhere in it that changes a live threshold, a stake or an execution
    setting, and a test asserts the package cannot even name one."""


@dataclass(frozen=True, slots=True)
class AnalyticsDataset:
    """The immutable analysis input: sorted rows, what was excluded, and a content fingerprint."""

    rows: tuple[AnalyticsRow, ...] = ()
    quality: DataQualityReport = field(
        default_factory=lambda: DataQualityReport(
            totalTrades=0,
            eligibleTrades=0,
            resolved=0,
            wins=0,
            losses=0,
            draws=0,
            invalid=0,
            cancelled=0,
            pendingEntry=0,
            open=0,
            entryTimeouts=0,
            resolutionTimeouts=0,
            contextCancellations=0,
            unsupportedVersions=0,
            malformed=0,
            monetaryTrades=0,
            strategyJoinedTrades=0,
        )
    )
    fingerprint: str = ""
    sampleStart: int | None = None
    sampleEnd: int | None = None

    def __len__(self) -> int:
        return len(self.rows)
