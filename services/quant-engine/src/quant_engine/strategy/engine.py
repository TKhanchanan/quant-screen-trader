"""Event-driven entry point for the strategy layer.

The engine holds no market state of its own. Phase 5 owns the series, Phase 6 owns the
features, and this layer owns only the last few opinions per slot so the read API and the
desktop diagnostics have something to show between events.

Evaluation is triggered by a closed primary-timeframe feature snapshot, never by a UI poll.
A bundle whose identity and as-of time have already been evaluated returns the stored
snapshot instead of recomputing it, so one primary close produces exactly one ensemble.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from typing import Final
from uuid import UUID

from quant_engine.configuration import Platform
from quant_engine.features.models import FeatureBundle
from quant_engine.strategy.common import market_gate
from quant_engine.strategy.ensemble import evaluate_bundle, evaluate_strategies
from quant_engine.strategy.models import (
    EnsembleSnapshot,
    RegimeSnapshot,
    SlotStrategyDiagnostics,
    StrategyEvaluation,
    StrategyVote,
)
from quant_engine.strategy.regime import classify

HISTORY_CAPACITY: Final = 32
"""Bounded per slot. History is a diagnostic convenience; Parquet is the durable record."""

type SlotKey = tuple[Platform, int]
type Identity = tuple[str, UUID, int]


def _identity(snapshot: EnsembleSnapshot) -> Identity:
    return snapshot.assetName, snapshot.contextId, snapshot.asOf


class StrategyEngine:
    """One instance owns the latest opinions for every live slot."""

    def __init__(self) -> None:
        self.slots: dict[SlotKey, deque[EnsembleSnapshot]] = {}
        self.evaluated = 0
        self.duplicates = 0

    def evaluate(self, bundle: FeatureBundle) -> EnsembleSnapshot:
        """Evaluate one bundle and record it. Repeating a bundle does not repeat the work."""
        key: SlotKey = (bundle.platform, bundle.slotId)
        history = self.slots.get(key)
        wanted: Identity = (bundle.assetName, bundle.contextId, bundle.asOf)
        if history and _identity(history[-1]) == wanted:
            self.duplicates += 1
            return history[-1]
        snapshot = evaluate_bundle(bundle)
        identity = (bundle.assetName, bundle.contextId)
        if history is None or (history[0].assetName, history[0].contextId) != identity:
            # A new asset or context is a new series, exactly as Phase 6 defines identity;
            # nothing from the previous one may survive into it.
            history = deque(maxlen=HISTORY_CAPACITY)
            self.slots[key] = history
        history.append(snapshot)
        self.evaluated += 1
        return snapshot

    def evaluate_regime(self, bundle: FeatureBundle) -> RegimeSnapshot:
        """The regime alone, without running the catalog. Pure, and records nothing."""
        return classify(bundle, market_gate(bundle))

    def evaluate_strategies(
        self, bundle: FeatureBundle, regime: RegimeSnapshot
    ) -> list[StrategyEvaluation]:
        """Every strategy's opinion against a regime already computed. Pure."""
        return evaluate_strategies(bundle, regime, market_gate(bundle))

    def latest(self, platform: Platform, slot_id: int) -> EnsembleSnapshot | None:
        history = self.slots.get((platform, slot_id))
        return history[-1] if history else None

    def recent(self, platform: Platform, slot_id: int, limit: int) -> list[EnsembleSnapshot]:
        history = self.slots.get((platform, slot_id))
        if not history or limit <= 0:
            return []
        return list(history)[-limit:]

    def reset_slot(self, platform: Platform, slot_ids: Iterable[int]) -> int:
        return sum(self.slots.pop((platform, slot_id), None) is not None for slot_id in slot_ids)

    def diagnostics(self) -> list[SlotStrategyDiagnostics]:
        """Compact per-slot summary for the read API and the developer diagnostics line."""
        return [summarize(history[-1]) for history in self.slots.values() if history]


def summarize(snapshot: EnsembleSnapshot) -> SlotStrategyDiagnostics:
    return SlotStrategyDiagnostics(
        platform=snapshot.platform,
        slotId=snapshot.slotId,
        assetName=snapshot.assetName,
        contextId=snapshot.contextId,
        asOf=snapshot.asOf,
        primaryRegime=snapshot.regime.primaryRegime,
        regimeConfidence=snapshot.regime.confidence,
        direction=snapshot.direction,
        confidence=snapshot.confidence,
        eligibleStrategies=snapshot.eligibleStrategies,
        activeStrategies=snapshot.activeStrategies,
        evaluations=len(snapshot.strategies),
        votes=[
            StrategyVote(
                strategyId=item.strategyId,
                direction=item.direction,
                confidence=item.confidence,
            )
            for item in snapshot.strategies
        ],
        vetoes=[veto.code for veto in snapshot.vetoes],
    )
