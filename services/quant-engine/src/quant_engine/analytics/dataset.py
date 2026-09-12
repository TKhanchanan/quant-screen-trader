"""Deterministic analysis dataset from the durable Phase 9 record.

The durable record is append-only: one row per lifecycle transition, written in whatever order
the ingestion thread produced them and read back in whatever order the filesystem hands the
files over. Neither of those is allowed to reach an analysis, so this module does three things
before anything is measured — collapse each trade to its terminal row, drop anything produced
under a contract this version does not support, and sort what remains by market time.

The same stored history therefore produces the same dataset, and the same dataset produces the
same numbers. Nothing here reads a clock, a modification time or a file order.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import fields
from datetime import datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from quant_engine.analytics.models import (
    SUPPORTED_FEATURE_VERSION,
    SUPPORTED_PAPER_VERSION,
    SUPPORTED_RANKING_VERSION,
    SUPPORTED_REGIME_VERSION,
    SUPPORTED_STRATEGY_VERSION,
    AnalyticsDataset,
    AnalyticsFilters,
    AnalyticsRow,
    AnalyticsSettings,
    DataQualityReport,
    ResolvedOutcome,
    StrategyVoteRow,
)
from quant_engine.paper.models import PaperTrade
from quant_engine.strategy.models import StrategyEvaluation

GENERATED_FROM = "paper_trades"

STATE_ORDER: Mapping[str, int] = {
    "PENDING_ENTRY": 0,
    "OPEN": 1,
    "CANCELLED": 2,
    "INVALID": 2,
    "RESOLVED": 3,
}
"""Newest-state-wins precedence for the several stored rows of one trade.

Deliberately restated here rather than imported from the market engine's restart path. That one
decides what a *running* engine may continue; this one decides what a *finished* trade was. They
agree today, and a change to either must be a deliberate change to both rather than a silent
one to whichever happened to own the rule.
"""

ENTRY_TIMEOUT = "ENTRY_TIMEOUT"
RESOLUTION_TIMEOUT = "RESOLUTION_TIMEOUT"
CONTEXT_CODES = frozenset({"CONTEXT_CHANGED", "ASSET_CHANGED", "SLOT_RESET"})

type StrategyKey = tuple[str, int, UUID, int]


def supported(trade: PaperTrade) -> bool:
    """Whether every contract this trade was produced under is one this analysis understands.

    All five are checked, not just the paper version. A trade resolved under ``qst-paper-v1``
    but ranked under a later ranking contract is evidence about a different selection rule, and
    pooling the two would make a calibration of neither.
    """
    return (
        trade.featureVersion == SUPPORTED_FEATURE_VERSION
        and trade.regimeVersion == SUPPORTED_REGIME_VERSION
        and trade.strategyVersion == SUPPORTED_STRATEGY_VERSION
        and trade.rankingVersion == SUPPORTED_RANKING_VERSION
        and trade.paperVersion == SUPPORTED_PAPER_VERSION
    )


def version_label(trade: PaperTrade) -> str:
    return "/".join(
        (
            trade.featureVersion,
            trade.regimeVersion,
            trade.strategyVersion,
            trade.rankingVersion,
            trade.paperVersion,
        )
    )


def terminal_rows(trades: Iterable[PaperTrade]) -> list[PaperTrade]:
    """One row per trade id: the furthest state it ever reached.

    Sorted by id at the end so the collapse cannot inherit the order the rows arrived in.
    """
    latest: dict[UUID, PaperTrade] = {}
    for trade in trades:
        previous = latest.get(trade.paperTradeId)
        if previous is None or _rank(trade) >= _rank(previous):
            latest[trade.paperTradeId] = trade
    return [latest[key] for key in sorted(latest, key=str)]


def _rank(trade: PaperTrade) -> tuple[int, int]:
    return (
        STATE_ORDER.get(trade.status, 0),
        trade.resolvedAtMarketTime if trade.resolvedAtMarketTime is not None else 0,
    )


def strategy_index(
    evaluations: Iterable[StrategyEvaluation],
) -> dict[StrategyKey, list[StrategyEvaluation]]:
    """Phase 7 evaluations keyed by the exact decision they belong to.

    The join is on platform, slot, context and the decision's own close time, so an evaluation
    can only attach to the trade that was actually taken from it. A slot whose asset changed
    carries a new context id and therefore cannot leak its votes into the previous asset's
    outcomes.
    """
    index: dict[StrategyKey, list[StrategyEvaluation]] = defaultdict(list)
    for evaluation in evaluations:
        key: StrategyKey = (
            evaluation.platform,
            evaluation.slotId,
            evaluation.contextId,
            evaluation.asOf,
        )
        index[key].append(evaluation)
    # Sorted by strategy id so a matrix cell is assembled in the same order whatever order the
    # evaluations were stored in.
    return {key: sorted(items, key=lambda item: item.strategyId) for key, items in index.items()}


def votes_for(
    trade: PaperTrade, index: Mapping[StrategyKey, Sequence[StrategyEvaluation]]
) -> tuple[StrategyVoteRow, ...]:
    identity: StrategyKey = (trade.platform, trade.slotId, trade.contextId, trade.boardAsOf)
    return tuple(
        StrategyVoteRow(
            strategyId=evaluation.strategyId,
            direction=evaluation.direction,
            confidence=evaluation.confidence,
            eligible=evaluation.eligible,
        )
        for evaluation in index.get(identity, ())
    )


def selects(trade: PaperTrade, filters: AnalyticsFilters) -> bool:
    """Whether one trade belongs to a filtered view.

    Applied to the trade rather than to the finished rows so a filtered snapshot's data-quality
    report describes the same population its metrics do. A filtered view that reported the whole
    record's resolution rate beside one platform's win rate would be two different samples in
    one table.

    The time window is compared against the expiry a resolved trade actually has, and against
    the decision time for one that never reached an expiry, so an unresolved trade still lands
    in the window it belonged to.
    """
    if filters.platform is not None and trade.platform != filters.platform:
        return False
    if filters.assetName is not None and trade.assetName != filters.assetName:
        return False
    if filters.regime is not None and trade.primaryRegime != filters.regime:
        return False
    if filters.direction is not None and trade.direction != filters.direction:
        return False
    stamp = trade.expiryTime if trade.expiryTime is not None else trade.boardAsOf
    if filters.fromTime is not None and stamp < filters.fromTime:
        return False
    return not (filters.toTime is not None and stamp > filters.toTime)


def build(
    trades: Iterable[PaperTrade],
    evaluations: Iterable[StrategyEvaluation] = (),
    *,
    settings: AnalyticsSettings | None = None,
    filters: AnalyticsFilters | None = None,
) -> AnalyticsDataset:
    """The canonical dataset: resolved outcomes only, plus an honest account of the rest."""
    options = settings if settings is not None else AnalyticsSettings()
    selection = filters if filters is not None else AnalyticsFilters()
    zone = ZoneInfo(options.timezone)
    index = strategy_index(evaluations)

    rows: list[AnalyticsRow] = []
    counts: dict[str, int] = defaultdict(int)
    unsupported_labels: set[str] = set()
    observed: set[str] = set()

    collapsed = terminal_rows(trades)
    for trade in collapsed:
        if not selects(trade, selection):
            continue
        counts["total"] += 1
        observed.add(version_label(trade))
        if not supported(trade):
            counts["unsupported"] += 1
            unsupported_labels.add(version_label(trade))
            continue
        counts["eligible"] += 1
        counts[_bucket(trade.status)] += 1
        if ENTRY_TIMEOUT in trade.invalidReasons or ENTRY_TIMEOUT in trade.reasons:
            counts["entryTimeouts"] += 1
        if RESOLUTION_TIMEOUT in trade.invalidReasons or RESOLUTION_TIMEOUT in trade.reasons:
            counts["resolutionTimeouts"] += 1
        if CONTEXT_CODES & set(trade.reasons):
            counts["contextCancellations"] += 1
        if trade.status != "RESOLVED":
            continue
        row = _row(trade, index=index, zone=zone, timezone=options.timezone)
        if row is None:
            counts["malformed"] += 1
            continue
        counts[row.outcome.casefold()] += 1
        if row.realizedPaperPnl is not None:
            counts["monetary"] += 1
        if row.strategyVotes:
            counts["strategyJoined"] += 1
        rows.append(row)

    # The one ordering every number in this package depends on. Market time first, trade id to
    # break ties, so two outcomes that resolved on the same millisecond cannot swap places
    # between runs and move a temporal split boundary with them.
    ordered = tuple(sorted(rows, key=lambda row: (row.expiryTime, str(row.paperTradeId))))
    quality = DataQualityReport(
        totalTrades=counts["total"],
        eligibleTrades=counts["eligible"],
        resolved=len(ordered),
        wins=counts["win"],
        losses=counts["loss"],
        draws=counts["draw"],
        invalid=counts["invalid"],
        cancelled=counts["cancelled"],
        pendingEntry=counts["pending"],
        open=counts["open"],
        entryTimeouts=counts["entryTimeouts"],
        resolutionTimeouts=counts["resolutionTimeouts"],
        contextCancellations=counts["contextCancellations"],
        unsupportedVersions=counts["unsupported"],
        unsupportedVersionLabels=sorted(unsupported_labels)[:32],
        versionsObserved=sorted(observed)[:32],
        malformed=counts["malformed"],
        monetaryTrades=counts["monetary"],
        strategyJoinedTrades=counts["strategyJoined"],
        resolvedRate=len(ordered) / counts["eligible"] if counts["eligible"] else None,
    )
    return AnalyticsDataset(
        rows=ordered,
        quality=quality,
        fingerprint=fingerprint(ordered),
        sampleStart=ordered[0].expiryTime if ordered else None,
        sampleEnd=ordered[-1].expiryTime if ordered else None,
    )


def _bucket(status: str) -> str:
    return {
        "PENDING_ENTRY": "pending",
        "OPEN": "open",
        "RESOLVED": "resolved",
        "CANCELLED": "cancelled",
        "INVALID": "invalid",
    }.get(status, "malformed")


def _row(
    trade: PaperTrade,
    *,
    index: Mapping[StrategyKey, Sequence[StrategyEvaluation]],
    zone: ZoneInfo,
    timezone: str,
) -> AnalyticsRow | None:
    """One resolved trade as an analysis row, or ``None`` if it cannot honestly be one.

    A row claiming RESOLVED without an entry time, an expiry time or a scoreable outcome is a
    data-quality problem, not an outcome. It is counted as malformed and excluded rather than
    patched up with a guessed timestamp.
    """
    if trade.outcome not in ("WIN", "LOSS", "DRAW"):
        return None
    if trade.entryTime is None or trade.expiryTime is None:
        return None
    outcome: ResolvedOutcome = trade.outcome
    stamp = datetime.fromtimestamp(trade.entryTime / 1000, tz=zone)
    votes = votes_for(trade, index)
    directional = sum(1 for vote in votes if vote.direction in ("UP", "DOWN"))
    return AnalyticsRow(
        paperTradeId=trade.paperTradeId,
        platform=trade.platform,
        slotId=trade.slotId,
        assetName=trade.assetName,
        contextId=trade.contextId,
        direction=trade.direction,
        boardAsOf=trade.boardAsOf,
        decisionAvailableAt=trade.decisionAvailableAt,
        entryTime=trade.entryTime,
        expiryTime=trade.expiryTime,
        rank=trade.rank,
        rankScore=trade.rankScore,
        ensembleConfidence=trade.ensembleConfidence,
        agreement=trade.agreement,
        primaryRegime=trade.primaryRegime,
        regimeConfidence=trade.regimeConfidence,
        leadMargin=trade.leadMargin,
        boardStatus=trade.boardStatus,
        entryQuality=trade.entryQuality,
        expiryQuality=trade.expiryQuality,
        entrySource=trade.entrySource,
        expirySource=trade.expirySource,
        outcome=outcome,
        priceDeltaBps=trade.priceDeltaBps,
        paperStake=trade.paperStake,
        paperPayoutRate=trade.paperPayoutRate,
        realizedPaperPnl=trade.realizedPaperPnl,
        paperCurrency=trade.paperCurrency,
        featureVersion=trade.featureVersion,
        regimeVersion=trade.regimeVersion,
        strategyVersion=trade.strategyVersion,
        rankingVersion=trade.rankingVersion,
        paperVersion=trade.paperVersion,
        hourOfDay=stamp.hour,
        dayOfWeek=stamp.weekday(),
        localDate=stamp.strftime("%Y-%m-%d"),
        timezone=timezone,
        strategyVotes=votes,
        strategyCount=len(votes),
        directionalBreadth=directional / len(votes) if votes else None,
    )


def fingerprint(rows: Sequence[AnalyticsRow]) -> str:
    """Content hash of the analysed outcomes.

    Over **every field of every row**, in declaration order, rather than over a hand-picked list
    of the ones that happen to feed a metric today. A curated list is a list that drifts: a
    field added for one table and forgotten here would let two materially different datasets
    share an identifier, and a snapshot id is only worth having if that cannot happen. The
    exhaustive version costs a few microseconds a row and is asserted field by field in the
    tests, which a curated one could not be.

    Never over a file's modification time, which changes when nothing did and stays put when an
    outcome is rewritten.
    """
    columns = tuple(item.name for item in fields(AnalyticsRow))
    digest = hashlib.sha256()
    for row in rows:
        digest.update("|".join(_scalar(getattr(row, name)) for name in columns).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _scalar(value: object) -> str:
    """One value, spelled the same way every run.

    ``repr`` for floats because it round-trips; ``str`` formatting does not always, and a
    fingerprint that depended on formatting would be a fingerprint of the formatter. Strategy
    votes are folded in as a nested record, so a changed vote on an unchanged outcome still
    changes the dataset — the strategy tables are built from exactly those.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, tuple):
        return "[" + ";".join(_scalar(item) for item in value) + "]"
    if isinstance(value, StrategyVoteRow):
        return (
            f"{value.strategyId}:{value.direction}:"
            f"{repr(float(value.confidence))}:{_scalar(value.eligible)}"
        )
    return str(value)


def settings_fingerprint(settings: AnalyticsSettings, filters: AnalyticsFilters) -> str:
    payload = {
        "settings": settings.model_dump(mode="json"),
        "filters": filters.model_dump(mode="json"),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


EXPORT_COLUMNS = (
    "paperTradeId",
    "platform",
    "slotId",
    "assetName",
    "direction",
    "boardAsOf",
    "decisionAvailableAt",
    "entryTime",
    "expiryTime",
    "rank",
    "rankScore",
    "ensembleConfidence",
    "agreement",
    "primaryRegime",
    "regimeConfidence",
    "leadMargin",
    "boardStatus",
    "outcome",
    "priceDeltaBps",
    "realizedPaperPnl",
    "paperCurrency",
    "hourOfDay",
    "dayOfWeek",
    "localDate",
    "timezone",
    "paperVersion",
    "analyticsVersion",
)
"""What a local export contains. An explicit allow-list rather than every attribute of the row:
an export is a file that leaves the application, and it carries market measurements only — no
session, credential, calibration or window identity has a column here."""


def export_mapping(row: AnalyticsRow) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for column in EXPORT_COLUMNS:
        value = getattr(row, column)
        values[column] = str(value) if isinstance(value, UUID) else value
    return values
