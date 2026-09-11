"""Settlement aggregation. Pure, and deliberately suspicious of its own input.

Two rules shape everything here. A settlement contributes money only when Phase 9 actually
measured some, in this session's own currency, and only once. And a settlement that cannot
contribute money can still contribute its outcome — the market really did move that way — so
the win/loss tally and the P/L are kept as separate facts rather than one derived from the
other.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from quant_engine.paper.models import PaperSettlement
from quant_engine.session_guard.models import (
    SESSION_GUARD_VERSION,
    SUPPORTED_PAPER_VERSION,
    DailySession,
    DailySessionSummary,
)


@dataclass(frozen=True, slots=True)
class Contribution:
    """What one settlement is allowed to do to a session."""

    counted: bool
    """Whether it joins the win/loss/draw tally."""
    monetary: bool
    """Whether it moves ``realizedPnl``. Never true without ``counted``."""
    amount: float
    reason: str | None = None
    """Why it was limited or refused. ``None`` only for a fully counted monetary settlement."""

    @property
    def rejected(self) -> bool:
        return not self.counted


def classify(
    settlement: PaperSettlement,
    *,
    currency: str,
    already_processed: bool,
    belongs_to_session: bool,
) -> Contribution:
    """Decide what this settlement may contribute, and name the reason when it is less than all.

    Order matters. Identity is checked before contract, contract before value, and value before
    currency, so the reported reason is the first thing actually wrong rather than whichever
    check happened to run last.
    """
    if already_processed:
        return Contribution(counted=False, monetary=False, amount=0.0, reason="DUPLICATE")
    if not belongs_to_session:
        return Contribution(counted=False, monetary=False, amount=0.0, reason="LATE_SETTLEMENT")
    if settlement.paperVersion != SUPPORTED_PAPER_VERSION:
        return Contribution(
            counted=False, monetary=False, amount=0.0, reason="UNSUPPORTED_PAPER_VERSION"
        )
    value = settlement.realizedPnl
    if value is not None and not math.isfinite(value):
        # Not a number the session can be stopped on, and not one it can safely ignore either.
        return Contribution(counted=False, monetary=False, amount=0.0, reason="NOT_FINITE")
    if value is None:
        # Phase 9 resolved a direction without an accounting snapshot. The outcome is real; the
        # money is unknown, and unknown is never zero.
        return Contribution(counted=True, monetary=False, amount=0.0, reason="NO_MONETARY_VALUE")
    if settlement.currency != currency:
        # No automatic conversion, ever. A rate nobody configured is a number nobody can audit.
        return Contribution(counted=True, monetary=False, amount=0.0, reason="CURRENCY_MISMATCH")
    return Contribution(counted=True, monetary=True, amount=value)


def aggregate(session: DailySession, outcome: str, contribution: Contribution) -> dict[str, object]:
    """The field updates one contribution makes to a session. Never mutates its input."""
    updates: dict[str, object] = {}
    if contribution.reason == "DUPLICATE":
        return {"duplicateSettlements": session.duplicateSettlements + 1}
    if contribution.reason == "LATE_SETTLEMENT":
        return {"lateSettlements": session.lateSettlements + 1}
    if contribution.rejected:
        return {"rejectedSettlements": session.rejectedSettlements + 1}

    updates["resolvedTrades"] = session.resolvedTrades + 1
    if outcome == "WIN":
        updates["wins"] = session.wins + 1
    elif outcome == "LOSS":
        updates["losses"] = session.losses + 1
    else:
        updates["draws"] = session.draws + 1

    if contribution.reason == "CURRENCY_MISMATCH":
        updates["currencyMismatches"] = session.currencyMismatches + 1
    elif contribution.reason == "NO_MONETARY_VALUE":
        updates["nonMonetarySettlements"] = session.nonMonetarySettlements + 1

    if not contribution.monetary:
        return updates

    amount = contribution.amount
    realized = session.realizedPnl + amount
    peak = max(session.peakRealizedPnl, realized)
    updates["monetaryTrades"] = session.monetaryTrades + 1
    updates["realizedPnl"] = realized
    updates["grossProfit"] = session.grossProfit + max(0.0, amount)
    updates["grossLoss"] = session.grossLoss + max(0.0, -amount)
    updates["largestWin"] = max(session.largestWin, amount)
    updates["largestLoss"] = min(session.largestLoss, amount)
    updates["peakRealizedPnl"] = peak
    updates["troughRealizedPnl"] = min(session.troughRealizedPnl, realized)
    updates["maxRealizedDrawdown"] = max(session.maxRealizedDrawdown, peak - realized)
    return updates


def summarize(session: DailySession) -> DailySessionSummary:
    """One flat, already-computed record of a trading day.

    Ratios are ``None`` rather than zero when there is nothing to divide by: a day with no
    resolved trades has no win rate, and reporting 0% would be a claim about performance that
    the day contains no evidence for.
    """
    decisive = session.wins + session.losses
    resolved = decisive + session.draws
    duration = (
        session.targetReachedAt - session.startedAt if session.targetReachedAt is not None else None
    )
    return DailySessionSummary(
        sessionId=session.sessionId,
        date=session.sessionDate,
        timezone=session.timezone,
        startedAt=session.startedAt,
        endedAt=session.completedAt,
        status=session.status,
        stopReason=session.stopReason,
        accountingSource=session.accountingSource,
        currency=session.currency,
        profitTarget=session.profitTarget,
        lossLimit=session.lossLimit,
        realizedPnl=session.realizedPnl,
        grossProfit=session.grossProfit,
        grossLoss=session.grossLoss,
        wins=session.wins,
        losses=session.losses,
        draws=session.draws,
        invalid=session.invalid,
        resolvedTrades=session.resolvedTrades,
        monetaryTrades=session.monetaryTrades,
        winRateExcludingDraws=session.wins / decisive if decisive else None,
        winRateIncludingDraws=session.wins / resolved if resolved else None,
        largestWin=session.largestWin,
        largestLoss=session.largestLoss,
        peakRealizedPnl=session.peakRealizedPnl,
        troughRealizedPnl=session.troughRealizedPnl,
        maxRealizedDrawdown=session.maxRealizedDrawdown,
        targetReached=session.targetReachedAt is not None,
        lossLimitReached=session.lossLimitReachedAt is not None,
        targetReachedAt=session.targetReachedAt,
        lossLimitReachedAt=session.lossLimitReachedAt,
        tradesToTarget=session.tradesToTarget,
        durationToTargetMs=max(0, duration) if duration is not None else None,
        sessionGuardVersion=SESSION_GUARD_VERSION,
    )
