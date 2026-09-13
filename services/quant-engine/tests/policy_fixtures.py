"""SYNTHETIC_BEHAVIOR_TEST: constructed contract records, never market evidence."""

from __future__ import annotations

from typing import Any

from paper_fixtures import EPOCH
from quant_engine.policy.evidence import build_snapshot
from quant_engine.policy.models import (
    SOURCE_VERSIONS,
    EvidencePeriod,
    PolicyEvidence,
    PolicySettings,
    PolicySnapshot,
    Scope,
    identity,
)

CUTOFF = EPOCH - 10_000


def evidence(*, negative: bool = False, synthetic: bool = False, **changes: Any) -> PolicyEvidence:
    def p(start: int, end: int) -> EvidencePeriod:
        return EvidencePeriod(
            start=start,
            end=end,
            total=1000,
            wins=10 if negative else 800,
            losses=90 if negative else 200,
            draws=0,
            baselineWins=800,
            baselineLosses=200,
            currency="THB",
            monetaryVerified=True,
            expectancy=22,
        )

    data: dict[str, Any] = dict(
        sourceId=identity("test-source", "shared-dataset"),
        source="REPLAY_OOS",
        sourceMode="SYNTHETIC" if synthetic else "REPLAY",
        evidenceAvailableAt=CUTOFF,
        sampleEnd=CUTOFF - 1,
        sourceFingerprint="fixture",
        sourceVersions=tuple(sorted(SOURCE_VERSIONS.items())),
        scope=Scope(scopeType="regime", scopeValue="NOISY", platform="capitalbear")
        if negative
        else Scope(scopeType="platform", scopeValue="capitalbear", platform="capitalbear"),
        train=p(CUTOFF - 4000, CUTOFF - 3001),
        tests=tuple(p(CUTOFF - 3000 + i * 1000, CUTOFF - 2001 + i * 1000) for i in range(3)),
    )
    data.update(changes)
    return PolicyEvidence.model_validate({"evidenceId": identity("test-evidence", data), **data})


def snapshot(*, negative: bool = False, mode: str = "SHADOW") -> PolicySnapshot:
    return build_snapshot(
        [evidence(), *([evidence(negative=True)] if negative else [])],
        PolicySettings.model_validate({"mode": mode}),
        CUTOFF,
    )
