"""Phase 12 analytical contracts. No field grants a real execution capability."""

from __future__ import annotations

import hashlib
import json
from typing import Literal, Self
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import ConfigDict, Field, model_validator

from quant_engine.configuration import Model, Platform
from quant_engine.strategy.models import Direction, Regime

POLICY_VERSION = "qst-policy-v1"
WATCHDOG_VERSION = "qst-watchdog-v1"
SOURCE_VERSIONS = {
    "featureVersion": "qfe-v2",
    "regimeVersion": "qst-regime-v1",
    "strategyVersion": "qst-strategy-v1",
    "rankingVersion": "qst-ranking-v1",
    "paperVersion": "qst-paper-v1",
    "analyticsVersion": "qst-analytics-v1",
    "replayVersion": "qst-replay-v2",
    "sessionGuardVersion": "qst-session-guard-v1",
}
type PolicyAction = Literal["ALLOW", "WATCH", "SKIP"]
type PolicyMode = Literal["OFF", "SHADOW", "PAPER_GATED"]
type EvidenceStatus = Literal[
    "NO_EVIDENCE",
    "INSUFFICIENT_SAMPLE",
    "UNSTABLE",
    "DIRECTIONAL_ONLY",
    "MONETARY_UNVERIFIED",
    "VALIDATED",
    "STALE",
    "DRIFTED",
    "VERSION_MISMATCH",
    "INVALID",
]
type EvidenceStability = Literal["STABLE", "MIXED", "UNSTABLE", "UNTESTED"]
type ScopeType = Literal[
    "global", "platform", "regime", "strategy-regime", "rankScore", "ensembleConfidence"
]
type WatchdogState = Literal[
    "HEALTHY",
    "WARMING",
    "WARNING",
    "DRIFTED",
    "INSUFFICIENT_DATA",
    "ACCOUNTING_UNAVAILABLE",
    "VERSION_MISMATCH",
]


class Frozen(Model):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


def identity(kind: str, payload: object) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        POLICY_VERSION
        + "/"
        + kind
        + "/"
        + json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False),
    )


def fingerprint(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


class PolicySettings(Frozen):
    mode: PolicyMode = "SHADOW"
    minSample: int = Field(default=100, ge=100, le=100_000)
    minOosFolds: int = Field(default=3, ge=3, le=64)
    minTestSample: int = Field(default=50, ge=50, le=100_000)
    minCoverage: float = Field(default=0.20, ge=0.20, le=1)
    minEffect: float = Field(default=0.05, ge=0.05, le=1)
    maxEvidenceAgeMs: int = Field(default=30 * 86_400_000, ge=1)
    watchdogWarmupTrades: int = Field(default=50, ge=50)
    shortWindow: int = Field(default=50, ge=50)
    longWindow: int = Field(default=200, ge=50, le=10_000)
    requireMonetary: bool = False

    @model_validator(mode="after")
    def windows(self) -> Self:
        if not self.watchdogWarmupTrades <= self.shortWindow <= self.longWindow:
            raise ValueError("warmup <= shortWindow <= longWindow required")
        return self


class Scope(Frozen):
    scopeType: ScopeType
    scopeValue: str = "*"
    # A platform qualifier prevents pooling incompatible 5s and 60s horizons.
    platform: Platform | None = None
    bandStart: float | None = Field(default=None, ge=0, le=1)
    bandEnd: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def valid_scope(self) -> Self:
        if self.scopeType in ("rankScore", "ensembleConfidence"):
            if self.bandStart is None or self.bandEnd is None or self.bandStart >= self.bandEnd:
                raise ValueError("A score band requires increasing bounds")
        elif self.bandStart is not None or self.bandEnd is not None:
            raise ValueError("Only score scopes accept band bounds")
        if self.scopeType == "strategy-regime" and (
            self.scopeValue.count("|") != 1 or not all(self.scopeValue.split("|"))
        ):
            raise ValueError("Strategy-regime scope requires strategy|regime")
        return self


class EvidencePeriod(Frozen):
    start: int
    end: int
    total: int = Field(ge=0)
    wins: int = Field(ge=0)
    losses: int = Field(ge=0)
    draws: int = Field(ge=0)
    baselineWins: int = Field(ge=0)
    baselineLosses: int = Field(ge=0)
    currency: str | None = None
    monetaryVerified: bool = False
    expectancy: float | None = None

    @model_validator(mode="after")
    def counts(self) -> Self:
        if self.end < self.start or self.wins + self.losses + self.draws > self.total:
            raise ValueError("Invalid evidence period")
        if self.baselineWins + self.baselineLosses > self.total:
            raise ValueError("Invalid baseline counts")
        if self.wins > self.baselineWins or self.losses > self.baselineLosses:
            raise ValueError("Scope must be a subset of baseline")
        return self

    @property
    def sample(self) -> int:
        return self.wins + self.losses

    @property
    def lift(self) -> float | None:
        n = self.baselineWins + self.baselineLosses
        return self.wins / self.sample - self.baselineWins / n if n and self.sample else None


class PolicyEvidence(Frozen):
    evidenceId: UUID
    sourceId: UUID
    source: Literal["REPLAY_OOS", "ANALYTICS"]
    sourceMode: Literal["REPLAY", "SYNTHETIC"]
    evidenceAvailableAt: int
    sampleEnd: int
    sourceFingerprint: str
    sourceVersions: tuple[tuple[str, str], ...]
    scope: Scope
    train: EvidencePeriod
    tests: tuple[EvidencePeriod, ...] = ()
    warnings: tuple[str, ...] = ()


class EvidenceAssessment(Frozen):
    evidenceId: UUID
    status: EvidenceStatus
    stability: EvidenceStability = "UNTESTED"
    action: PolicyAction = "WATCH"
    sampleCount: int = 0
    oosFoldCount: int = 0
    coverage: float | None = None
    historicalLow95: float | None = None
    historicalHigh95: float | None = None
    reasons: tuple[str, ...]


class PolicyRule(Frozen):
    ruleId: UUID
    scope: Scope
    action: PolicyAction
    evidence: PolicyEvidence
    assessment: EvidenceAssessment
    validFrom: int
    evidenceAvailableAt: int
    expiresAt: int
    reasons: tuple[str, ...]


class PolicySnapshot(Frozen):
    snapshotId: UUID
    createdFromEvidenceIds: tuple[UUID, ...]
    evidenceCutoffTime: int
    generatedAt: int
    rules: tuple[PolicyRule, ...]
    settings: PolicySettings
    sourceVersions: tuple[tuple[str, str], ...]
    policyVersion: str = POLICY_VERSION
    watchdogVersion: str = WATCHDOG_VERSION
    status: EvidenceStatus
    assessments: tuple[EvidenceAssessment, ...] = ()
    warnings: tuple[str, ...] = ()


class AdaptivePolicyDecision(Frozen):
    decisionId: UUID
    platform: Platform
    slotId: int | None
    assetName: str | None
    contextId: UUID | None
    asOf: int
    decisionAvailableAt: int
    originalPhase8Action: str
    originalDirection: Direction | None
    rankScore: float | None
    ensembleConfidence: float | None
    agreement: float | None
    regime: Regime | None
    regimeConfidence: float | None
    policyAction: PolicyAction
    baselineAction: PolicyAction
    adaptiveAction: PolicyAction
    baseGatePassed: bool
    paperEligible: bool
    mode: PolicyMode
    evidenceStatus: EvidenceStatus
    evidenceId: UUID | None = None
    evidenceAvailableAt: int | None = None
    snapshotId: UUID | None = None
    matchedRules: tuple[UUID, ...] = ()
    vetoes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    reasons: tuple[str, ...]
    policyVersion: str = POLICY_VERSION
    watchdogVersion: str = WATCHDOG_VERSION
    analyticalOnly: Literal[True] = True
    appliedToLiveExecution: Literal[False] = False


class PolicyEvent(Frozen):
    eventId: UUID
    eventType: Literal[
        "POLICY_CREATED",
        "POLICY_ACTIVATED",
        "POLICY_DEACTIVATED",
        "POLICY_ROLLED_BACK",
        "POLICY_WITHDRAWN_DRIFT",
        "POLICY_VERSION_REJECTED",
        "WATCHDOG_WARNING",
        "WATCHDOG_DRIFTED",
    ]
    at: int
    snapshotId: UUID | None
    previousPolicySnapshotId: UUID | None
    mode: PolicyMode
    reasons: tuple[str, ...]
    policyVersion: str = POLICY_VERSION
    watchdogVersion: str = WATCHDOG_VERSION


class WatchdogSnapshot(Frozen):
    watchdogId: UUID
    snapshotId: UUID
    activationEventId: UUID
    asOf: int
    state: WatchdogState
    sampleCount: int
    shortLow95: float | None = None
    shortHigh95: float | None = None
    longLow95: float | None = None
    longHigh95: float | None = None
    reasons: tuple[str, ...]
    policyVersion: str = POLICY_VERSION
    watchdogVersion: str = WATCHDOG_VERSION
