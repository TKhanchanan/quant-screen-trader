"""SYNTHETIC_REHEARSAL tests for the Phase 14 rehearsal and the recorder fixes it proved.

None of this is live acceptance evidence. The full 25-hour rehearsal runs outside CI with
``npm run rehearsal:phase14``; these tests keep its harness, its checks and the recorder
corrections honest on every commit.
"""

from __future__ import annotations

import dataclasses
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import phase14_rehearsal as rehearsal
import pytest
import replay_fixtures
from quant_engine.market_api import MarketEngine, ObservationBatch
from quant_engine.market_models import MarketObservation
from quant_engine.market_storage import ParquetStorage
from quant_engine.paper.policy import PaperSettings
from quant_engine.policy.service import PolicyService
from quant_engine.shadow_live import (
    MAX_EVENTS_BYTES,
    SHADOW_LIVE_VERSION,
    ShadowLiveRecorder,
)
from quant_engine.shadow_live_api import DesktopTelemetry

HOUR = rehearsal.HOUR
SECOND = rehearsal.SECOND


def recorder(root: Path, clock: rehearsal.VirtualClock | None = None) -> ShadowLiveRecorder:
    clock = clock or rehearsal.VirtualClock(rehearsal.VIRTUAL_EPOCH_MS)
    return ShadowLiveRecorder(
        root, commit_sha="a" * 40, application_version="0.1.0", now=clock.now, clock=clock
    )


def complete_state(root: Path) -> dict[str, Any]:
    r = recorder(root)
    for platform in r.data["platforms"].values():
        platform.update(captureDurationMs=24 * HOUR, liveObservations=10)
    r.data.update(
        qualifiedCaptureDurationMs=25 * HOUR,
        restartVerified=True,
        storageVerified=True,
        executionVerified=True,
        storageCorruption=False,
        engineCrashLoop=False,
        executionArmed=False,
        unboundedQueue=False,
        unexpectedBrokerPresses=0,
        policy={"mode": "SHADOW"},
        paperEnabled=True,
        finishedAt=rehearsal.VIRTUAL_EPOCH_MS,
    )
    state = r.report(rehearsal.VIRTUAL_EPOCH_MS)
    r.lease.close()
    assert state["acceptance"] == "COMPLETE"
    return state


def test_acceptance_gate_boundaries_use_the_real_evaluator(tmp_path: Path) -> None:
    gate = rehearsal.gate_boundaries(complete_state(tmp_path / "state"), tmp_path / "gate")
    assert gate["cases"] == dict(
        total_23_59_59="PENDING",
        total_24_00_00="COMPLETE",
        capitalbear_22_59_59="PENDING",
        iqoption_22_59_59="PENDING",
        both_23_00_00="COMPLETE",
    )
    assert gate["correct"] is True


def heartbeat(name: str, instance: str, at: int) -> dict[str, Any]:
    value = dict(
        platform=name,
        instanceId=instance,
        healthRevision=1,
        captureRunning=True,
        surfaceAvailable=True,
        engineAvailable=True,
        intervalMs=1000,
        queueDepth=3,
        droppedBatches=0,
        http429s=0,
        armed=False,
        brokerPresses=0,
        mainLoopDelayMs=1.0,
        slots=[
            dict(
                slotId=1,
                enabled=True,
                assetName="EUR/USD",
                contextId="00000000-0000-4000-8000-000000000001",
                state="READY",
                observations=1,
                dataUncertain=0,
                dropped=0,
                captureEligible=True,
                lastCaptureAttemptAt=at,
            )
        ],
    )
    return DesktopTelemetry.model_validate(value).model_dump(mode="json")


def test_virtual_clock_accrues_24h_and_23h_from_heartbeats_only(tmp_path: Path) -> None:
    clock = rehearsal.VirtualClock(rehearsal.VIRTUAL_EPOCH_MS)
    r = recorder(tmp_path, clock)
    instance = str(uuid4())
    start = clock.now
    last_below: str | None = None
    # Five-second heartbeats are the longest gap the recorder still credits.
    for step in range(0, 24 * 720 + 2):
        at = start + step * 5 * SECOND
        clock.advance_to(at)
        for name in ("capitalbear", "iqoption"):
            r.telemetry(heartbeat(name, instance, at), at)
        if step % 12 == 0:
            r.flush(now=at)  # the engine checkpoints every second; once a minute is enough here
        qualified = r.data["qualifiedCaptureDurationMs"]
        if qualified < 24 * HOUR:
            last_below = rehearsal.duration(qualified)
    assert last_below == "23:59:55"
    report = r.report(clock.now)
    assert report["qualifiedCaptureDurationMs"] >= 24 * HOUR
    assert all(p["captureDurationMs"] >= 23 * HOUR for p in report["platforms"].values())
    # Duration alone never completes acceptance: nothing was observed, verified or checked.
    assert report["acceptance"] == "PENDING"
    assert rehearsal.unmet_requirements(report)


def test_every_negative_fixture_is_rejected(tmp_path: Path) -> None:
    results = rehearsal.negative_fixtures(tmp_path)
    assert set(results) == {
        "futureSample",
        "invalidEntryTiming",
        "invalidExpiryTiming",
        "staleContext",
        "duplicateBoard",
        "oversizedTransportBatch",
        "queueBeyondCapacity",
        "autoArmedDuringRun",
        "staleExecutionTicket",
    }
    assert {name: value["rejected"] for name, value in results.items()} == dict.fromkeys(
        results, True
    )


def test_paper_armed_execution_is_evidence_not_failure(tmp_path: Path) -> None:
    control = rehearsal.paper_execution_control(tmp_path)
    assert control["allowed"] is True, control
    assert control["executionArmed"] is False and control["paperExecutionArmed"] is True


def switched_contexts(seconds: int = 420, tail: int = 90) -> list[MarketObservation]:
    """Three CapitalBear slots whose contexts all change without a slot reset, as Start
    observation or a surface change does. The switch lands just after an S5 boundary, so the
    old context's last bar is closed by the new context's first reading."""
    rows = sorted(replay_fixtures.session(seconds=seconds), key=lambda row: row.observedAt)
    switch = replay_fixtures.BASE_MS + seconds * 1000 + 100
    for row in list(rows[-3:]):
        for step in range(tail):
            at = switch + step * 1000
            rows.append(
                row.model_copy(
                    update=dict(
                        id=uuid4(),
                        contextId=replay_fixtures.identity(f"switched/{row.slotId}"),
                        observedAt=datetime.fromtimestamp(at / 1000, UTC),
                        parsedAt=datetime.fromtimestamp((at + 50) / 1000, UTC),
                    )
                )
            )
    return sorted(rows, key=lambda row: row.observedAt)


def test_context_change_without_reset_is_not_contamination(tmp_path: Path) -> None:
    engine = MarketEngine(
        ParquetStorage(tmp_path / "market", batch_size=100000),
        paper=PaperSettings(),
        policy=PolicyService(),
    )
    clock = rehearsal.VirtualClock(replay_fixtures.BASE_MS)
    engine.shadow = recorder(tmp_path / "phase14", clock)
    for row in switched_contexts():
        at = int(row.parsedAt.timestamp() * 1000)
        clock.advance_to(max(clock.now, at))
        engine.ingest(ObservationBatch(observations=[row]), at)
    report = engine.shadow.report(clock.now)
    assert report["platforms"]["capitalbear"]["pipeline"]["Phase8"] > 0
    assert sum(slot["contextTransitions"] for slot in report["slots"].values()) == 3
    assert report["crossContextContamination"] == 0, report["errors"]


def test_mixed_context_bundle_and_foreign_context_are_detected(tmp_path: Path) -> None:
    engine = MarketEngine(
        ParquetStorage(tmp_path / "market", batch_size=100000),
        paper=PaperSettings(),
        policy=PolicyService(),
    )
    clock = rehearsal.VirtualClock(replay_fixtures.BASE_MS)
    shadow = recorder(tmp_path / "phase14", clock)
    engine.shadow = shadow
    for row in sorted(replay_fixtures.session(seconds=120), key=lambda row: row.observedAt):
        at = int(row.parsedAt.timestamp() * 1000)
        clock.advance_to(max(clock.now, at))
        engine.ingest(ObservationBatch(observations=[row]), at)
    bundle = engine.features.latest_bundle("capitalbear", 1)
    assert bundle is not None and shadow.data["crossContextContamination"] == 0
    context = next(iter(bundle.contexts))
    snapshot = bundle.contexts[context]
    assert snapshot is not None
    mixed = bundle.model_copy(
        update={
            "contexts": {
                **bundle.contexts,
                context: snapshot.model_copy(update={"contextId": uuid4()}),
            }
        }
    )
    shadow.ensemble(mixed, clock.now)
    assert shadow.data["errors"].get("MIXED_CONTEXT_BUNDLE") == 1
    foreign = bundle.model_copy(update={"contextId": uuid4(), "asOf": bundle.asOf + 5000})
    shadow.ensemble(foreign, clock.now)
    assert shadow.data["errors"].get("DERIVED_CONTEXT_MISMATCH", 0) >= 1
    assert shadow.report(clock.now)["acceptance"] == "FAIL"


def test_board_and_policy_detail_is_tiered_and_counts_stay_complete(tmp_path: Path) -> None:
    engine = MarketEngine(
        ParquetStorage(tmp_path / "market", batch_size=100000),
        paper=PaperSettings(),
        policy=PolicyService(),
    )
    clock = rehearsal.VirtualClock(replay_fixtures.BASE_MS)
    shadow = recorder(tmp_path / "phase14", clock)
    engine.shadow = shadow
    for row in sorted(replay_fixtures.session(seconds=900), key=lambda row: row.observedAt):
        at = int(row.parsedAt.timestamp() * 1000)
        clock.advance_to(max(clock.now, at))
        engine.ingest(ObservationBatch(observations=[row]), at)
        if at // 1000 % 10 == 0:
            shadow.flush(now=at)
    shadow.flush(now=clock.now, finish=True)
    events = [json.loads(line) for line in shadow.events.read_text().splitlines()]
    boards = [e for e in events if e["kind"] == "BOARD"]
    policies = [e for e in events if e["kind"] == "POLICY"]
    summaries = [e for e in events if e["kind"] == "BOARD_SUMMARY"]
    platform = shadow.data["platforms"]["capitalbear"]
    revisions = sum(platform["boards"].values())
    assert revisions > 0 and summaries
    assert all(
        e["board"]["selectedSlotId"] is not None or e["board"]["status"] == "INVALID"
        for e in boards
    )
    assert all(
        e["decision"]["baseGatePassed"] or e["decision"]["policyAction"] != "SKIP" for e in policies
    )
    summarized = sum(sum(s["boardRevisions"].values()) for s in summaries)
    assert summarized == platform["summarizedBoardRevisions"] == revisions - len(boards)
    decisions = sum(platform["policyActions"].values())
    assert sum(sum(s["policyActions"].values()) for s in summaries) + len(policies) == decisions
    # Non-selecting revisions cost a few bytes each once summarized: a day of CapitalBear's
    # 6,480 revisions an hour stays far inside the detailed-event bound.
    summary_bytes = sum(len(json.dumps(s, separators=(",", ":"))) + 1 for s in summaries)
    assert summary_bytes / summarized * 6480 * 26 < MAX_EVENTS_BYTES / 10
    assert SHADOW_LIVE_VERSION == "qst-shadow-live-v2"


def write_observations(root: Path, assets: list[str]) -> list[Path]:
    """One production Parquet file per asset, written by the engine's own storage."""
    storage = ParquetStorage(root / "market-data")
    template = replay_fixtures.session(seconds=1)[0]
    for asset in assets:
        storage.append(
            "observations", template.model_copy(update={"id": uuid4(), "assetName": asset})
        )
    storage.flush()
    return sorted((root / "market-data" / "observations").rglob("*.parquet"))


def test_storage_audit_resumes_past_the_per_call_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient
    from quant_engine.app import create_app
    from quant_engine.shadow_live_verify import verify_storage

    monkeypatch.setenv("QST_SHADOW_LIVE", "1")
    monkeypatch.setenv("QST_COMMIT_SHA", "a" * 40)
    monkeypatch.setattr("quant_engine.shadow_live_verify.FILES_PER_CALL", 3)
    with TestClient(create_app(data_dir=tmp_path)) as client:
        state = client.get("/api/shadow-live/state").json()
        shadow = client.app.state.market.shadow  # type: ignore[attr-defined]
        folder = tmp_path / "market-data" / "observations" / "platform=capitalbear"
        (old,) = write_observations(tmp_path, ["Before run"])
        before = (state["startedAt"] - 86_400_000) / 1000
        os.utime(old, (before, before))
        assert len(write_observations(tmp_path, [f"Asset {index}" for index in range(7)])) == 8
        seen = []
        for _ in range(4):
            audit = verify_storage(shadow, tmp_path)
            seen.append((audit["parquetFiles"], audit["remainingFiles"], audit["complete"]))
            assert shadow.data["storageVerified"] is audit["complete"]
            if audit["complete"]:
                break
        assert seen == [(3, 4, False), (6, 1, False), (7, 0, True)]
        assert shadow.data["storageVerified"] is True
        # A new, unreadable file after completion is picked up on the next call and fails it.
        (folder / "asset=broken").mkdir()
        (folder / "asset=broken" / "broken.parquet").write_text("not parquet")
        audit = verify_storage(shadow, tmp_path)
        assert audit["complete"] is False and audit["corruption"] is None
        assert shadow.data["storageVerified"] is False


def test_compressed_rehearsal_passes_every_check(tmp_path: Path) -> None:
    scenario = dataclasses.replace(
        rehearsal.smoke(16),
        capture_start={"capitalbear": 30 * SECOND, "iqoption": 40 * SECOND},
        identity_failure=rehearsal.SlotWindow("capitalbear", 7, 2 * 60_000, 3 * 60_000),
        unreadable_slot=rehearsal.SlotWindow("iqoption", 4, 3 * 60_000, 4 * 60_000),
        asset_change=rehearsal.AssetChange("capitalbear", 2, 4 * 60_000, "NZD/USD OTC"),
        context_change=("iqoption", 5 * 60_000),
        auto_sync_on=60_000,
        auto_sync_change=rehearsal.AssetChange("iqoption", 6, 6 * 60_000, "USD/CHF (OTC)"),
        auto_sync_review=7 * 60_000,
        restart=8 * 60_000,
        restart_downtime=60_000,
        resume_capture_delay=30 * SECOND,
        verify_restart_delay=60_000,
        engine_stall=(11 * 60_000, 8 * SECOND),
        network_hiccup=rehearsal.Window(12 * 60_000, 12 * 60_000 + 30 * SECOND),
        storage_batch=100_000,
    )
    run = rehearsal.Rehearsal(scenario, tmp_path / "data", commit_sha="c" * 40)
    run.run()
    facts = rehearsal.evaluate(run, tmp_path / "work", expect_complete=False)
    failed = [(c.name, c.detail) for c in run.outcome.checks if not c.passed]
    assert failed == []
    final = facts["final"]
    assert final["engineRestarts"] == 1 and final["restartVerified"] is True
    assert facts["contextAudit"]["clean"] is True
    assert final["acceptance"] == "PENDING"  # minutes of capture never satisfy the 24h gate


def test_rehearsal_output_never_goes_to_live_evidence(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        rehearsal.main(
            ["--out", str(tmp_path / "docs" / "evidence" / "phase14"), "--scenario", "smoke"]
        )
