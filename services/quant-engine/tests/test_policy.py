"""Phase 12 acceptance using SYNTHETIC_BEHAVIOR_TEST contract fixtures."""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path
from typing import Any

import analytics_fixtures as outcomes
import paper_fixtures as paper
import policy_fixtures as fixtures
import pytest
from pydantic import ValidationError
from quant_engine.market_api import MarketEngine
from quant_engine.market_storage import ParquetStorage
from quant_engine.policy.drift import evaluate_watchdog
from quant_engine.policy.engine import PolicyEngine
from quant_engine.policy.evidence import assess, build_snapshot, from_rows
from quant_engine.policy.models import (
    SOURCE_VERSIONS,
    AdaptivePolicyDecision,
    PolicyEvent,
    PolicySettings,
    PolicySnapshot,
    identity,
)
from quant_engine.policy.repository import PolicyRepository
from quant_engine.policy.service import PolicyService
from quant_engine.replay.models import FoldMetrics, WalkForwardFold
from quant_engine.strategy.models import Direction


def evaluate(board: Any = None, value: Any = None, **kwargs: Any) -> AdaptivePolicyDecision:
    return PolicyEngine().evaluate(
        board or paper.board(), value or fixtures.snapshot(), paper.EPOCH + 1200, **kwargs
    )


@pytest.mark.parametrize("status", ["NO_OPPORTUNITY", "PARTIAL", "COLLECTING", "INVALID"])
def test_phase8_rejection_cannot_be_resurrected(status: str) -> None:
    b = paper.board().model_copy(update={"status": status})
    result = evaluate(b)
    assert result.policyAction == "SKIP" and not result.paperEligible
    assert result.reasons


@pytest.mark.parametrize("direction", ["UP", "DOWN"])
def test_direction_score_confidence_and_board_immutable(direction: Direction) -> None:
    b = paper.board(direction=direction)
    before = b.model_dump_json()
    d = evaluate(b)
    assert d.policyAction == "ALLOW"
    assert d.originalDirection == direction
    assert d.rankScore == b.selectedScore
    assert b.model_dump_json() == before
    assert d == evaluate(b)


def test_watch_and_no_selected_slot_stay_rejected() -> None:
    b = paper.board().model_copy(update={"selectedSlotId": None})
    assert evaluate(b).policyAction == "SKIP"
    b = paper.board()
    b.candidates[0].candidateStatus = "WATCH"
    assert evaluate(b).policyAction == "SKIP"


def test_no_evidence_is_conservative_and_shadow_preserves_paper() -> None:
    d = PolicyEngine().evaluate(paper.board(), None, paper.EPOCH + 1200)
    assert d.policyAction == "WATCH" and d.evidenceStatus == "NO_EVIDENCE"
    assert d.paperEligible and d.mode == "SHADOW"
    d = PolicyEngine().evaluate(paper.board(), None, paper.EPOCH + 1200, mode="PAPER_GATED")
    assert not d.paperEligible


def test_negative_evidence_veto_and_precedence() -> None:
    d = evaluate(paper.board(regime="NOISY"), fixtures.snapshot(negative=True))
    assert d.policyAction == "SKIP" and d.evidenceStatus == "VALIDATED"
    assert "MATERIALLY_ADVERSE_OOS" in d.reasons
    assert evaluate(value=fixtures.snapshot(negative=True)).policyAction == "ALLOW"


@pytest.mark.parametrize(
    "mutation,status",
    [
        ({"sourceMode": "SYNTHETIC"}, "INVALID"),
        ({"warnings": ("SYNTHETIC_BEHAVIOR_TEST",)}, "INVALID"),
        (
            {
                "sourceVersions": tuple(
                    (k, "qst-replay-v1" if k == "replayVersion" else v)
                    for k, v in sorted(SOURCE_VERSIONS.items())
                )
            },
            "VERSION_MISMATCH",
        ),
        ({"tests": ()}, "DIRECTIONAL_ONLY"),
        ({"sampleEnd": fixtures.CUTOFF - 40 * 86_400_000}, "STALE"),
        ({"warnings": ("DRIFTED",)}, "DRIFTED"),
    ],
)
def test_explicit_evidence_statuses(mutation: dict[str, Any], status: str) -> None:
    e = fixtures.evidence(**mutation)
    assert assess(e, PolicySettings(), fixtures.CUTOFF).status == status
    assert not build_snapshot([e], PolicySettings(), fixtures.CUTOFF).rules


def test_sample_floor_and_bad_oos_cannot_activate() -> None:
    e = fixtures.evidence()
    tiny = e.train.model_copy(update={"wins": 10, "losses": 2})
    assert (
        assess(fixtures.evidence(train=tiny), PolicySettings(), fixtures.CUTOFF).status
        == "INSUFFICIENT_SAMPLE"
    )
    bad = tuple(
        t.model_copy(
            update={"wins": 200, "losses": 800, "baselineWins": 200, "baselineLosses": 800}
        )
        for t in e.tests
    )
    assert (
        assess(fixtures.evidence(tests=bad), PolicySettings(), fixtures.CUTOFF).status == "UNSTABLE"
    )
    narrow = tuple(
        t.model_copy(
            update={"wins": 500, "losses": 500, "baselineWins": 500, "baselineLosses": 500}
        )
        for t in e.tests
    )
    assert (
        assess(fixtures.evidence(tests=narrow), PolicySettings(), fixtures.CUTOFF).status
        == "UNSTABLE"
    )


def test_uncertainty_not_point_rate_drives_veto() -> None:
    e = fixtures.evidence(negative=True)
    weak = tuple(
        t.model_copy(
            update={
                "wins": 498,
                "losses": 502,
                "total": 2000,
                "baselineWins": 1000,
                "baselineLosses": 1000,
            }
        )
        for t in e.tests
    )
    result = assess(fixtures.evidence(negative=True, tests=weak), PolicySettings(), fixtures.CUTOFF)
    assert result.status == "UNSTABLE"


def test_currency_train_thb_test_usd_is_unverified() -> None:
    e = fixtures.evidence()
    mixed = fixtures.evidence(
        tests=tuple(t.model_copy(update={"currency": "USD"}) for t in e.tests)
    )
    assert (
        assess(mixed, PolicySettings(requireMonetary=True), fixtures.CUTOFF).status
        == "MONETARY_UNVERIFIED"
    )
    assert assess(mixed, PolicySettings(), fixtures.CUTOFF).status == "VALIDATED"
    assert "DIRECTIONAL_EVIDENCE" in assess(mixed, PolicySettings(), fixtures.CUTOFF).reasons


def test_coverage_individual_and_combined() -> None:
    e = fixtures.evidence(negative=True)
    periods = tuple(
        t.model_copy(
            update={
                "wins": 100,
                "losses": 9800,
                "total": 10000,
                "baselineWins": 200,
                "baselineLosses": 9800,
            }
        )
        for t in e.tests
    )
    result = assess(
        fixtures.evidence(negative=True, tests=periods), PolicySettings(), fixtures.CUTOFF
    )
    assert result.status != "VALIDATED"
    s = build_snapshot([e], PolicySettings(), fixtures.CUTOFF)
    assert not s.rules and "COMBINED_COVERAGE_FLOOR" in s.warnings


def test_future_evidence_not_even_in_snapshot_identity() -> None:
    e = fixtures.evidence()
    before = build_snapshot([e], PolicySettings(), fixtures.CUTOFF)
    future = fixtures.evidence(evidenceAvailableAt=fixtures.CUTOFF + 1)
    after = build_snapshot([future, e], PolicySettings(), fixtures.CUTOFF)
    assert before == after
    assert build_snapshot([e, future], PolicySettings(), fixtures.CUTOFF) == after
    d = PolicyEngine().evaluate(paper.board(), before, fixtures.CUTOFF - 1)
    assert d.evidenceId is None and d.snapshotId is None


def test_snapshot_version_and_quality_fail_closed() -> None:
    s = fixtures.snapshot().model_copy(update={"policyVersion": "future"})
    assert evaluate(value=s).policyAction == "SKIP"
    b = paper.board().model_copy(update={"rankingVersion": "future"})
    assert evaluate(b).policyAction == "SKIP"
    b = paper.board(regime="UNCERTAIN")
    assert evaluate(b).policyAction == "SKIP"


def configured(
    tmp_path: Path, *, mode: str = "PAPER_GATED", negative: bool = False
) -> PolicyService:
    service = PolicyService(PolicyRepository(tmp_path / "policy.sqlite3"))
    evidence = [fixtures.evidence(), *([fixtures.evidence(negative=True)] if negative else [])]
    s = service.create(evidence, PolicySettings(), fixtures.CUTOFF, fixtures.CUTOFF)
    service.activate(s.snapshotId, mode, fixtures.CUTOFF + 1)  # type: ignore[arg-type]
    return service


def test_append_only_rollback_and_restart(tmp_path: Path) -> None:
    service = configured(tmp_path)
    a = service.active_at(paper.EPOCH)[0]
    assert a is not None
    b = service.create(
        [fixtures.evidence()], PolicySettings(), fixtures.CUTOFF + 2, fixtures.CUTOFF + 2
    )
    service.activate(b.snapshotId, "PAPER_GATED", fixtures.CUTOFF + 3)
    service.rollback(fixtures.CUTOFF + 4)
    assert service.active_at(paper.EPOCH)[0] == a
    before = service.repository.records("event", PolicyEvent)
    restarted = PolicyService(PolicyRepository(tmp_path / "policy.sqlite3"))
    assert restarted.active_at(paper.EPOCH)[0] == a
    assert restarted.events == before
    with pytest.raises(sqlite3.IntegrityError):
        service.repository.db.execute("DELETE FROM journal")
    with pytest.raises(ValueError, match="backdate"):
        service.deactivate(fixtures.CUTOFF - 1)


def test_rebuild_does_not_activate_and_requires_validation_for_paper(tmp_path: Path) -> None:
    service = PolicyService()
    s = service.rebuild(tmp_path, fixtures.CUTOFF, fixtures.CUTOFF, PolicySettings())
    assert s.status == "NO_EVIDENCE"
    assert service.active_at(paper.EPOCH)[0] is None
    service.activate(s.snapshotId, "SHADOW", fixtures.CUTOFF)
    with pytest.raises(ValueError, match="validated"):
        service.activate(s.snapshotId, "PAPER_GATED", fixtures.CUTOFF)


def test_snapshot_tamper_is_refused(tmp_path: Path) -> None:
    service = configured(tmp_path)
    s = fixtures.snapshot().model_copy(update={"snapshotId": identity("tampered", 1), "rules": ()})
    service.repository.append("snapshot", s.snapshotId, fixtures.CUTOFF, s)
    service.event("POLICY_CREATED", s.snapshotId, fixtures.CUTOFF + 2, "SHADOW", ("test",))
    with pytest.raises(ValueError, match="mismatch"):
        service.activate(s.snapshotId, "SHADOW", fixtures.CUTOFF + 3)


def test_shadow_skip_keeps_paper_and_gated_skip_suppresses(tmp_path: Path) -> None:
    for mode in ("SHADOW", "PAPER_GATED"):
        service = configured(tmp_path / mode, mode=mode, negative=True)
        market = MarketEngine(ParquetStorage(tmp_path / mode / "market"), policy=service)
        b = paper.board(regime="NOISY")
        before = b.model_dump_json()
        market.offer_paper_board(b, paper.EPOCH + 1200)
        assert service.recent[-1].policyAction == "SKIP"
        assert market.paper.unresolved() == (1 if mode == "SHADOW" else 0)
        assert b.model_dump_json() == before


def test_allow_does_not_override_session_guard(tmp_path: Path) -> None:
    service = configured(tmp_path)
    assert not service.observe(paper.board(), paper.EPOCH + 1200, guard_permits=False)
    assert service.recent[-1].policyAction == "ALLOW"


def watchdog_trades(n: int, *, wins: int) -> list[Any]:
    return [
        outcomes.trade(
            f"watchdog-{i}",
            expiry=paper.EPOCH + i * 10_000,
            outcome="WIN" if i % 10 < wins else "LOSS",
        )
        for i in range(n)
    ]


@pytest.mark.parametrize(
    "n,wins,state",
    [(12, 0, "WARMING"), (50, 8, "HEALTHY"), (50, 0, "WARNING"), (200, 0, "DRIFTED")],
)
def test_watchdog_windows(n: int, wins: int, state: str, tmp_path: Path) -> None:
    service = configured(tmp_path)
    s, _, activation, _ = service.active_at(paper.EPOCH)
    assert s is not None and activation is not None
    result = evaluate_watchdog(
        s, activation, watchdog_trades(n, wins=wins), paper.EPOCH + 3_000_000
    )
    assert result.state == state
    assert result == evaluate_watchdog(
        s, activation, watchdog_trades(n, wins=wins), paper.EPOCH + 3_000_000
    )


def test_watchdog_ignores_future_and_preactivation_entries(tmp_path: Path) -> None:
    service = configured(tmp_path)
    s, _, activation, _ = service.active_at(paper.EPOCH)
    assert s is not None and activation is not None
    rows = watchdog_trades(200, wins=0)
    result = evaluate_watchdog(s, activation, rows, fixtures.CUTOFF + 1)
    assert result.sampleCount == 0 and result.state == "WARMING"


def test_withdrawal_is_sticky_and_rules_do_not_change(tmp_path: Path) -> None:
    service = configured(tmp_path)
    snapshot = service.active_at(paper.EPOCH)[0]
    assert snapshot is not None
    rows = watchdog_trades(200, wins=0)
    for t in rows:
        b = paper.board(as_of=t.boardAsOf, confidence=0.75)
        # Use the real decision/trade identity contract for the watchdog join.
        t = t.model_copy(update={"contextId": b.candidates[0].contextId})
        service.observe(b, t.decisionAvailableAt)
        service.resolved([t], t.resolvedAtMarketTime or 0)
    at = paper.EPOCH + 3_000_000
    assert service.state(at)["watchdogState"] == "DRIFTED"
    assert not service.observe(paper.board(as_of=at), at + 1200)
    assert service.active_at(at)[0] == snapshot
    assert len(service.repository.records("snapshot", PolicySnapshot)) == 1
    with pytest.raises(ValueError, match="Drifted"):
        service.activate(snapshot.snapshotId, "PAPER_GATED", at)


def test_no_aggressive_settings_or_execution_imports() -> None:
    with pytest.raises(ValidationError):
        PolicySettings.model_validate({"mode": "LIVE_REAL"})
    with pytest.raises(ValidationError):
        PolicySettings(minSample=12)
    root = Path(__file__).parents[1] / "src" / "quant_engine" / "policy"
    forbidden = {
        "executionmanager",
        "orderexecutor",
        "presspoint",
        "sendinputevent",
        "webcontentsview",
        "arm",
        "disarm",
        "brokerstake",
        "minrankscore",
        "dailyprofittarget",
        "dailylosslimit",
    }
    for path in root.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Name):
                assert node.id.lower() not in forbidden
            elif isinstance(node, ast.Attribute):
                assert node.attr.lower() not in forbidden


def test_oos_candidates_only_from_train_and_no_overlapping_folds() -> None:
    from quant_engine.analytics import build

    rows = build(
        [
            outcomes.trade(
                str(i), expiry=paper.EPOCH + i * 10_000, regime="NOISY" if i >= 100 else "TREND_UP"
            )
            for i in range(400)
        ]
    ).rows
    empty = FoldMetrics(period="TRAIN", total=0, selected=0, resolved=0)
    folds = [
        WalkForwardFold(
            foldId=i + 1,
            trainStart=paper.EPOCH - 10_000,
            trainEnd=paper.EPOCH + 995_000,
            validationStart=paper.EPOCH + 996_000,
            validationEnd=paper.EPOCH + 997_000,
            testStart=paper.EPOCH + (100 + i * 100) * 10_000,
            testEnd=paper.EPOCH + (200 + i * 100) * 10_000 - 1,
            train=empty,
            validation=empty.model_copy(update={"period": "VALIDATION"}),
            test=empty.model_copy(update={"period": "TEST"}),
            purgeMs=0,
            embargoMs=0,
        )
        for i in range(3)
    ]
    evidence = from_rows(
        rows,
        source_id=identity("source", 1),
        available_at=paper.EPOCH + 5_000_000,
        folds=folds,
        versions=SOURCE_VERSIONS,
        source_mode="SYNTHETIC",
        source_hash="fixture",
    )
    assert not any(e.scope.scopeValue == "NOISY" for e in evidence)
    assert not build_snapshot(evidence, PolicySettings(), paper.EPOCH + 5_000_000).rules
    e = fixtures.evidence()
    overlap = fixtures.evidence(tests=(e.tests[0], e.tests[0], e.tests[2]))
    assert assess(overlap, PolicySettings(), fixtures.CUTOFF).status == "INVALID"


def test_rollback_from_off_restores_prior_mode(tmp_path: Path) -> None:
    service = configured(tmp_path)
    old = service.active_at(paper.EPOCH)[0]
    service.deactivate(fixtures.CUTOFF + 2)
    service.rollback(fixtures.CUTOFF + 3)
    assert service.active_at(paper.EPOCH)[:2] == (old, "PAPER_GATED")


def test_journal_failure_preserves_shadow_but_closes_paper(tmp_path: Path) -> None:
    for mode in ("SHADOW", "PAPER_GATED"):
        service = configured(tmp_path / mode, mode=mode)
        service.repository.close()
        assert service.observe(paper.board(), paper.EPOCH + 1200) == (mode == "SHADOW")
        assert service.error


def test_under_supported_specialization_falls_back_to_platform() -> None:
    specific = fixtures.evidence(
        negative=True,
        train=fixtures.evidence(negative=True).train.model_copy(update={"wins": 1, "losses": 11}),
    )
    snapshot = build_snapshot([fixtures.evidence(), specific], PolicySettings(), fixtures.CUTOFF)
    result = evaluate(paper.board(regime="NOISY"), snapshot)
    assert result.policyAction == "ALLOW" and len(snapshot.rules) == 1


def test_actual_session_guard_blocks_gated_intent(tmp_path: Path) -> None:
    service = configured(tmp_path)
    market = MarketEngine(ParquetStorage(tmp_path / "market"), policy=service)
    market.guard.stop_session(paper.EPOCH)
    assert market.guard.current is not None and not market.guard.current.canOpenNewEntry
    before = market.guard.current.model_dump_json()
    market.offer_paper_board(paper.board(), paper.EPOCH + 1200)
    assert service.recent[-1].policyAction == "ALLOW"
    assert market.paper.unresolved() == 0
    assert market.guard.current.model_dump_json() == before


def test_context_reset_never_reuses_old_decision(tmp_path: Path) -> None:
    from uuid import UUID

    service = configured(tmp_path, negative=True)
    first = paper.board()
    assert service.observe(first, paper.EPOCH + 1200)
    second = paper.board(regime="NOISY", context=UUID("00000000-0000-5000-8000-000000000099"))
    assert not service.observe(second, paper.EPOCH + 1300)
    assert service.recent[-1].contextId == second.candidates[0].contextId
