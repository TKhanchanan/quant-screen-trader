"""Controlled ``EnsembleSnapshot`` fixtures for the ranking tests.

Phase 8's input contract *is* the Phase 7 ensemble, so building one directly here is reading
the contract rather than bypassing a layer — unlike Phase 7, where a hand-written feature set
would have proved a strategy against a bundle it never actually consumes.

Ranking has to be exercised at score combinations Phase 7 cannot be steered into producing on
demand: two candidates a thousandth apart, a whole cohort that is uniformly weak, a mirrored
UP and DOWN pair. Those need constructed inputs. The end-to-end tests that prove Phase 8 sits
behind the real chain drive the real Phase 6 and Phase 7 engines instead.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from features_fixtures import BASE_MS, CONTEXT_A
from quant_engine.configuration import Platform
from quant_engine.market_models import Timeframe
from quant_engine.opportunity import (
    SUPPORTED_FEATURE_VERSION,
    SUPPORTED_REGIME_VERSION,
    SUPPORTED_STRATEGY_VERSION,
)
from quant_engine.strategy.models import (
    AnalysisStatus,
    Direction,
    EnsembleSnapshot,
    EnsembleWeights,
    Regime,
    RegimeSnapshot,
)

EPOCH = BASE_MS
"""One market decision time. Every fixture cohort shares it unless a test moves a slot."""

HORIZON: dict[Platform, Timeframe] = {"capitalbear": "S5", "iqoption": "M1"}
PERIOD_MS: dict[Platform, int] = {"capitalbear": 5_000, "iqoption": 60_000}

ASSETS = {
    1: "EUR/USD OTC",
    2: "GBP/JPY OTC",
    3: "Sui OTC",
    4: "USD/JPY OTC",
    5: "BTC/USD OTC",
    6: "AUD/CAD OTC",
    7: "Gold OTC",
    8: "EUR/GBP OTC",
    9: "Silver OTC",
}


def regime_snapshot(
    *,
    platform: Platform,
    slot: int,
    asset: str,
    context: UUID,
    as_of: int,
    primary: Regime,
    confidence: float,
    noise: float,
    quality_fit: float,
    status: AnalysisStatus,
    regime_version: str,
    feature_version: str,
) -> RegimeSnapshot:
    trend = 0.7 if primary == "TREND_UP" else -0.7 if primary == "TREND_DOWN" else 0.0
    return RegimeSnapshot(
        platform=platform,
        slotId=slot,
        assetName=asset,
        contextId=context,
        asOf=as_of,
        primaryTimeframe=HORIZON[platform],
        featureVersion=feature_version,
        regimeVersion=regime_version,
        primaryRegime=primary,
        trendScore=trend,
        rangeScore=0.2,
        breakoutScore=0.0,
        noiseScore=noise,
        volatilityExpansionScore=0.3,
        volatilityCompressionScore=0.2,
        directionBias="UP" if trend > 0 else "DOWN" if trend < 0 else "NEUTRAL",
        confidence=confidence,
        qualityFit=quality_fit,
        status=status,
    )


def ensemble(
    *,
    platform: Platform = "capitalbear",
    slot: int = 1,
    asset: str | None = None,
    context: UUID = CONTEXT_A,
    as_of: int = EPOCH,
    direction: Direction = "UP",
    confidence: float = 0.60,
    agreement: float = 0.70,
    disagreement: float = 0.0,
    regime: Regime | None = None,
    regime_confidence: float = 0.80,
    noise: float = 0.10,
    quality_fit: float = 1.0,
    eligible: int = 4,
    active: int = 3,
    status: AnalysisStatus = "OK",
    feature_version: str = SUPPORTED_FEATURE_VERSION,
    regime_version: str = SUPPORTED_REGIME_VERSION,
    strategy_version: str = SUPPORTED_STRATEGY_VERSION,
    primary_timeframe: Timeframe | None = None,
) -> EnsembleSnapshot:
    """One Phase 7 opinion with every ranking input under the caller's control.

    Defaults describe an ordinary, clean, agreeing directional read, so a test only states
    the one thing it is actually about.
    """
    resolved_asset = asset if asset is not None else ASSETS[slot]
    resolved_regime: Regime = regime or (
        "TREND_UP" if direction == "UP" else "TREND_DOWN" if direction == "DOWN" else "RANGE"
    )
    return EnsembleSnapshot(
        platform=platform,
        slotId=slot,
        assetName=resolved_asset,
        contextId=context,
        asOf=as_of,
        primaryTimeframe=primary_timeframe or HORIZON[platform],
        featureVersion=feature_version,
        regimeVersion=regime_version,
        strategyVersion=strategy_version,
        regime=regime_snapshot(
            platform=platform,
            slot=slot,
            asset=resolved_asset,
            context=context,
            as_of=as_of,
            primary=resolved_regime,
            confidence=regime_confidence,
            noise=noise,
            quality_fit=quality_fit,
            status=status,
            regime_version=regime_version,
            feature_version=feature_version,
        ),
        strategies=[],
        direction=direction,
        confidence=confidence if direction in ("UP", "DOWN") else 0.0,
        agreement=agreement if direction in ("UP", "DOWN") else 0.0,
        disagreement=disagreement,
        weights=EnsembleWeights(up=1.0, down=0.0, neutral=0.0, active=1.0, net=1.0),
        eligibleStrategies=eligible,
        activeStrategies=active,
        status=status,
    )


def mirrored(direction: Direction, **changes: Any) -> EnsembleSnapshot:
    """The same evidence pointing the other way, so symmetry can be asserted directly."""
    return ensemble(direction=direction, **changes)


def cohort(
    slots: dict[int, float], *, platform: Platform = "capitalbear", **changes: Any
) -> list[EnsembleSnapshot]:
    """One ensemble per slot at one shared close, each with its own confidence."""
    return [
        ensemble(platform=platform, slot=slot, confidence=confidence, **changes)
        for slot, confidence in slots.items()
    ]


def next_epoch(platform: Platform = "capitalbear", periods: int = 1) -> int:
    return EPOCH + PERIOD_MS[platform] * periods
