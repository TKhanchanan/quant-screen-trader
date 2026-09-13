"""SYNTHETIC_BEHAVIOR_TEST: exact baseline equivalence and historical snapshot causality."""

from pathlib import Path

import replay_fixtures as fixtures
from quant_engine.policy.models import AdaptivePolicyDecision, PolicySettings
from quant_engine.policy.replay import PolicyReplayEngine, walk_forward
from quant_engine.replay import InMemoryObservationSource
from quant_engine.replay.models import FoldMetrics, WalkForwardFold
from test_replay_engine import manifest, replay, signature


def test_shadow_and_off_baseline_equivalence(tmp_path: Path) -> None:
    rows = fixtures.small_history()
    base = replay(rows, tmp_path / "baseline").run()
    for mode in ("SHADOW", "OFF"):
        runner = PolicyReplayEngine(
            manifest(),
            InMemoryObservationSource(rows, mode="SYNTHETIC"),
            root=tmp_path / mode,
            mode=mode,
        )
        result = runner.run()
        assert signature(base) == signature(result)
        decisions = runner.policyService.repository.records("decision", AdaptivePolicyDecision)
        if mode == "SHADOW":
            assert decisions
            assert all(d.policyAction != "ALLOW" for d in decisions)
            report = runner.comparison(result)
            assert report.baselineTrades == len(result.analysis.rows) if result.analysis else 0
            assert report.policyAllowed == 0
        else:
            assert decisions == []


def test_walk_forward_never_uses_test_evidence(tmp_path: Path) -> None:
    import policy_fixtures as evidence_fixtures

    rows = fixtures.small_history()
    start = fixtures.BASE_MS
    metrics = FoldMetrics(period="TRAIN", total=0, selected=0, resolved=0)
    fold = WalkForwardFold(
        foldId=1,
        trainStart=start,
        trainEnd=start + 100_000,
        validationStart=start + 110_000,
        validationEnd=start + 200_000,
        testStart=start + 210_000,
        testEnd=start + 400_000,
        purgeMs=10_000,
        embargoMs=10_000,
        train=metrics,
        validation=metrics.model_copy(update={"period": "VALIDATION"}),
        test=metrics.model_copy(update={"period": "TEST"}),
    )
    source = InMemoryObservationSource(rows, mode="SYNTHETIC")
    original = evidence_fixtures.evidence()
    shift = fold.trainEnd - 1 - original.evidenceAvailableAt
    past = evidence_fixtures.evidence(
        evidenceAvailableAt=original.evidenceAvailableAt + shift,
        sampleEnd=original.sampleEnd + shift,
        train=original.train.model_copy(
            update={"start": original.train.start + shift, "end": original.train.end + shift}
        ),
        tests=tuple(
            p.model_copy(update={"start": p.start + shift, "end": p.end + shift})
            for p in original.tests
        ),
    )
    first = walk_forward(manifest(), source, root=tmp_path, folds=[fold], evidence=[past])
    later = evidence_fixtures.evidence(evidenceAvailableAt=fold.testEnd + 1)
    second = walk_forward(
        manifest(),
        source,
        root=tmp_path,
        folds=[fold],
        evidence=[past, later],
        settings=PolicySettings(),
    )
    assert first == second
    assert first[0].evidenceStatus == "VALIDATED"
    changed = [
        r.model_copy(update={"price": r.price * 2})
        if r.price is not None and int(r.observedAt.timestamp() * 1000) > fold.testStart
        else r
        for r in rows
    ]
    third = walk_forward(
        manifest(),
        InMemoryObservationSource(changed, mode="SYNTHETIC"),
        root=tmp_path,
        folds=[fold],
        evidence=[past],
    )
    assert third[0].snapshotId == first[0].snapshotId
    assert third[0].validation == first[0].validation
