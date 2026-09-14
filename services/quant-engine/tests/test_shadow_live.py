"""SYNTHETIC_BEHAVIOR_TEST only. None of these fixtures are live acceptance evidence."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import paper_fixtures as fixtures
import pytest
from fastapi.testclient import TestClient
from quant_engine.app import create_app
from quant_engine.market_api import MarketEngine, ObservationBatch
from quant_engine.market_models import MarketObservation
from quant_engine.market_storage import ParquetStorage
from quant_engine.paper.engine import PaperEngine
from quant_engine.policy.service import PolicyService
from quant_engine.shadow_live import MAX_RECENT, ShadowLiveRecorder


def recorder(root: Path) -> ShadowLiveRecorder:
    return ShadowLiveRecorder(
        root, commit_sha="a" * 40, application_version="0.1.0", now=fixtures.EPOCH
    )


def observation(at: int, *, context: str | None = None) -> MarketObservation:
    sample = fixtures.sample(at, 1.2, source="DOM")
    return MarketObservation(
        id=uuid4(),
        platform=sample.platform,
        slotId=sample.slotId,
        assetName=sample.assetName,
        contextId=UUID(context) if context else sample.contextId,
        observedAt=datetime.fromtimestamp(at / 1000, UTC),
        parsedAt=datetime.fromtimestamp(at / 1000, UTC),
        sourceType="DOM",
        price=sample.price,
        payout=None,
        timerSeconds=None,
        parserConfidence=1.0,
        dataQuality=sample.quality,
        captureLatencyMs=0.0,
        parseLatencyMs=0.0,
        calibrationProfileId=None,
        parserVersion="test-only",
    )


def test_stale_uncertain_duplicates_and_out_of_order_are_not_fabricated(tmp_path: Path) -> None:
    r = recorder(tmp_path)
    e = MarketEngine(ParquetStorage(tmp_path / "market"))
    e.shadow = r
    at = fixtures.EPOCH
    o = observation(at)
    uncertain = observation(at + 1).model_copy(update={"price": None})
    assert (
        e.ingest(ObservationBatch(observations=[o, o, observation(at - 1), uncertain]), at + 2) == 1
    )
    assert e.ingest(ObservationBatch(observations=[observation(at - 4000)]), at) == 0
    report = r.report(at)
    p = report["platforms"]["capitalbear"]
    assert (p["observations"], p["accepted"], p["rejected"], p["dataUncertain"]) == (5, 1, 4, 1)
    s = report["slots"]["capitalbear:1"]
    assert s["staleSamples"] == 1 and s["duplicates"] == 1 and s["outOfOrder"] == 2
    assert report["causalityViolations"] == 0
    assert len(e.builders[("capitalbear", 1)].samples) == 1


def test_future_ocr_and_cross_context_are_detected(tmp_path: Path) -> None:
    r = recorder(tmp_path)
    b = fixtures.board()
    at = fixtures.decision_time(b)
    o = observation(at)
    r.observation(o, True, at)
    c = b.candidates[0]
    r.lineage[f"{b.platform}:{c.slotId}:{c.contextId}:{c.asOf}"] = at + 1
    policy = PolicyService()
    permits = policy.observe(b, at)
    r.board(b, at, policy.recent[-1], permits)
    assert r.report(at)["causalityViolations"] == 1
    r.observation(observation(at + 2, context=str(uuid4())), True, at + 2)
    r.board(b, at + 2, policy.recent[-1], permits)
    assert r.report(at)["crossContextContamination"] > 0
    assert r.report(at)["result"] == "FAIL"


def test_paper_entry_expiry_and_canonical_price_detection(tmp_path: Path) -> None:
    r = recorder(tmp_path)
    engine = PaperEngine()
    b = fixtures.board()
    at = fixtures.decision_time(b)
    r.observation(observation(at), True, at)
    pending = engine.on_board(b, at)
    r.paper(pending)
    sample = fixtures.sample(at, 1.2)
    opened = engine.on_market_sample(sample)
    r.paper(opened, sample)
    resolved = engine.on_market_sample(fixtures.sample(at + 5000, 1.3))
    r.paper(resolved, fixtures.sample(at + 5000, 1.3))
    assert r.report(at)["causalityViolations"] == 0
    assert r.report(at)["platforms"]["capitalbear"]["outcomes"]["WIN"] == 1
    # Independent corrupt transition identities defeat transition deduplication deliberately.
    trade = opened.trades[0].model_copy(
        update={"paperTradeId": uuid4(), "entryTime": at - 1, "entryPrice": 9.0}
    )
    r.paper(type(opened)(trades=(trade,)), sample)
    trade = resolved.trades[0].model_copy(update={"paperTradeId": uuid4(), "expiryTime": at + 4999})
    r.paper(type(resolved)(trades=(trade,)), fixtures.sample(at + 5000, 1.3))
    assert r.report(at)["causalityViolations"] == 4


def test_shadow_baseline_same_trade_identity_price_and_outcome(tmp_path: Path) -> None:
    results = []
    for mode in ("OFF", "SHADOW"):
        engine = MarketEngine(
            ParquetStorage(tmp_path / mode), policy=PolicyService(default_mode=mode)
        )
        if mode == "SHADOW":
            engine.shadow = recorder(tmp_path / "phase14")
            engine.shadow.observation(observation(fixtures.EPOCH), True, fixtures.EPOCH)
        b = fixtures.board()
        at = fixtures.decision_time(b)
        engine.offer_paper_board(b, at)
        engine.offer_paper_price(fixtures.sample(at, 1.2))
        engine.offer_paper_board(b, at + 1)  # Duplicate finalized selection remains duplicate.
        engine.offer_paper_price(fixtures.sample(at + 5000, 1.3))
        results.append(
            [
                row.model_dump(mode="json")
                for category, row in engine.storage.pending
                if category == "paper_trades"
            ]
        )
        if mode == "SHADOW":
            assert engine.policy.recent[-1].policyAction == "WATCH"
            assert engine.shadow is not None
            assert engine.shadow.data["baselineMismatches"] == 0
    assert results[0] == results[1] and len(results[0]) == 3


def test_shadow_skip_still_offers_baseline(tmp_path: Path) -> None:
    r = recorder(tmp_path)
    b = fixtures.board().model_copy(update={"status": "NO_OPPORTUNITY"})
    p = PolicyService()
    assert p.observe(b, fixtures.decision_time(b))
    assert p.recent[-1].policyAction == "SKIP"
    r.board(b, fixtures.decision_time(b), p.recent[-1], False)
    assert r.data["baselineMismatches"] == 1


def test_no_opportunity_and_zero_selections_are_not_health_failures(tmp_path: Path) -> None:
    r = recorder(tmp_path)
    b = fixtures.board().model_copy(update={"status": "NO_OPPORTUNITY", "candidates": []})
    p = PolicyService()
    permits = p.observe(b, fixtures.decision_time(b))
    r.board(b, fixtures.decision_time(b), p.recent[-1], permits)
    report = r.report(fixtures.EPOCH)
    assert report["health"] == "HEALTHY"
    assert report["acceptance"] == "PENDING"  # No fabricated 60-minute session.
    assert report["platforms"]["capitalbear"]["paperStates"]["PENDING_ENTRY"] == 0


def test_latency_aggregation_memory_and_disk_caps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = recorder(tmp_path)
    for value in range(1, 101):
        r.latency("capitalbear", "boardToPolicy", value)
    metric = r.report(fixtures.EPOCH)["latency"]["capitalbear:boardToPolicy"]
    assert (metric["count"], metric["p50"], metric["p95"], metric["max"]) == (100, 50, 95, 100)
    for value in range(10000):
        r.latency("capitalbear", "boardToPolicy", value)
        r.once(str(value))
        r.event("TEST", at=value)
    assert len(r.latencies["capitalbear:boardToPolicy"]) == MAX_RECENT
    assert len(r.seen) == 4096 and len(r.pending) == 512
    assert r.data["evidenceTruncated"]
    monkeypatch.setattr("quant_engine.shadow_live.MAX_EVENTS_BYTES", 1)
    r.flush(now=fixtures.EPOCH)
    assert "EVENT_CAP_REACHED" in r.data["warnings"]
    assert len(r.data["recentEvents"]) == 64


def test_restart_continuation_excludes_downtime_and_rejects_different_build(tmp_path: Path) -> None:
    r = recorder(tmp_path)
    r.flush(now=fixtures.EPOCH + 1000, finish=True)
    resumed = ShadowLiveRecorder(
        tmp_path,
        commit_sha="a" * 40,
        application_version="0.1.0",
        run_id=r.data["runId"],
        now=fixtures.EPOCH + 9000,
    )
    assert resumed.data["runId"] == r.data["runId"]
    assert resumed.data["engineRestarts"] == 1 and resumed.data["uncleanRestarts"] == 0
    assert resumed.report(fixtures.EPOCH + 10000)["durationMs"] == 2000
    assert not resumed.data["restartVerified"]
    resumed.flush(now=fixtures.EPOCH + 10000, finish=True)
    with pytest.raises(ValueError, match="same build"):
        ShadowLiveRecorder(
            tmp_path, commit_sha="b" * 40, application_version="0.1.0", run_id=r.data["runId"]
        )
    fresh = recorder(tmp_path)
    assert fresh.data["runId"] != r.data["runId"]


def test_recorder_failure_never_changes_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = recorder(tmp_path)

    def broken(*args: object, **kwargs: object) -> None:
        raise OSError("private profile path must not escape")

    monkeypatch.setattr(r, "event", broken)
    r.observation(observation(fixtures.EPOCH), True, fixtures.EPOCH)
    assert r.data["recorderErrors"] == 1
    assert "private profile" not in str(r.report(fixtures.EPOCH))


def test_local_only_closed_telemetry_schema_and_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("QST_SHADOW_LIVE", raising=False)
    with TestClient(create_app(data_dir=tmp_path / "off")) as client:
        assert client.get("/api/shadow-live/state").status_code == 409
    monkeypatch.setenv("QST_SHADOW_LIVE", "1")
    monkeypatch.setenv("QST_COMMIT_SHA", "a" * 40)
    with TestClient(create_app(data_dir=tmp_path / "on")) as client:
        assert (
            client.get(
                "/api/shadow-live/state", headers={"origin": "https://broker.invalid"}
            ).status_code
            == 403
        )
        assert (
            client.post("/api/shadow-live/telemetry", json={"cookies": "forbidden"}).status_code
            == 422
        )
        summary = client.post("/api/shadow-live/checkpoint").json()
        assert summary["acceptance"] == "PENDING"
        assert summary["unexpectedBrokerPresses"] is None
        audit = client.post("/api/shadow-live/verify-storage").json()
        assert audit["storageVerified"] is True
        assert len(audit["storageAudit"]["sqlite"]) == 2


def test_first_eligible_price_cannot_be_skipped(tmp_path: Path) -> None:
    from quant_engine.paper.engine import PaperUpdate

    r = recorder(tmp_path)
    p = PaperEngine()
    b = fixtures.board()
    at = fixtures.decision_time(b)
    pending = p.on_board(b, at).trades[0]
    r.first_price(pending, fixtures.sample(at - 1, 1.2), PaperUpdate(), p.settings)
    assert r.data["causalityViolations"] == 0
    r.first_price(pending, fixtures.sample(at, 1.2), PaperUpdate(), p.settings)
    assert r.data["causalityViolations"] == 1


def test_real_pipeline_instrumentation_is_observational(tmp_path: Path) -> None:
    import replay_fixtures

    signatures = []
    for enabled in (False, True):
        engine = MarketEngine(
            ParquetStorage(tmp_path / str(enabled), batch_size=100000), policy=PolicyService()
        )
        if enabled:
            engine.shadow = recorder(tmp_path / "phase14")
        rows = sorted(replay_fixtures.session(seconds=80), key=lambda row: row.observedAt)
        for row in rows:
            at = int(row.parsedAt.timestamp() * 1000)
            engine.ingest(ObservationBatch(observations=[row]), at)
        signatures.append([row.model_dump_json() for _, row in engine.storage.pending])
        if engine.shadow:
            counts = engine.shadow.data["platforms"]["capitalbear"]["pipeline"]
            assert all(counts[phase] > 0 for phase in ("Phase5", "Phase6", "Phase7", "Phase8"))
            assert engine.shadow.data["recorderErrors"] == 0
            assert engine.shadow.data["crossContextContamination"] == 0
    assert signatures[0] == signatures[1]


def test_capture_duration_requires_recent_data_and_excludes_stalls(tmp_path: Path) -> None:
    r = recorder(tmp_path)
    at = fixtures.EPOCH
    o = observation(at)
    value: dict[str, Any] = dict(
        platform="capitalbear",
        instanceId=str(uuid4()),
        healthRevision=1,
        captureRunning=True,
        surfaceAvailable=True,
        engineAvailable=True,
        intervalMs=500,
        armed=False,
        brokerPresses=0,
        queueDepth=1,
        mainLoopDelayMs=0,
        slots=[dict(slotId=1, enabled=True, dataUncertain=0)],
    )
    r.telemetry(value, at)
    r.telemetry(value, at + 1000)
    assert r.data["platforms"]["capitalbear"]["captureDurationMs"] == 0
    r.observation(o, True, at)
    value["slots"][0]["lastCaptureAttemptAt"] = at
    r.telemetry(value, at + 1000)
    r.telemetry(value, at + 2000)
    assert r.data["platforms"]["capitalbear"]["captureDurationMs"] == 1000
    r.telemetry(value, at + 8000)
    assert r.data["platforms"]["capitalbear"]["captureDurationMs"] == 1000
    r.telemetry(value, at + 20000)
    assert r.data["platforms"]["capitalbear"]["continuousCaptureMs"] == 0
    assert r.report(at + 20000)["acceptance"] == "PENDING"


def test_concurrent_run_identity_is_refused_and_unclean_restart_is_not_hidden(
    tmp_path: Path,
) -> None:
    r = recorder(tmp_path)
    kwargs = dict(commit_sha="a" * 40, application_version="0.1.0", run_id=r.data["runId"])
    with pytest.raises(ValueError, match="live owner"):
        ShadowLiveRecorder(tmp_path, **kwargs)

    r.lease.close()  # Simulate process exit without a clean recorder checkpoint.
    resumed = ShadowLiveRecorder(tmp_path, **kwargs, now=fixtures.EPOCH + 1000)
    assert resumed.data["uncleanRestarts"] == 1
    assert resumed.report(fixtures.EPOCH + 1000)["acceptance"] == "PENDING"


def test_zero_selection_completed_acceptance_does_not_require_trades(tmp_path: Path) -> None:
    r = recorder(tmp_path)
    # Only a predicate fixture, never exported as a real session.
    for p in r.data["platforms"].values():
        p.update(longestContinuousCaptureMs=3600000, liveObservations=10)
    r.data.update(
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
    )
    assert r.report(fixtures.EPOCH)["acceptance"] == "COMPLETE"
    assert r.report(fixtures.EPOCH)["result"] == "PASS"
    r.data["storageCorruption"] = None
    assert r.report(fixtures.EPOCH)["acceptance"] == "PENDING"


def test_failed_ocr_attempts_count_as_capture_without_fabricating_samples(tmp_path: Path) -> None:
    r = recorder(tmp_path)
    value = dict(
        platform="iqoption",
        instanceId=str(uuid4()),
        healthRevision=1,
        captureRunning=True,
        surfaceAvailable=True,
        engineAvailable=False,
        intervalMs=1000,
        armed=False,
        brokerPresses=0,
        queueDepth=0,
        mainLoopDelayMs=0,
        slots=[dict(slotId=1, enabled=True, dataUncertain=1, lastCaptureAttemptAt=fixtures.EPOCH)],
    )
    r.telemetry(value, fixtures.EPOCH)
    r.telemetry(value, fixtures.EPOCH + 1000)
    assert r.data["platforms"]["iqoption"]["captureDurationMs"] == 1000
    assert r.data["platforms"]["iqoption"]["observations"] == 0
    assert r.report(fixtures.EPOCH + 1000)["health"] == "DEGRADED"
    assert r.report(fixtures.EPOCH + 1000)["acceptance"] == "PENDING"
