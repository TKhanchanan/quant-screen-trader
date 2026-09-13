"""Offline Phase 12 acceptance over a supplied recorded market-data directory."""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path

from quant_engine.policy.models import AdaptivePolicyDecision, PolicySettings
from quant_engine.policy.replay import PolicyReplayEngine
from quant_engine.policy.service import PolicyService
from quant_engine.replay import ReplayEngine, ReplayManifest
from quant_engine.replay.source import ParquetObservationSource


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    spec = ReplayManifest()  # Full qfe warm-up derived by v2; no threshold or maturity override.
    source = ParquetObservationSource(args.source)
    baseline = ReplayEngine(spec, source, root=args.output / "baseline", persist=False).run()
    shadow = PolicyReplayEngine(spec, source, root=args.output, persist=False)
    result = shadow.run()
    assert baseline.run.status == result.run.status == "COMPLETED"
    assert baseline.analysis is not None and result.analysis is not None
    assert baseline.analysis.fingerprint == result.analysis.fingerprint
    assert baseline.trades == result.trades
    assert baseline.boards == result.boards
    assert baseline.causality == result.causality
    at = int(time.time() * 1000)
    candidate = PolicyService().rebuild(args.source, at, at, PolicySettings())
    decisions = shadow.policyService.repository.records("decision", AdaptivePolicyDecision)
    report = dict(
        label="RECORDED_HISTORY_ACCEPTANCE",
        policyVersion="qst-policy-v1",
        replayVersion="qst-replay-v2",
        sourceFingerprint=result.dataset.inputFingerprint,
        events=result.dataset.events,
        startTime=result.dataset.startTime,
        endTime=result.dataset.endTime,
        warmupDurationMs=result.window.warmupDurationMs,
        baselineUnchanged=True,
        baselineSelections=result.run.boardsSelected,
        baselineResolved=result.run.paperResolved,
        shadowDecisions=len(decisions),
        shadowActions=dict(Counter(d.policyAction for d in decisions)),
        evidenceStatus=candidate.status,
        candidatesConsidered=len(candidate.assessments),
        rejectedByStatus=dict(
            Counter(a.status for a in candidate.assessments if a.status != "VALIDATED")
        ),
        rejectedByReason=dict(
            Counter(
                reason
                for a in candidate.assessments
                if a.status != "VALIDATED"
                for reason in a.reasons
            )
        ),
        validatedRules=len(candidate.rules),
        oosFolds=max((a.oosFoldCount for a in candidate.assessments), default=0),
        comparison=shadow.comparison(result).model_dump(mode="json"),
        causalityViolations=result.causality.violations,
        warnings=list(candidate.warnings),
    )
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "real-acceptance.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
