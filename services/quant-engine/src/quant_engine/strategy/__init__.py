"""Regime-aware strategy ensemble (Phase 7).

Consumes the frozen qfe-v2 FeatureBundle and produces analytical opinions: a regime, one
evaluation per strategy, and a weighted ensemble. It places no orders, sizes no stakes,
touches no broker control and manages no bankroll — there is no execution surface anywhere
in this package, and a test asserts it.
"""

from quant_engine.strategy.engine import StrategyEngine, summarize
from quant_engine.strategy.ensemble import CATALOG, evaluate_bundle, evaluate_strategies
from quant_engine.strategy.models import (
    REGIME_VERSION,
    STRATEGY_VERSION,
    SUPPORTED_FEATURE_VERSION,
    VETO_CODES,
    AnalysisStatus,
    Direction,
    EnsembleSnapshot,
    EnsembleWeights,
    Reason,
    Regime,
    RegimeSnapshot,
    SlotStrategyDiagnostics,
    StrategyEvaluation,
    StrategyVote,
)
from quant_engine.strategy.policy import BASE_WEIGHTS, STRATEGY_IDS, policy_for
from quant_engine.strategy.regime import classify

__all__ = [
    "BASE_WEIGHTS",
    "CATALOG",
    "REGIME_VERSION",
    "STRATEGY_IDS",
    "STRATEGY_VERSION",
    "SUPPORTED_FEATURE_VERSION",
    "VETO_CODES",
    "AnalysisStatus",
    "Direction",
    "EnsembleSnapshot",
    "EnsembleWeights",
    "Reason",
    "Regime",
    "RegimeSnapshot",
    "SlotStrategyDiagnostics",
    "StrategyEngine",
    "StrategyEvaluation",
    "StrategyVote",
    "classify",
    "evaluate_bundle",
    "evaluate_strategies",
    "policy_for",
    "summarize",
]
