"""Deterministic resolved outcomes for the analytics tests.

Phase 10's input contract *is* a finished ``PaperTrade``, so building one directly here is
reading the contract rather than bypassing a layer. The properties this layer has to be proved
against — a score band that is genuinely stronger, an inverted one, a regime that behaves
differently from its neighbour, a strategy that is right only in trends — cannot be steered out
of a live capture on demand, and a calibration test driven by whatever the market happened to do
would assert nothing.

Two tests drive the real thing instead: the pipeline test runs the whole Phase 5-9 chain from
broker-shaped observations and analyses whatever it produced, and the storage test round-trips
through Parquet.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from quant_engine.analytics import (
    SUPPORTED_FEATURE_VERSION,
    SUPPORTED_PAPER_VERSION,
    SUPPORTED_RANKING_VERSION,
    SUPPORTED_REGIME_VERSION,
    SUPPORTED_STRATEGY_VERSION,
)
from quant_engine.configuration import Platform
from quant_engine.paper.models import PaperTrade
from quant_engine.strategy.models import Direction, Regime, StrategyEvaluation

BASE_MS = int(datetime(2026, 9, 1, 3, 0, tzinfo=UTC).timestamp() * 1000)
"""03:00 UTC is 10:00 in Asia/Bangkok, so the default hour bucket is unambiguous and the
timezone test has a known answer that is not the same number in both zones."""

STEP_MS = 60_000
CONTEXT = UUID("33333333-3333-4333-8333-333333333333")
STRATEGIES = (
    "trend_follow_v1",
    "momentum_continuation_v1",
    "breakout_v1",
    "mean_reversion_v1",
    "micro_impulse_v1",
    "trend_pullback_v1",
)
ASSETS = ("EUR/USD OTC", "GBP/JPY OTC", "Gold OTC")


def identity(label: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"qst-analytics-test/{label}")


def trade(
    label: str,
    *,
    outcome: str = "WIN",
    platform: Platform = "capitalbear",
    slot: int = 1,
    asset: str = "EUR/USD OTC",
    direction: str = "UP",
    rank_score: float = 0.6,
    confidence: float = 0.7,
    agreement: float = 0.6,
    regime: Regime = "TREND_UP",
    regime_confidence: float = 0.7,
    lead_margin: float | None = 0.12,
    expiry: int | None = None,
    stake: float | None = 50.0,
    payout: float | None = 0.8,
    currency: str | None = "THB",
    status: str = "RESOLVED",
    context: UUID = CONTEXT,
    delta_bps: float | None = None,
    **changes: Any,
) -> PaperTrade:
    """One resolved paper trade with everything a calibration could group by.

    ``realizedPaperPnl`` follows the Phase 9 accounting rule exactly — a win returns
    ``stake * payout``, a loss the whole stake, a draw zero — so a money metric computed over
    these fixtures is checking the same arithmetic the engine actually recorded.
    """
    resolved_at = BASE_MS if expiry is None else expiry
    entry = resolved_at - 5_000
    priced = stake is not None and payout is not None and currency is not None
    money = None
    if priced and status == "RESOLVED" and stake is not None and payout is not None:
        money = {"WIN": stake * payout, "LOSS": -stake, "DRAW": 0.0}.get(outcome)
    movement = delta_bps
    if movement is None and status == "RESOLVED":
        movement = {"WIN": 4.0, "LOSS": -4.0, "DRAW": 0.0}.get(outcome, 0.0)
    values: dict[str, Any] = {
        "paperTradeId": identity(label),
        "platform": platform,
        "slotId": slot,
        "assetName": asset,
        "contextId": context,
        "direction": direction,
        "boardAsOf": entry - 1_200,
        "decisionAvailableAt": entry - 200,
        "rank": 1,
        "rankScore": rank_score,
        "ensembleConfidence": confidence,
        "agreement": agreement,
        "primaryRegime": regime,
        "regimeConfidence": regime_confidence,
        "leadMargin": lead_margin,
        "boardStatus": "READY",
        "entryTime": entry if status in ("OPEN", "RESOLVED") else None,
        "entryPrice": 1.0842 if status in ("OPEN", "RESOLVED") else None,
        "entrySource": "SYNTHETIC" if status in ("OPEN", "RESOLVED") else None,
        "entryQuality": "GOOD" if status in ("OPEN", "RESOLVED") else None,
        "durationMs": 5_000,
        "expiryTargetTime": resolved_at if status in ("OPEN", "RESOLVED") else None,
        "expiryTime": resolved_at if status == "RESOLVED" else None,
        "expiryPrice": 1.0847 if status == "RESOLVED" else None,
        "expirySource": "SYNTHETIC" if status == "RESOLVED" else None,
        "expiryQuality": "GOOD" if status == "RESOLVED" else None,
        "priceDelta": 0.0005 if status == "RESOLVED" else None,
        "priceDeltaBps": movement,
        "status": status,
        "outcome": outcome if status == "RESOLVED" else "UNRESOLVED",
        "paperCurrency": currency if priced else None,
        "paperStake": stake if priced else None,
        "paperPayoutRate": payout if priced else None,
        "realizedPaperPnl": money,
        "featureVersion": SUPPORTED_FEATURE_VERSION,
        "regimeVersion": SUPPORTED_REGIME_VERSION,
        "strategyVersion": SUPPORTED_STRATEGY_VERSION,
        "rankingVersion": SUPPORTED_RANKING_VERSION,
        "paperVersion": SUPPORTED_PAPER_VERSION,
        "createdAtMarketTime": entry - 200,
        "resolvedAtMarketTime": resolved_at if status == "RESOLVED" else None,
        "reasons": ["BOARD_SELECTION"],
        "invalidReasons": [],
    }
    values.update(changes)
    return PaperTrade.model_validate(values)


def evaluation(
    source: PaperTrade,
    strategy_id: str,
    direction: Direction,
    *,
    confidence: float = 0.6,
    eligible: bool = True,
) -> StrategyEvaluation:
    """One Phase 7 vote positioned on the exact decision a paper trade was taken from."""
    return StrategyEvaluation(
        strategyId=strategy_id,
        strategyVersion=SUPPORTED_STRATEGY_VERSION,
        platform=source.platform,
        slotId=source.slotId,
        assetName=source.assetName,
        contextId=source.contextId,
        asOf=source.boardAsOf,
        eligible=eligible,
        direction=direction,
        confidence=confidence,
        rawScore=0.4 if direction == "UP" else -0.4 if direction == "DOWN" else 0.0,
        regimeFit=0.8,
        qualityFit=0.9,
        platformFit=1.0,
        evidenceCoverage=0.9,
        featureVersion=SUPPORTED_FEATURE_VERSION,
        regimeVersion=SUPPORTED_REGIME_VERSION,
    )


def outcomes(pattern: str, count: int) -> list[str]:
    """Repeat a W/L/D pattern to a length, so a win rate is exact rather than approximate."""
    letters = {"W": "WIN", "L": "LOSS", "D": "DRAW"}
    return [letters[pattern[index % len(pattern)]] for index in range(count)]


def sequence(
    outcome_list: list[str],
    *,
    start: int = BASE_MS,
    step: int = STEP_MS,
    label: str = "seq",
    **changes: Any,
) -> list[PaperTrade]:
    """Chronological trades, one per step, so a temporal split has an unambiguous order."""
    return [
        trade(f"{label}-{index}", outcome=value, expiry=start + index * step, **changes)
        for index, value in enumerate(outcome_list)
    ]


def scatter(index: int) -> int:
    """A fixed, decorrelated 0-99 draw for an index.

    Deliberately not ``random``: the whole point of these fixtures is that the same corpus is
    produced on every machine and every run. It is also deliberately not another small modulus —
    an outcome keyed on ``index % 3`` beside a score keyed on ``index % 6`` makes the score
    *determine* the outcome, and a calibration test over a dataset like that proves only that
    the fixture leaked the answer.
    """
    return (index * 37 + 11) % 100


def monotone(count: int = 400) -> list[PaperTrade]:
    """A history where each successive rank-score band really does win more often.

    Used where the property under test is the diagnostic itself — a monotonic curve has to be
    reported as monotonic before a non-monotonic one reported as non-monotonic means anything.
    """
    rows: list[PaperTrade] = []
    for index in range(count):
        band = index % 5
        score = 0.12 + band * 0.2
        target = 30 + band * 12
        outcome = "WIN" if scatter(index) < target else "LOSS"
        rows.append(
            trade(
                f"monotone-{index}",
                outcome=outcome,
                rank_score=round(score, 4),
                confidence=round(score, 4),
                expiry=BASE_MS + index * STEP_MS,
            )
        )
    return rows


def corpus() -> tuple[list[PaperTrade], list[StrategyEvaluation]]:
    """A deterministic multi-platform, multi-asset, multi-regime, multi-strategy history.

    Built with two properties deliberately true and checkable. Outcomes above a ``rankScore``
    of 0.70 are genuinely stronger than the ones below, consistently across the whole timeline,
    so a threshold search that finds nothing there is a broken search rather than an honest
    report. And ``ensembleConfidence`` carries no relationship to outcomes at all, because a
    calibration layer that can only describe a score that works is not a calibration layer —
    reporting the second one as unhelpful is as much a required behaviour as finding the first.
    """
    trades: list[PaperTrade] = []
    evaluations: list[StrategyEvaluation] = []
    regimes: tuple[Regime, ...] = ("TREND_UP", "RANGE", "NOISY", "TREND_DOWN")
    for index in range(600):
        strong = index % 5 < 2
        rank_score = 0.74 + (index % 4) * 0.05 if strong else 0.22 + (index % 6) * 0.06
        # Roughly two wins in three above the band, one in three below it. Deliberately not a
        # clean sweep: a fixture where the strong band never loses would let a broken threshold
        # search look correct, and would not resemble anything this system will ever record.
        won = scatter(index) < (67 if strong else 34)
        outcome = "DRAW" if index % 97 == 0 else ("WIN" if won else "LOSS")
        # Platform and asset cycle on coprime periods, so every broker/asset pair really occurs.
        # Aligning them would quietly make "asset" and "platform" the same column.
        platform: Platform = "iqoption" if index % 4 == 0 else "capitalbear"
        regime = regimes[index % len(regimes)]
        direction: Direction = "UP" if index % 2 else "DOWN"
        row = trade(
            f"corpus-{index}",
            outcome=outcome,
            platform=platform,
            slot=1 + index % 9,
            asset=ASSETS[index % len(ASSETS)],
            direction=direction,
            rank_score=min(rank_score, 0.99),
            confidence=min(0.30 + (index % 7) * 0.10, 0.99),
            agreement=min(0.10 + (index % 9) * 0.10, 0.99),
            regime=regime,
            regime_confidence=min(0.25 + (index % 8) * 0.09, 0.99),
            lead_margin=round(0.02 + (index % 5) * 0.07, 4),
            expiry=BASE_MS + index * STEP_MS,
        )
        trades.append(row)
        for position, strategy_id in enumerate(STRATEGIES):
            # trend_follow agrees in trends and abstains elsewhere; the rest vary.
            vote: Direction
            if strategy_id == "trend_follow_v1":
                vote = direction if regime.startswith("TREND") else "SKIP"
            elif position % 2 == index % 2:
                vote = direction
            elif position % 3 == 0:
                vote = "DOWN" if direction == "UP" else "UP"
            else:
                vote = "NEUTRAL"
            evaluations.append(evaluation(row, strategy_id, vote, confidence=0.4 + position / 20))
    return (trades, evaluations)
