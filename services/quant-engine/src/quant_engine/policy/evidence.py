"""Past-only evidence admission. Legacy artifacts become visible on first policy import."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from quant_engine.analytics import build
from quant_engine.analytics.metrics import money_metrics, wilson_interval
from quant_engine.analytics.models import AnalyticsRow, AnalyticsSnapshot
from quant_engine.market_storage import ParquetStorage
from quant_engine.paper.models import PaperTrade
from quant_engine.policy.models import (
    SOURCE_VERSIONS,
    EvidenceAssessment,
    EvidencePeriod,
    EvidenceStatus,
    PolicyEvidence,
    PolicyRule,
    PolicySettings,
    PolicySnapshot,
    Scope,
    fingerprint,
    identity,
)
from quant_engine.policy.rules import candidate_scopes, matches

if TYPE_CHECKING:
    from quant_engine.replay.models import WalkForwardFold
from quant_engine.strategy.models import StrategyEvaluation


def assess(e: PolicyEvidence, settings: PolicySettings, at: int) -> EvidenceAssessment:
    """Wilson uncertainty plus meaningful effect, repeated disjoint OOS support, no pooling trick."""

    def reject(status: EvidenceStatus, reason: str) -> EvidenceAssessment:
        return EvidenceAssessment(evidenceId=e.evidenceId, status=status, reasons=(reason,))

    if dict(e.sourceVersions) != SOURCE_VERSIONS:
        return reject("VERSION_MISMATCH", "SOURCE_VERSION_MISMATCH")
    if e.sourceMode == "SYNTHETIC" or "SYNTHETIC_BEHAVIOR_TEST" in e.warnings:
        return reject("INVALID", "SYNTHETIC_BEHAVIOR_TEST")
    if e.evidenceAvailableAt > at or e.sampleEnd > e.evidenceAvailableAt:
        return reject("INVALID", "EVIDENCE_NOT_AVAILABLE")
    if any(w in e.warnings for w in ("INVALID_SOURCE", "VERSION_MIXED", "MALFORMED_INPUT_ROWS")):
        return reject("INVALID", "INVALID_SOURCE")
    if e.train.sample < settings.minSample:
        return reject("INSUFFICIENT_SAMPLE", "TRAIN_SAMPLE_FLOOR")
    if at - e.sampleEnd > settings.maxEvidenceAgeMs:
        return reject("STALE", "EVIDENCE_EXPIRED")
    if "DRIFTED" in e.warnings:
        return reject("DRIFTED", "EVIDENCE_WITHDRAWN")
    tests = sorted(e.tests, key=lambda p: (p.start, p.end))
    if len(tests) < settings.minOosFolds:
        return reject("DIRECTIONAL_ONLY", "OOS_FOLD_FLOOR")
    if any(t.sample < settings.minTestSample for t in tests):
        return reject("INSUFFICIENT_SAMPLE", "TEST_SAMPLE_FLOOR")
    if (
        tests[0].start <= e.train.end
        or any(b.start <= a.end for a, b in zip(tests, tests[1:], strict=False))
        or any(t.end > e.sampleEnd for t in tests)
    ):
        return reject("INVALID", "OVERLAPPING_OR_FUTURE_FOLDS")
    periods = (e.train, *tests)
    currencies = {p.currency for p in periods}
    monetary = all(p.monetaryVerified for p in periods) and len(currencies) == 1
    if settings.requireMonetary and not monetary:
        return reject("MONETARY_UNVERIFIED", "MONETARY_CURRENCY_OR_ACCOUNTING_UNVERIFIED")
    # Platform/global evidence describes the frozen baseline itself, against chance direction.
    # Specialized evidence measures lift against the same-platform baseline, without a payout claim.
    broad = e.scope.scopeType in ("platform", "global")
    effects = [(p.wins / p.sample - 0.5) if broad else p.lift for p in periods]
    train_effect = effects[0]
    if train_effect is None or abs(train_effect) < settings.minEffect:
        return reject("UNSTABLE", "EFFECT_SIZE_FLOOR")
    sign = 1 if train_effect > 0 else -1
    consistent = sum(v is not None and sign * v >= settings.minEffect for v in effects[1:])
    if consistent <= len(tests) / 2 or any(v is None or sign * v <= 0 for v in effects[1:]):
        return reject("UNSTABLE", "OOS_INSTABILITY")
    if settings.requireMonetary and any(
        p.expectancy is None or sign * p.expectancy <= 0 for p in periods
    ):
        return reject("UNSTABLE", "EXPECTANCY_NOT_CONSISTENT")
    wins, n = sum(t.wins for t in tests), sum(t.sample for t in tests)
    low, high = wilson_interval(wins, n)
    baseline_n = sum(t.baselineWins + t.baselineLosses for t in tests)
    b_low, b_high = (
        (0.5, 0.5) if broad else wilson_interval(sum(t.baselineWins for t in tests), baseline_n)
    )
    if low is None or high is None or b_low is None or b_high is None:
        return reject("INVALID", "MISSING_INTERVAL")
    if (sign > 0 and low <= b_high) or (sign < 0 and high >= b_low):
        return reject("UNSTABLE", "INTERVALS_OVERLAP")
    shares = [(t.wins + t.losses + t.draws) / t.total if t.total else 0 for t in tests]
    coverage = min(shares) if sign > 0 else 1 - max(shares)
    if coverage < settings.minCoverage:
        return reject("UNSTABLE", "COVERAGE_FLOOR")
    return EvidenceAssessment(
        evidenceId=e.evidenceId,
        status="VALIDATED",
        stability="STABLE",
        action="ALLOW" if sign > 0 else "SKIP",
        sampleCount=e.train.sample + n,
        oosFoldCount=len(tests),
        coverage=coverage,
        historicalLow95=low,
        historicalHigh95=high,
        reasons=(
            "STABLE_POSITIVE_OOS" if sign > 0 else "MATERIALLY_ADVERSE_OOS",
            "DIRECTIONAL_EVIDENCE" if not monetary else "SAME_CURRENCY_ACCOUNTING",
        ),
    )


def build_snapshot(
    evidence: Sequence[PolicyEvidence], settings: PolicySettings, cutoff: int
) -> PolicySnapshot:
    # Filter BEFORE fingerprinting: appending future evidence cannot change even the ID.
    visible = sorted(
        {e.evidenceId: e for e in evidence if e.evidenceAvailableAt <= cutoff}.values(),
        key=lambda e: str(e.evidenceId),
    )
    assessments = tuple(assess(e, settings, cutoff) for e in visible)
    rules = []
    for e, a in zip(visible, assessments, strict=True):
        if a.status != "VALIDATED":
            continue
        rules.append(
            PolicyRule(
                ruleId=identity(
                    "rule", [e.model_dump(mode="json"), settings.model_dump(mode="json")]
                ),
                scope=e.scope,
                action=a.action,
                evidence=e,
                assessment=a,
                validFrom=e.evidenceAvailableAt,
                evidenceAvailableAt=e.evidenceAvailableAt,
                expiresAt=e.sampleEnd + settings.maxEvidenceAgeMs,
                reasons=a.reasons,
            )
        )
    # Pick the latest validated source for each exact scope; replay outranks analytics.
    chosen: dict[Scope, PolicyRule] = {}
    for r in sorted(
        rules,
        key=lambda r: (r.evidence.source == "REPLAY_OOS", r.evidenceAvailableAt, str(r.ruleId)),
    ):
        chosen[r.scope] = r
    rules = sorted(chosen.values(), key=lambda r: str(r.ruleId))
    warnings: list[str] = []
    # Conservative union bound: even disjoint veto scopes cannot remove >80% of support.
    # Each platform must have an ALLOW region after subtracting every possible veto overlap.
    for platform in {r.scope.platform for r in rules}:
        group = [r for r in rules if r.scope.platform == platform]
        source = max(
            group,
            key=lambda r: (
                r.evidence.source == "REPLAY_OOS",
                r.evidenceAvailableAt,
                r.evidence.sourceFingerprint,
            ),
        ).evidence
        # Coverage arithmetic must share a dataset; never subtract shares from different histories.
        group = [
            r
            for r in group
            if r.evidence.sourceId == source.sourceId
            and r.evidence.sourceFingerprint == source.sourceFingerprint
        ]
        rules = [r for r in rules if r.scope.platform != platform or r in group]
        allowed = max((r.assessment.coverage or 0 for r in group if r.action == "ALLOW"), default=0)
        removed = sum(1 - (r.assessment.coverage or 0) for r in group if r.action == "SKIP")
        if allowed - removed < settings.minCoverage:
            rules = [r for r in rules if r.scope.platform != platform]
            warnings.append("COMBINED_COVERAGE_FLOOR")
    status: EvidenceStatus = (
        "VALIDATED"
        if rules
        else ("UNSTABLE" if warnings else assessments[0].status if assessments else "NO_EVIDENCE")
    )
    payload = dict(
        createdFromEvidenceIds=tuple(e.evidenceId for e in visible),
        evidenceCutoffTime=cutoff,
        generatedAt=cutoff,
        rules=tuple(rules),
        settings=settings,
        sourceVersions=tuple(sorted(SOURCE_VERSIONS.items())),
        status=status,
        assessments=assessments,
        warnings=tuple(sorted(set(warnings))),
    )
    # Full source content, not only declared IDs, participates in identity.
    key = [[e.model_dump(mode="json") for e in visible], settings.model_dump(mode="json"), cutoff]
    return PolicySnapshot.model_validate({"snapshotId": identity("snapshot", key), **payload})


def period(rows: Sequence[AnalyticsRow], scope: Scope, start: int, end: int) -> EvidencePeriod:
    base = [
        r
        for r in rows
        if start <= r.decisionAvailableAt
        and r.expiryTime <= end
        and (scope.platform is None or r.platform == scope.platform)
    ]
    selected = [r for r in base if matches(scope, r)]
    money = money_metrics(selected)
    priced = (
        bool(selected)
        and money.available
        and not money.mixedCurrency
        and money.excludedByCurrency == 0
        and money.monetaryTrades == len(selected)
        and all(r.paperStake is not None and r.paperPayoutRate is not None for r in selected)
    )
    return EvidencePeriod(
        start=start,
        end=end,
        total=len(base),
        wins=sum(r.won for r in selected),
        losses=sum(r.outcome == "LOSS" for r in selected),
        draws=sum(r.outcome == "DRAW" for r in selected),
        baselineWins=sum(r.won for r in base),
        baselineLosses=sum(r.outcome == "LOSS" for r in base),
        currency=money.currency,
        monetaryVerified=priced,
        expectancy=money.expectancyPerTrade if priced else None,
    )


def from_rows(
    rows: tuple[AnalyticsRow, ...],
    *,
    source_id: object,
    available_at: int,
    folds: Sequence[WalkForwardFold],
    versions: dict[str, str],
    source_mode: str,
    source_hash: str,
    warnings: tuple[str, ...] = (),
) -> tuple[PolicyEvidence, ...]:
    """Fixed candidates from the FIRST TRAIN; every later test is a disjoint unseen window.

    Phase 11's changing best-threshold candidates are never pooled as if they were one rule.
    The raw Phase 10 rows are measured under identical fixed predicates in each test instead.
    """
    ordered = sorted(folds, key=lambda f: (f.testStart, f.foldId))
    if any(
        not (
            f.trainStart
            <= f.trainEnd
            < f.validationStart
            <= f.validationEnd
            < f.testStart
            <= f.testEnd
        )
        for f in ordered
    ):
        warnings += ("INVALID_SOURCE",)
    if ordered:
        start, end = ordered[0].trainStart, ordered[0].trainEnd
    else:
        start = min((r.decisionAvailableAt for r in rows), default=0)
        end = max((r.expiryTime for r in rows), default=0)
    train = tuple(r for r in rows if start <= r.decisionAvailableAt and r.expiryTime <= end)
    scopes = candidate_scopes(train) or (Scope(scopeType="global"),)
    sample_end = max((r.expiryTime for r in rows), default=end)
    result = []
    for scope in scopes:
        data = dict(
            sourceId=source_id,
            source="REPLAY_OOS",
            sourceMode=source_mode,
            evidenceAvailableAt=available_at,
            sampleEnd=sample_end,
            sourceFingerprint=source_hash,
            sourceVersions=tuple(sorted(versions.items())),
            scope=scope,
            train=period(train, scope, start, end),
            tests=tuple(period(rows, scope, f.testStart, f.testEnd) for f in ordered),
            warnings=warnings,
        )
        result.append(
            PolicyEvidence.model_validate({"evidenceId": identity("evidence", data), **data})
        )
    return tuple(result)


def load_replay(folder: Path, imported_at: int) -> tuple[PolicyEvidence, ...]:
    from quant_engine.replay.models import ReplayEvidence, ReplayRun, ReplaySummary

    e = ReplayEvidence.model_validate_json((folder / "evidence.json").read_text())
    run = ReplayRun.model_validate_json((folder / "run.json").read_text())
    if run.status != "COMPLETED" or run.replayRunId != e.replayRunId:
        raise ValueError("Incomplete or mismatched replay")
    if (
        run.inputFingerprint != e.inputFingerprint
        or run.settingsFingerprint != e.settingsFingerprint
    ):
        raise ValueError("Replay fingerprints disagree")
    summary = ReplaySummary.model_validate_json((folder / "summary.json").read_text())
    if (
        summary.partial
        or summary.replayRunId != e.replayRunId
        or summary.replayVersion != e.replayVersion
        or run.replayVersion != e.replayVersion
        or run.sourceMode != e.sourceMode
    ):
        raise ValueError("Incomplete or inconsistent replay provenance")
    versions = {k: getattr(e, k, v) for k, v in SOURCE_VERSIONS.items()}
    storage = ParquetStorage(folder / "market")
    trades = [r for r in storage.reload("paper_trades") if isinstance(r, PaperTrade)]
    evaluations = [
        r for r in storage.reload("strategy_evaluations") if isinstance(r, StrategyEvaluation)
    ]
    # An expiry price may precede its actual resolution availability; never admit on expiry alone.
    trades = [
        t
        for t in trades
        if t.resolvedAtMarketTime is not None and t.resolvedAtMarketTime <= imported_at
    ]
    dataset = build(trades, evaluations)
    resolved = {t.paperTradeId: t.resolvedAtMarketTime for t in trades}
    # Only policy fold membership uses actual outcome availability. Upstream rows/formulas stay frozen.
    available_rows = tuple(
        replace(r, expiryTime=max(r.expiryTime, resolved[r.paperTradeId] or 0))
        for r in dataset.rows
    )
    warnings = tuple(e.warnings)
    if dataset.quality.unsupportedVersions or dataset.quality.malformed:
        warnings += ("INVALID_SOURCE",)
    if any(t.entrySource == "SYNTHETIC" or t.expirySource == "SYNTHETIC" for t in trades):
        warnings += ("SYNTHETIC_BEHAVIOR_TEST",)
    available = max(
        imported_at, run.finishedRuntimeTime or imported_at, run.finishedMarketTime or imported_at
    )
    return from_rows(
        available_rows,
        source_id=e.replayRunId,
        available_at=available,
        folds=e.walkForwardFolds,
        versions=versions,
        source_mode=e.sourceMode,
        source_hash=fingerprint([e.model_dump(mode="json"), dataset.fingerprint]),
        warnings=warnings,
    )


def from_analytics(snapshot: AnalyticsSnapshot, imported_at: int) -> PolicyEvidence:
    """A legacy Phase 10 summary has no repeated OOS provenance: report, never auto-promote."""
    m = snapshot.overallMetrics
    end = snapshot.sampleEnd or 0
    p = EvidencePeriod(
        start=snapshot.sampleStart or 0,
        end=end,
        total=m.resolved,
        wins=m.wins,
        losses=m.losses,
        draws=m.draws,
        baselineWins=m.wins,
        baselineLosses=m.losses,
    )
    data = dict(
        sourceId=snapshot.snapshotId,
        source="ANALYTICS",
        sourceMode="REPLAY",
        evidenceAvailableAt=max(imported_at, end),
        sampleEnd=end,
        sourceFingerprint=fingerprint(snapshot.model_dump(mode="json")),
        sourceVersions=tuple(
            sorted((k, getattr(snapshot, k, v)) for k, v in SOURCE_VERSIONS.items())
        ),
        scope=Scope(scopeType="global"),
        train=p,
        warnings=tuple(snapshot.warnings),
    )
    return PolicyEvidence.model_validate({"evidenceId": identity("evidence", data), **data})
