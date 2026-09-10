"""Event-driven entry point for the ranking layer.

The engine holds no market state of its own. Phase 5 owns the series, Phase 6 the features,
Phase 7 the opinions, and this layer owns only the last few opinions per slot — enough to
measure short-term stability — plus a bounded history of the boards it has produced.

Ranking is triggered by a new Phase 7 ensemble, never by a UI poll, and a board compares
only snapshots carrying the same primary close time. Nine slots do not arrive in the same
millisecond, but they do describe the same market decision time or they are not comparable:
a slot still carrying the previous close is marked stale for the epoch rather than ranked
against markets that have already moved on.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final
from uuid import UUID

from quant_engine.configuration import Platform
from quant_engine.market_models import Timeframe
from quant_engine.opportunity.models import (
    INTEGRITY_EXCLUSIONS,
    MAX_WATCHLIST,
    RANKING_VERSION,
    SUPPORTED_FEATURE_VERSION,
    SUPPORTED_REGIME_VERSION,
    SUPPORTED_STRATEGY_VERSION,
    BoardStatus,
    CandidateStatus,
    OpportunityBoard,
    OpportunityCandidate,
    PlatformOpportunityDiagnostics,
    WatchlistEntry,
)
from quant_engine.opportunity.scoring import (
    MIN_LEAD_MARGIN,
    MIN_SELECTION_SCORE,
    PERSISTENCE_WINDOW_LONG,
    PERSISTENCE_WINDOW_SHORT,
    median,
    persistence,
    rank_score,
    sort_key,
    support_for,
)
from quant_engine.strategy.models import Direction, EnsembleSnapshot

RECENT_CAPACITY: Final = PERSISTENCE_WINDOW_LONG
"""Exactly what the longest stability window needs. Ranking never looks further back, so
keeping more would be storing history this layer has no rule for reading."""

BOARD_HISTORY_CAPACITY: Final = 32
"""Bounded per platform. History is a diagnostic convenience; Parquet is the durable record."""

PRIMARY_HORIZON: Final[Mapping[Platform, Timeframe]] = MappingProxyType(
    {"capitalbear": "S5", "iqoption": "M1"}
)
"""Which close defines a ranking epoch on each broker.

Deliberately a literal restatement rather than an import of the Phase 5/6 table. A board's
epoch is the meaning of the whole comparison; if a horizon ever changed upstream, this layer
must refuse the mismatch loudly rather than silently redefine what its boards compare."""

type SlotKey = tuple[Platform, int]


@dataclass(frozen=True, slots=True)
class Observation:
    """One past Phase 7 reading for one slot, reduced to what stability needs."""

    asOf: int
    direction: Direction
    confidence: float
    strategyVersion: str


@dataclass(slots=True)
class SlotHistory:
    """Bounded recent readings for one slot under one unchanging identity."""

    assetName: str
    contextId: UUID
    entries: deque[Observation] = field(default_factory=lambda: deque(maxlen=RECENT_CAPACITY))

    def window(self, size: int) -> list[Observation]:
        return list(self.entries)[-size:]


@dataclass(slots=True)
class Epoch:
    """One platform's cohort for one primary close."""

    platform: Platform
    asOf: int
    primaryTimeframe: Timeframe
    expected: set[int]
    candidates: dict[int, OpportunityCandidate] = field(default_factory=dict)
    received: set[int] = field(default_factory=set)
    """Slots that produced a snapshot for *this* close. A slot present in ``candidates`` but
    not here is a stale placeholder: visible on the board, and still owed."""
    superseded: bool = False
    board: OpportunityBoard | None = None

    @property
    def complete(self) -> bool:
        return bool(self.expected) and self.received >= self.expected

    @property
    def closed(self) -> bool:
        return self.complete or self.superseded


@dataclass(frozen=True, slots=True)
class Ingestion:
    """What one ensemble did to the ranking state.

    ``finalized`` carries the board of an epoch that this arrival superseded — the one moment
    a board becomes immutable, and therefore the only moment it is worth persisting.
    """

    board: OpportunityBoard
    finalized: OpportunityBoard | None = None
    accepted: bool = True


class OpportunityEngine:
    """One instance owns the current cohort and recent boards for every platform."""

    def __init__(self) -> None:
        self.slots: dict[SlotKey, SlotHistory] = {}
        self.current: dict[Platform, Epoch] = {}
        self.history: dict[Platform, deque[OpportunityBoard]] = {}
        self.ingested = 0
        self.duplicates = 0
        self.outOfOrder = 0
        self.staleForEpoch = 0
        self.finalized = 0

    # --- ingestion ---------------------------------------------------------------------

    def ingest(self, ensemble: EnsembleSnapshot, expected_slots: Iterable[int]) -> Ingestion | None:
        """Rank one new Phase 7 ensemble against its platform's current cohort.

        Returns ``None`` only when there is nothing to report at all, which cannot happen
        once a platform has an epoch: a rejected arrival still returns the unchanged board so
        a caller never has to guess whether its state moved.
        """
        platform, slot = ensemble.platform, ensemble.slotId
        expected = {value for value in expected_slots if 1 <= value <= 9} | {slot}

        self._reset_on_context_change(ensemble)
        history = self.slots.get((platform, slot))
        if history is not None and self._is_duplicate(history, ensemble):
            self.duplicates += 1
            return self._unchanged(platform)
        if history is not None and self._is_out_of_order(history, ensemble):
            self.outOfOrder += 1
            return self._unchanged(platform)

        exclusions = self._exclusions(ensemble)
        trusted = not (INTEGRITY_EXCLUSIONS & set(exclusions))
        if trusted:
            history = self._record(ensemble)

        candidate = self._candidate(ensemble, history if trusted else None, exclusions)
        return self._place(candidate, ensemble.asOf, expected)

    def _unchanged(self, platform: Platform) -> Ingestion | None:
        epoch = self.current.get(platform)
        if epoch is None or epoch.board is None:
            return None
        return Ingestion(board=epoch.board, finalized=None, accepted=False)

    def _reset_on_context_change(self, ensemble: EnsembleSnapshot) -> None:
        """A new asset or context is a new series. Nothing from the previous one survives:
        not the stability window, and not the candidate already on the open board."""
        key: SlotKey = (ensemble.platform, ensemble.slotId)
        history = self.slots.get(key)
        if history is None:
            return
        if (history.assetName, history.contextId) == (ensemble.assetName, ensemble.contextId):
            return
        del self.slots[key]
        epoch = self.current.get(ensemble.platform)
        if epoch is not None:
            self._drop(epoch, ensemble.slotId)

    @staticmethod
    def _is_duplicate(history: SlotHistory, ensemble: EnsembleSnapshot) -> bool:
        """Identity is platform, slot, asset, context, as-of and strategy version. The first
        four are already established by the history this slot owns."""
        if not history.entries:
            return False
        last = history.entries[-1]
        return last.asOf == ensemble.asOf and last.strategyVersion == ensemble.strategyVersion

    @staticmethod
    def _is_out_of_order(history: SlotHistory, ensemble: EnsembleSnapshot) -> bool:
        return bool(history.entries) and ensemble.asOf < history.entries[-1].asOf

    def _exclusions(self, ensemble: EnsembleSnapshot) -> list[str]:
        """Every reason this snapshot may not enter a directional ranking, integrity first.

        A Phase 7 SKIP is never resurrected and a Phase 7 NEUTRAL is never converted into a
        direction, so neither appears here as something to argue with.
        """
        codes: list[str] = []
        if (
            ensemble.featureVersion != SUPPORTED_FEATURE_VERSION
            or ensemble.regimeVersion != SUPPORTED_REGIME_VERSION
            or ensemble.strategyVersion != SUPPORTED_STRATEGY_VERSION
        ):
            codes.append("UNSUPPORTED_VERSION")
        if ensemble.primaryTimeframe != PRIMARY_HORIZON[ensemble.platform]:
            codes.append("INVALID_IDENTITY")
        if ensemble.asOf <= 0:
            codes.append("CHRONOLOGY_INVALID")
        if ensemble.status == "INVALID":
            codes.append("INVALID_ANALYSIS")
        if ensemble.direction == "SKIP":
            codes.append("STRATEGY_SKIP")
        return codes

    def _record(self, ensemble: EnsembleSnapshot) -> SlotHistory:
        """Append to the slot's bounded window. SKIP and NEUTRAL are recorded too: a window
        that mostly abstained holds less directional evidence, and that is worth knowing."""
        key: SlotKey = (ensemble.platform, ensemble.slotId)
        history = self.slots.get(key)
        if history is None:
            history = SlotHistory(assetName=ensemble.assetName, contextId=ensemble.contextId)
            self.slots[key] = history
        history.entries.append(
            Observation(
                asOf=ensemble.asOf,
                direction=ensemble.direction,
                confidence=ensemble.confidence,
                strategyVersion=ensemble.strategyVersion,
            )
        )
        self.ingested += 1
        return history

    def _candidate(
        self,
        ensemble: EnsembleSnapshot,
        history: SlotHistory | None,
        exclusions: list[str],
    ) -> OpportunityCandidate:
        """One candidate with every diagnostic that moved its score, flattering or not."""
        directional = ensemble.direction in ("UP", "DOWN") and not exclusions
        short = self._directions(history, PERSISTENCE_WINDOW_SHORT)
        long = self._directions(history, PERSISTENCE_WINDOW_LONG)
        persistence3 = persistence(short, ensemble.direction) if directional else 0.0
        persistence5 = persistence(long, ensemble.direction) if directional else 0.0
        support = support_for(
            agreement=ensemble.agreement,
            regime_confidence=ensemble.regime.confidence,
            active_strategies=ensemble.activeStrategies,
            direction_persistence3=persistence3,
            disagreement=ensemble.disagreement,
            noise_score=ensemble.regime.noiseScore,
            analysis_status=ensemble.status,
        )
        score = rank_score(ensemble.confidence, support.score) if directional else 0.0
        status: CandidateStatus
        if exclusions:
            status = "EXCLUDED"
        elif ensemble.direction == "NEUTRAL":
            status = "NEUTRAL"
        elif score >= MIN_SELECTION_SCORE:
            status = "ACTIONABLE"
        else:
            status = "WATCH"
        reasons = list(support.codes) if directional else []
        if status == "WATCH":
            reasons.insert(0, "BELOW_SELECTION_SCORE")
        return OpportunityCandidate(
            platform=ensemble.platform,
            slotId=ensemble.slotId,
            assetName=ensemble.assetName,
            contextId=ensemble.contextId,
            asOf=ensemble.asOf,
            primaryTimeframe=ensemble.primaryTimeframe,
            featureVersion=ensemble.featureVersion,
            regimeVersion=ensemble.regimeVersion,
            strategyVersion=ensemble.strategyVersion,
            rankingVersion=RANKING_VERSION,
            direction=ensemble.direction,
            analysisStatus=ensemble.status,
            ensembleConfidence=ensemble.confidence,
            agreement=ensemble.agreement,
            disagreement=ensemble.disagreement,
            primaryRegime=ensemble.regime.primaryRegime,
            regimeConfidence=ensemble.regime.confidence,
            noiseScore=ensemble.regime.noiseScore,
            qualityFit=ensemble.regime.qualityFit,
            eligibleStrategies=ensemble.eligibleStrategies,
            activeStrategies=ensemble.activeStrategies,
            directionPersistence3=persistence3,
            directionPersistence5=persistence5,
            confidenceMedian3=self._median(history, PERSISTENCE_WINDOW_SHORT),
            confidenceMedian5=self._median(history, PERSISTENCE_WINDOW_LONG),
            supportScore=support.score,
            rankScore=score,
            candidateStatus=status,
            rankReasonCodes=reasons[:4],
            exclusionReasons=exclusions,
        )

    @staticmethod
    def _directions(history: SlotHistory | None, size: int) -> list[Direction]:
        return [item.direction for item in history.window(size)] if history else []

    @staticmethod
    def _median(history: SlotHistory | None, size: int) -> float:
        if history is None:
            return 0.0
        return median([item.confidence for item in history.window(size)])

    # --- cohort ------------------------------------------------------------------------

    def _place(self, candidate: OpportunityCandidate, as_of: int, expected: set[int]) -> Ingestion:
        """Put one candidate into its platform's epoch, opening or closing epochs as needed."""
        platform = candidate.platform
        epoch = self.current.get(platform)
        finalized: OpportunityBoard | None = None

        if epoch is not None and as_of < epoch.asOf:
            # This slot is still carrying the previous close. It is owed, not comparable.
            self.staleForEpoch += 1
            epoch.candidates[candidate.slotId] = candidate.model_copy(
                update={
                    "candidateStatus": "EXCLUDED",
                    "rank": None,
                    "rankScore": 0.0,
                    "rankReasonCodes": [],
                    "exclusionReasons": [*candidate.exclusionReasons, "STALE_FOR_EPOCH"][:8],
                }
            )
            epoch.received.discard(candidate.slotId)
            epoch.expected = expected | epoch.received
            return Ingestion(board=self._rebuild(epoch), finalized=None, accepted=False)

        if epoch is None or as_of > epoch.asOf:
            if epoch is not None:
                finalized = self._finalize(epoch)
            epoch = Epoch(
                platform=platform,
                asOf=as_of,
                primaryTimeframe=candidate.primaryTimeframe,
                expected=set(expected),
            )
            self.current[platform] = epoch

        epoch.candidates[candidate.slotId] = candidate
        epoch.received.add(candidate.slotId)
        epoch.expected = expected | epoch.received
        return Ingestion(board=self._rebuild(epoch), finalized=finalized, accepted=True)

    def _drop(self, epoch: Epoch, slot_id: int) -> None:
        epoch.candidates.pop(slot_id, None)
        epoch.received.discard(slot_id)
        epoch.expected.discard(slot_id)
        self._rebuild(epoch)

    def _finalize(self, epoch: Epoch) -> OpportunityBoard:
        """Close an epoch the next one has superseded. Missing slots stay missing."""
        epoch.superseded = True
        board = self._rebuild(epoch)
        boards = self.history.setdefault(epoch.platform, deque(maxlen=BOARD_HISTORY_CAPACITY))
        boards.append(board)
        self.finalized += 1
        return board

    def _rebuild(self, epoch: Epoch) -> OpportunityBoard:
        epoch.board = build_board(epoch)
        return epoch.board

    # --- reads -------------------------------------------------------------------------

    def latest_board(self, platform: Platform) -> OpportunityBoard | None:
        epoch = self.current.get(platform)
        return epoch.board if epoch is not None else None

    def candidate(self, platform: Platform, slot_id: int) -> OpportunityCandidate | None:
        board = self.latest_board(platform)
        if board is None:
            return None
        return next((item for item in board.candidates if item.slotId == slot_id), None)

    def recent_boards(self, platform: Platform, limit: int) -> list[OpportunityBoard]:
        """Newest first, starting with the live board. Bounded by what the engine keeps."""
        if limit <= 0:
            return []
        boards: list[OpportunityBoard] = []
        current = self.latest_board(platform)
        if current is not None:
            boards.append(current)
        boards.extend(reversed(self.history.get(platform, deque())))
        return boards[:limit]

    def reset_slot(self, platform: Platform, slot_ids: Iterable[int]) -> int:
        """Drop every trace of a slot when its asset or context changes upstream."""
        removed = 0
        epoch = self.current.get(platform)
        for slot_id in slot_ids:
            if self.slots.pop((platform, slot_id), None) is not None:
                removed += 1
            if epoch is not None:
                self._drop(epoch, slot_id)
        return removed

    def diagnostics(self) -> list[PlatformOpportunityDiagnostics]:
        platforms = sorted({*self.current, *self.history})
        return [
            PlatformOpportunityDiagnostics(
                platform=platform,
                board=self.latest_board(platform),
                boards=len(self.history.get(platform, deque())),
                slotsTracked=sum(1 for key in self.slots if key[0] == platform),
            )
            for platform in platforms
        ]


# --- board assembly --------------------------------------------------------------------


def build_board(epoch: Epoch) -> OpportunityBoard:
    """Rank one cohort deterministically and decide whether anything leads it.

    Pure with respect to the epoch: the same candidates in any arrival order produce the same
    board, because ranking sorts on the candidates' own values and the slot number breaks
    every remaining tie.
    """
    ranked, others = _rank(epoch.candidates.values())
    reasons: list[str] = []
    selection = _select(ranked, epoch.closed, reasons)
    missing = sorted(epoch.expected - epoch.received)
    if missing:
        reasons.append("MISSING_SLOTS")
    if any(
        "STALE_FOR_EPOCH" in candidate.exclusionReasons for candidate in epoch.candidates.values()
    ):
        reasons.append("STALE_SLOT_PRESENT")
    if any(
        "UNSUPPORTED_VERSION" in candidate.exclusionReasons
        for candidate in epoch.candidates.values()
    ):
        reasons.append("UNSUPPORTED_VERSION_PRESENT")
    reasons.append("COHORT_COMPLETE" if epoch.complete else "COHORT_INCOMPLETE")
    candidates = [*ranked, *others]
    reasons = list(dict.fromkeys(reasons))
    return OpportunityBoard(
        platform=epoch.platform,
        asOf=epoch.asOf,
        primaryTimeframe=epoch.primaryTimeframe,
        featureVersion=SUPPORTED_FEATURE_VERSION,
        regimeVersion=SUPPORTED_REGIME_VERSION,
        strategyVersion=SUPPORTED_STRATEGY_VERSION,
        rankingVersion=RANKING_VERSION,
        status=_status(epoch, candidates, selection),
        expectedSlots=len(epoch.expected),
        receivedSlots=len(epoch.received),
        rankedSlots=len(ranked),
        excludedSlots=sum(1 for item in candidates if item.candidateStatus == "EXCLUDED"),
        missingSlots=missing,
        candidates=candidates,
        selectedSlotId=selection.slotId,
        selectedAssetName=selection.assetName,
        selectedDirection=selection.direction,
        selectedScore=selection.score,
        runnerUpSlotId=selection.runnerUpSlotId,
        leadMargin=selection.leadMargin,
        watchlist=_watchlist(ranked),
        reasons=reasons[:8],
    )


def _rank(
    candidates: Iterable[OpportunityCandidate],
) -> tuple[list[OpportunityCandidate], list[OpportunityCandidate]]:
    """Directional candidates in rank order, then everything else by slot.

    Sorting is over the candidates' own values, never over dictionary iteration order, so a
    replay of the same cohort produces identical ranks.
    """
    directional = sorted(
        (item for item in candidates if item.candidateStatus in ("ACTIONABLE", "WATCH")),
        key=sort_key,
    )
    ranked = [
        item.model_copy(update={"rank": position})
        for position, item in enumerate(directional, start=1)
    ]
    others = sorted(
        (item for item in candidates if item.candidateStatus not in ("ACTIONABLE", "WATCH")),
        key=lambda item: item.slotId,
    )
    return ranked, others


@dataclass(frozen=True, slots=True)
class Selection:
    """The board's leading analysis, or the explicit absence of one."""

    slotId: int | None = None
    assetName: str | None = None
    direction: Direction | None = None
    score: float | None = None
    runnerUpSlotId: int | None = None
    leadMargin: float | None = None


def _select(ranked: list[OpportunityCandidate], closed: bool, reasons: list[str]) -> Selection:
    """Name the top analytical candidate, or refuse to.

    Three conservative conditions, each of which can only withhold a selection: the cohort
    must be closed, so a stronger market cannot still be on its way; the leader must clear
    ``MIN_SELECTION_SCORE`` on its own, however few rivals it has; and it must stand
    ``MIN_LEAD_MARGIN`` clear of the runner-up, because two scores a hundredth apart are not
    distinguishable by a heuristic nobody has calibrated.
    """
    if not ranked:
        reasons.append("NO_DIRECTIONAL_CANDIDATE")
        return Selection()
    top = ranked[0]
    runner_up = ranked[1] if len(ranked) > 1 else None
    margin = None if runner_up is None else top.rankScore - runner_up.rankScore
    identity = Selection(
        runnerUpSlotId=None if runner_up is None else runner_up.slotId, leadMargin=margin
    )
    if not closed:
        reasons.append("COHORT_INCOMPLETE")
        return identity
    if top.candidateStatus != "ACTIONABLE":
        reasons.append("BELOW_SELECTION_SCORE")
        return identity
    if margin is not None and margin < MIN_LEAD_MARGIN:
        reasons.append("LOW_LEAD_MARGIN")
        _annotate(ranked, 2, "LOW_LEAD_MARGIN")
        return identity
    _annotate(ranked, 1, "SOLE_CANDIDATE" if runner_up is None else "TOP_CANDIDATE")
    return Selection(
        slotId=top.slotId,
        assetName=top.assetName,
        direction=top.direction,
        score=top.rankScore,
        runnerUpSlotId=identity.runnerUpSlotId,
        leadMargin=margin,
    )


def _annotate(candidates: list[OpportunityCandidate], count: int, code: str) -> None:
    """Record on the leaders themselves why they did or did not become the board's top
    analysis, in place, so the board and its candidates can never disagree about it."""
    for position in range(min(count, len(candidates))):
        candidate = candidates[position]
        if code in candidate.rankReasonCodes:
            continue
        candidates[position] = candidate.model_copy(
            update={"rankReasonCodes": [code, *candidate.rankReasonCodes][:8]}
        )


def _watchlist(ranked: Sequence[OpportunityCandidate]) -> list[WatchlistEntry]:
    return [
        WatchlistEntry(
            rank=candidate.rank or position,
            slotId=candidate.slotId,
            assetName=candidate.assetName,
            direction=candidate.direction,
            rankScore=candidate.rankScore,
            ensembleConfidence=candidate.ensembleConfidence,
            regime=candidate.primaryRegime,
            candidateStatus=candidate.candidateStatus,
        )
        for position, candidate in enumerate(ranked[:MAX_WATCHLIST], start=1)
    ]


def _status(
    epoch: Epoch, candidates: Sequence[OpportunityCandidate], selection: Selection
) -> BoardStatus:
    """One status, chosen so the most important caveat is the one that gets reported.

    Integrity first: a cohort in which nothing arrived under a contract this layer knows is
    INVALID, whatever else is true of it. Then incompleteness, because a ranking over an
    unfinished cohort is provisional no matter how good its leader looks. Only a complete,
    trustworthy cohort is READY, and a complete one that produced no leader says so.
    """
    if candidates and all(INTEGRITY_EXCLUSIONS & set(item.exclusionReasons) for item in candidates):
        return "INVALID"
    if not epoch.closed:
        return "COLLECTING"
    if not epoch.complete:
        return "PARTIAL"
    return "READY" if selection.slotId is not None else "NO_OPPORTUNITY"
