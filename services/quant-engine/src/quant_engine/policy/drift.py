"""Interpretable two-window withdrawal; no rule or setting is ever adjusted."""

from __future__ import annotations

from collections.abc import Sequence

from quant_engine.analytics.metrics import wilson_interval
from quant_engine.paper.models import PaperTrade
from quant_engine.policy.models import (
    SOURCE_VERSIONS,
    PolicyEvent,
    PolicySnapshot,
    WatchdogSnapshot,
    WatchdogState,
    identity,
)


def evaluate_watchdog(
    snapshot: PolicySnapshot, activation: PolicyEvent, trades: Sequence[PaperTrade], at: int
) -> WatchdogSnapshot:
    settings = snapshot.settings
    # Entries must belong to this activation, not merely settle after it. Deduplicate updates.
    latest = {
        t.paperTradeId: t
        for t in trades
        if t.status == "RESOLVED"
        and t.resolvedAtMarketTime is not None
        and activation.at <= t.decisionAvailableAt <= t.resolvedAtMarketTime <= at
    }
    rows = sorted(latest.values(), key=lambda t: (t.resolvedAtMarketTime or 0, str(t.paperTradeId)))
    binary = [t for t in rows if t.outcome in ("WIN", "LOSS")]
    short, long = binary[-settings.shortWindow :], binary[-settings.longWindow :]
    slo, shi = wilson_interval(sum(t.outcome == "WIN" for t in short), len(short))
    llo, lhi = wilson_interval(sum(t.outcome == "WIN" for t in long), len(long))
    state: WatchdogState = "WARMING"
    reasons = ["WATCHDOG_SAMPLE_WARMUP"]
    historic = [
        r.assessment.historicalLow95
        for r in snapshot.rules
        if r.action == "ALLOW" and r.assessment.historicalLow95 is not None
    ]
    if any(
        getattr(t, k) != SOURCE_VERSIONS[k]
        for t in rows
        for k in (
            "featureVersion",
            "regimeVersion",
            "strategyVersion",
            "rankingVersion",
            "paperVersion",
        )
    ):
        state, reasons = "VERSION_MISMATCH", ["OUTCOME_VERSION_MISMATCH"]
    elif not historic:
        state, reasons = "INSUFFICIENT_DATA", ["NO_VALIDATED_REFERENCE"]
    elif len(binary) >= settings.watchdogWarmupTrades:
        state, reasons = "HEALTHY", ["WITHIN_HISTORICAL_INTERVAL"]
        reference = min(historic)
        bad_short = shi is not None and shi + settings.minEffect < reference
        bad_long = lhi is not None and lhi + settings.minEffect < reference
        if bad_short or bad_long:
            state, reasons = "WARNING", ["DEGRADATION_PENDING_LONG_WINDOW"]
        if bad_short and bad_long and len(long) >= settings.longWindow:
            state, reasons = "DRIFTED", ["BOTH_WINDOWS_BELOW_HISTORICAL_INTERVAL"]
        if settings.requireMonetary and (
            len({t.paperCurrency for t in long}) != 1
            or any(
                t.paperCurrency is None
                or t.paperStake is None
                or t.paperPayoutRate is None
                or t.realizedPaperPnl is None
                for t in long
            )
        ):
            state, reasons = (
                "ACCOUNTING_UNAVAILABLE",
                ["MONETARY_CURRENCY_OR_ACCOUNTING_UNVERIFIED"],
            )
    values = dict(
        snapshotId=snapshot.snapshotId,
        activationEventId=activation.eventId,
        asOf=at,
        state=state,
        sampleCount=len(binary),
        shortLow95=slo,
        shortHigh95=shi,
        longLow95=llo,
        longHigh95=lhi,
        reasons=tuple(reasons),
    )
    return WatchdogSnapshot.model_validate({"watchdogId": identity("watchdog", values), **values})
