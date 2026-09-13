"""Phase 12 replay adapter around the unchanged v2 driver, with its own output namespace."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

from quant_engine.analytics.metrics import money_metrics, outcome_metrics
from quant_engine.analytics.models import AnalyticsRow
from quant_engine.market_api import MarketEngine
from quant_engine.policy.evidence import build_snapshot
from quant_engine.policy.models import (
    AdaptivePolicyDecision,
    Frozen,
    PolicyEvidence,
    PolicyMode,
    PolicySettings,
    PolicySnapshot,
    identity,
)
from quant_engine.policy.repository import PolicyRepository
from quant_engine.policy.service import PolicyService
from quant_engine.replay.engine import ReplayEngine, ReplayResult, ReplayStorage, ReplayWindow
from quant_engine.replay.models import ReplayManifest, WalkForwardFold
from quant_engine.replay.robustness import drawdown, equity
from quant_engine.replay.source import ReplaySource


class PolicyComparison(Frozen):
    baselineTrades: int
    adaptiveEligible: int
    watched: int
    skipped: int
    baselineSelections: int
    policyAllowed: int
    coverage: float | None
    baselineWins: int
    baselineLosses: int
    baselineDraws: int
    adaptiveWins: int
    adaptiveLosses: int
    adaptiveDraws: int
    baselineWinRate: float | None
    adaptiveWinRate: float | None
    baselineExpectancy: float | None
    adaptiveExpectancy: float | None
    baselineProfitFactor: float | None
    adaptiveProfitFactor: float | None
    baselineMaxDrawdown: float | None
    adaptiveMaxDrawdown: float | None
    currency: str | None
    warnings: tuple[str, ...]


def compare(
    rows: Sequence[AnalyticsRow],
    decisions: Sequence[AdaptivePolicyDecision],
    minimum_coverage: float = 0.20,
) -> PolicyComparison:
    unique = {(d.platform, d.asOf, d.slotId, d.contextId): d for d in decisions if d.baseGatePassed}
    accepted = {key for key, d in unique.items() if d.policyAction == "ALLOW"}
    adaptive = [r for r in rows if (r.platform, r.boardAsOf, r.slotId, r.contextId) in accepted]
    base, selected = outcome_metrics(rows, minimum=100), outcome_metrics(adaptive, minimum=100)
    bm, am = money_metrics(rows), money_metrics(adaptive)
    coverage = len(accepted) / len(unique) if unique else None
    monetary = (
        bm.available
        and am.available
        and bm.currency == am.currency
        and not bm.mixedCurrency
        and not am.mixedCurrency
        and bm.excludedByCurrency == am.excludedByCurrency == 0
        and bm.monetaryTrades == len(rows)
        and am.monetaryTrades == len(adaptive)
    )
    warnings = []
    if coverage is None or coverage < minimum_coverage:
        warnings.append("COVERAGE_FLOOR")
    if not monetary:
        warnings.append("MONETARY_UNVERIFIED")
    return PolicyComparison(
        baselineTrades=len(rows),
        adaptiveEligible=len(adaptive),
        watched=sum(d.policyAction == "WATCH" for d in unique.values()),
        skipped=sum(d.policyAction == "SKIP" for d in unique.values()),
        baselineSelections=len(unique),
        policyAllowed=len(accepted),
        coverage=coverage,
        baselineWins=base.wins,
        baselineLosses=base.losses,
        baselineDraws=base.draws,
        adaptiveWins=selected.wins,
        adaptiveLosses=selected.losses,
        adaptiveDraws=selected.draws,
        baselineWinRate=base.winRateExcludingDraws,
        adaptiveWinRate=selected.winRateExcludingDraws,
        baselineExpectancy=bm.expectancyPerTrade if monetary else None,
        adaptiveExpectancy=am.expectancyPerTrade if monetary else None,
        baselineProfitFactor=bm.profitFactor if monetary else None,
        adaptiveProfitFactor=am.profitFactor if monetary else None,
        baselineMaxDrawdown=drawdown(equity(rows), None)[0] if monetary else None,
        adaptiveMaxDrawdown=drawdown(equity(adaptive), None)[0] if monetary else None,
        currency=bm.currency if monetary else None,
        warnings=tuple(warnings),
    )


class PolicyReplayEngine(ReplayEngine):
    def __init__(
        self,
        manifest: ReplayManifest,
        source: ReplaySource,
        *,
        root: Path,
        evidence: Sequence[PolicyEvidence] = (),
        snapshot: PolicySnapshot | None = None,
        activation_at: int | None = None,
        mode: PolicyMode = "SHADOW",
        persist: bool = False,
    ) -> None:
        self.policyRunId = identity(
            "replay",
            [
                manifest.model_dump(mode="json"),
                source.prepare().inputFingerprint,
                snapshot.snapshotId if snapshot else None,
                activation_at,
                mode,
            ],
        )
        isolated = root / "policy_replay" / str(self.policyRunId)
        repo = PolicyRepository(isolated / "journal.sqlite3" if persist else None)
        self.policyService = PolicyService(repo, default_mode=mode)
        if snapshot is not None:
            if activation_at is None or activation_at < snapshot.generatedAt:
                raise ValueError("Explicit past-only activation time required")
            created = self.policyService.create(
                list(evidence), snapshot.settings, snapshot.evidenceCutoffTime, snapshot.generatedAt
            )
            if created != snapshot:
                raise ValueError("Snapshot does not match supplied evidence")
            self.policyService.activate(snapshot.snapshotId, mode, activation_at)
        super().__init__(manifest, source, root=isolated, persist=persist)

    def _engine(self, storage: ReplayStorage, window: ReplayWindow) -> MarketEngine:
        engine = super()._engine(storage, window)
        engine.policy = self.policyService
        return engine

    def comparison(self, result: ReplayResult) -> PolicyComparison:
        if self.policyService.defaultMode == "PAPER_GATED":
            raise ValueError("Use a SHADOW replay to measure baseline counterfactual outcomes")
        decisions = [
            d
            for d in self.policyService.repository.records("decision", AdaptivePolicyDecision)
            if result.window.evaluationStart <= d.decisionAvailableAt <= result.window.evaluationEnd
        ]
        comparison = compare(result.analysis.rows if result.analysis else (), decisions)
        if result.dataset.sourceMode == "SYNTHETIC":
            comparison = comparison.model_copy(
                update={"warnings": (*comparison.warnings, "SYNTHETIC_BEHAVIOR_TEST")}
            )
        return comparison


class PolicyFoldResult(Frozen):
    foldId: int
    snapshotId: UUID
    trainCutoff: int
    evidenceStatus: str
    validation: PolicyComparison
    test: PolicyComparison
    warnings: tuple[str, ...]


def walk_forward(
    manifest: ReplayManifest,
    source: ReplaySource,
    *,
    root: Path,
    folds: Sequence[WalkForwardFold],
    evidence: Sequence[PolicyEvidence],
    settings: PolicySettings | None = None,
) -> tuple[PolicyFoldResult, ...]:
    """TRAIN-visible evidence -> snapshot -> freeze -> VALIDATION/TEST, all folds reported.

    Nested OOS receipts must already have completed within TRAIN. TEST results are never
    handed to build_snapshot, and a replay generated later cannot be backdated into TRAIN.
    """
    settings = settings or PolicySettings()
    results = []
    for fold in sorted(folds, key=lambda f: f.foldId):
        if not (
            fold.trainStart
            <= fold.trainEnd
            < fold.validationStart
            <= fold.validationEnd
            < fold.testStart
            <= fold.testEnd
        ):
            raise ValueError("Policy fold windows must be chronological and disjoint")
        past = [
            e
            for e in evidence
            if e.evidenceAvailableAt <= fold.trainEnd and e.sampleEnd <= fold.trainEnd
        ]
        snapshot = build_snapshot(past, settings, fold.trainEnd)
        comparisons = []
        for start, end in (
            (fold.validationStart, fold.validationEnd),
            (fold.testStart, fold.testEnd),
        ):
            spec = manifest.model_copy(update={"fromTime": start, "toTime": end})
            replay = PolicyReplayEngine(
                spec,
                source,
                root=root,
                evidence=past,
                snapshot=snapshot,
                activation_at=fold.validationStart,
                mode="SHADOW",
            )
            result = replay.run()
            if result.run.status != "COMPLETED":
                raise ValueError("Incomplete policy walk-forward replay")
            comparisons.append(replay.comparison(result))
        results.append(
            PolicyFoldResult(
                foldId=fold.foldId,
                snapshotId=snapshot.snapshotId,
                trainCutoff=fold.trainEnd,
                evidenceStatus=snapshot.status,
                validation=comparisons[0],
                test=comparisons[1],
                warnings=("SHADOW_COUNTERFACTUAL", "SYNTHETIC_BEHAVIOR_TEST")
                if manifest.sourceMode == "SYNTHETIC"
                else ("SHADOW_COUNTERFACTUAL",),
            )
        )
    return tuple(results)
