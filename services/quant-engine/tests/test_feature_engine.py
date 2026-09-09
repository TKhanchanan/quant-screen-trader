"""W11-W18 and the invariant suite: engine behaviour, isolation and no-lookahead."""

import math
from typing import Any

import pytest
from features_fixtures import BASE_MS, CONTEXT_A, CONTEXT_B, candle, flat_candles, second
from quant_engine.features import FEATURE_VERSION, FeatureEngine, FeatureSnapshot
from quant_engine.market_models import TIMEFRAMES, Candle


def walk(count: int, **changes: Any) -> list[Candle]:
    """A deterministic, non-monotonic price path with a real range on every bar."""
    closes = [
        100.0 * (1 + 0.004 * math.sin(index / 3) + 0.002 * math.cos(index / 7))
        for index in range(count)
    ]
    return [
        candle(index, close, close * 1.002, close * 0.998, close, **changes)
        for index, close in enumerate(closes)
    ]


def feed(engine: FeatureEngine, candles: list[Candle]) -> list[FeatureSnapshot]:
    return [snapshot for c in candles if (snapshot := engine.ingest_candle(c)) is not None]


def numbers(value: Any) -> list[float]:
    if isinstance(value, dict):
        return [number for item in value.values() for number in numbers(item)]
    if isinstance(value, list):
        return [number for item in value for number in numbers(item)]
    return [value] if isinstance(value, float | int) and not isinstance(value, bool) else []


def test_warming_reports_missing_indicators_as_none_never_zero() -> None:
    engine = FeatureEngine()
    snapshots = feed(engine, walk(10))
    latest = snapshots[-1]
    assert latest.status == "WARMING"
    assert latest.barCount == 10
    assert latest.featureVersion == FEATURE_VERSION
    assert latest.trend.ema50 is None
    assert latest.trend.priceToEma50Bps is None
    assert latest.momentum.rsi14 is None
    assert latest.volatility.atr14 is None
    assert latest.volatility.bbMiddle is None
    assert latest.structure.priorHigh20 is None
    assert latest.noise.efficiencyRatio20 is None
    assert latest.trend.ema5 is not None  # a five-bar EMA can already exist


def test_basis_point_features_are_basis_points_not_bare_ratios() -> None:
    # A 1% true range on a 100 close is 100 bps. Reporting the 0.01 ratio under a Bps name
    # silently understates volatility by four orders of magnitude.
    engine = FeatureEngine()
    feed(engine, [candle(index, 100.0, 100.5, 99.5, 100.0) for index in range(20)])
    latest = engine.latest_snapshot("capitalbear", 1, "M1")
    assert latest is not None
    assert latest.priceAction.trueRangeBps == pytest.approx(100.0)
    assert latest.volatility.atr14 == pytest.approx(1.0)
    assert latest.volatility.atr14Bps == pytest.approx(100.0)
    assert latest.priceAction.rangeBps == pytest.approx((100.5 / 99.5 - 1) * 10_000)


def test_a_clean_history_becomes_ready_with_every_indicator_present() -> None:
    engine = FeatureEngine()
    latest = feed(engine, walk(60))[-1]
    assert latest.status == "READY"
    assert latest.barCount == 60
    assert latest.trend.ema50 is not None
    assert latest.trend.ema50Slope3 is not None
    assert latest.trend.priceSlope20 is not None
    assert latest.momentum.rsi14 is not None
    assert latest.momentum.stochD3 is not None
    assert latest.momentum.macdHistogram is not None
    assert latest.volatility.atr14 is not None
    assert latest.volatility.realizedVol20Bps is not None
    assert latest.volatility.bbPercentB is not None
    assert latest.structure.priorHigh20 is not None
    assert latest.structure.confirmedPivots > 0
    assert latest.noise.choppiness14 is not None
    assert latest.quality.goodRatio10 == pytest.approx(1.0)


def test_degraded_inputs_still_compute_but_never_claim_a_clean_status() -> None:
    engine = FeatureEngine()
    latest = feed(engine, walk(60, quality="DEGRADED", coverage=0.5, gap_ms=30_000))[-1]
    assert latest.status == "DEGRADED"
    assert latest.trend.ema50 is not None, "degraded inputs are still measured"
    assert latest.quality.goodRatio10 == pytest.approx(0.0)
    assert latest.quality.meanCoverage10 == pytest.approx(0.5)
    assert latest.quality.gapBars10 == 10
    assert latest.quality.missingSecondsRecent == 300
    assert latest.quality.currentCandleQuality == "DEGRADED"


def test_a_recent_gap_downgrades_an_otherwise_ready_history() -> None:
    engine = FeatureEngine()
    clean = walk(60)
    feed(engine, clean[:55])
    assert feed(engine, clean[55:56])[-1].status == "READY"
    degraded = [
        record.model_copy(update={"quality": "DEGRADED", "coverage": 0.2, "gapDurationMs": 48_000})
        for record in clean[56:]
    ]
    assert feed(engine, degraded)[-1].status == "DEGRADED"


def test_an_impossible_candle_is_reported_invalid_and_touches_no_state() -> None:
    engine = FeatureEngine()
    feed(engine, walk(20))
    before = engine.latest_snapshot("capitalbear", 1, "M1")
    assert before is not None
    broken = candle(20, 100.0, 90.0, 95.0, 92.0).model_copy(update={"high": 90.0, "low": 95.0})
    result = engine.ingest_candle(broken)
    assert result is not None and result.status == "INVALID"
    assert result.trend.ema5 is None
    assert engine.latest_snapshot("capitalbear", 1, "M1") == before
    assert engine.rejected == 1


def test_forming_candles_and_replayed_history_never_reach_the_indicators() -> None:
    engine = FeatureEngine()
    feed(engine, walk(20))
    forming = candle(20, 100.0, 101.0, 99.0, 100.0).model_copy(update={"state": "FORMING"})
    assert engine.ingest_candle(forming) is None
    replayed = walk(20)[10]
    assert engine.ingest_candle(replayed) is None, "a closed bar never arrives twice"
    assert engine.latest_snapshot("capitalbear", 1, "M1") is not None
    assert engine.latest_snapshot("capitalbear", 1, "M1").barCount == 20  # type: ignore[union-attr]


def test_future_candles_never_rewrite_an_emitted_snapshot() -> None:
    engine = FeatureEngine()
    history = walk(80)
    emitted = feed(engine, history[:30])
    captured = emitted[-1]
    before = [snapshot.model_dump(mode="json") for snapshot in emitted]
    feed(engine, history[30:])
    assert [snapshot.model_dump(mode="json") for snapshot in emitted] == before
    assert captured.barCount == 30
    assert engine.latest_snapshot("capitalbear", 1, "M1") is not captured


def test_streaming_and_batch_replay_agree_exactly() -> None:
    history = walk(70)
    streamed = FeatureEngine()
    interleaved = walk(70, slot=2, asset="GBP/USD OTC")
    results = []
    for index, record in enumerate(history):
        results.append(streamed.ingest_candle(record))
        streamed.ingest_candle(interleaved[index])  # a busy neighbour must change nothing
    batch = FeatureEngine()
    expected = feed(batch, history)
    assert [snapshot.model_dump(mode="json") for snapshot in results if snapshot] == [
        snapshot.model_dump(mode="json") for snapshot in expected
    ]


def test_slot_platform_and_asset_state_never_leak_into_each_other() -> None:
    engine = FeatureEngine()
    feed(engine, walk(60))
    feed(engine, [c.model_copy(update={"slotId": 2}) for c in flat_candles([50.0] * 60)])
    feed(engine, [c.model_copy(update={"platform": "iqoption"}) for c in flat_candles([9.0] * 60)])
    first = engine.latest_snapshot("capitalbear", 1, "M1")
    same_platform = engine.latest_snapshot("capitalbear", 2, "M1")
    other_platform = engine.latest_snapshot("iqoption", 1, "M1")
    assert first and same_platform and other_platform
    assert first.trend.ema20 != same_platform.trend.ema20
    assert same_platform.trend.ema20 == pytest.approx(50.0)
    assert other_platform.trend.ema20 == pytest.approx(9.0)
    assert first.assetName != other_platform.assetName or first.platform != other_platform.platform


def test_a_new_context_starts_from_nothing() -> None:
    engine = FeatureEngine()
    feed(engine, walk(60))
    for index in range(30):
        engine.ingest_second(second(index, 100.0 + index))
    assert engine.latest_snapshot("capitalbear", 1, "M1") is not None
    fresh = engine.ingest_candle(candle(0, 7.0, 7.1, 6.9, 7.0, context=CONTEXT_B, asset="Sui OTC"))
    assert fresh is not None
    assert fresh.barCount == 1
    assert fresh.status == "WARMING"
    assert fresh.contextId == CONTEXT_B
    assert fresh.assetName == "Sui OTC"
    assert fresh.trend.ema5 is None and fresh.momentum.rsi14 is None
    assert fresh.volatility.atr14 is None and fresh.structure.confirmedPivots == 0
    assert fresh.micro.samples == 0, "one-second history belongs to the old asset"


def test_an_explicit_reset_drops_every_slot_it_names() -> None:
    engine = FeatureEngine()
    feed(engine, walk(20))
    feed(engine, [c.model_copy(update={"slotId": 3}) for c in walk(20)])
    assert engine.reset_slot("capitalbear", [1, 9]) == 1
    assert engine.latest_snapshot("capitalbear", 1, "M1") is None
    assert engine.latest_snapshot("capitalbear", 3, "M1") is not None


def test_seconds_feed_micro_state_and_follow_the_same_identity() -> None:
    engine = FeatureEngine()
    for index in range(20):
        engine.ingest_second(second(index, 100.0 * (1.001**index)))
    snapshot = feed(engine, walk(3))[-1]
    assert snapshot.micro.samples == 20
    assert snapshot.micro.microReturn1sBps == pytest.approx((1.001 - 1) * 10_000)
    engine.ingest_second(second(21, 5.0, context=CONTEXT_B))
    assert engine.latest_bundle("capitalbear", 1) is not None
    assert engine.latest_bundle("capitalbear", 1).micro.samples == 1  # type: ignore[union-attr]


def test_bundle_uses_the_platform_primary_timeframe() -> None:
    engine = FeatureEngine()
    feed(engine, walk(6, timeframe="S5"))
    feed(engine, [c.model_copy(update={"platform": "iqoption"}) for c in walk(6, timeframe="M1")])
    capitalbear = engine.latest_bundle("capitalbear", 1)
    iqoption = engine.latest_bundle("iqoption", 1)
    assert capitalbear and capitalbear.primaryTimeframe == "S5"
    assert capitalbear.primary is not None and capitalbear.primary.timeframe == "S5"
    assert iqoption and iqoption.primaryTimeframe == "M1"
    assert iqoption.primary is not None and iqoption.primary.timeframe == "M1"


def test_context_timeframes_are_joined_strictly_as_of_the_primary() -> None:
    engine = FeatureEngine()
    feed(engine, walk(4, timeframe="S5"))
    feed(engine, walk(1, timeframe="M5"))  # closes at BASE + 300s, far after the S5 bars
    bundle = engine.latest_bundle("capitalbear", 1)
    assert bundle is not None and bundle.primary is not None
    assert bundle.asOf == BASE_MS + 4 * 5_000
    assert "M5" not in bundle.contexts, "a later M5 close must never be attached"
    assert engine.latest_snapshot("capitalbear", 1, "M5") is not None
    feed(engine, walk(80, timeframe="S5")[4:])  # advance the primary past the M5 close
    later = engine.latest_bundle("capitalbear", 1)
    assert later is not None and later.primary is not None
    assert "M5" in later.contexts
    assert later.contexts["M5"].featureTime <= later.primary.featureTime
    assert all(snapshot.featureTime <= later.asOf for snapshot in later.contexts.values())


def test_every_timeframe_is_tracked_independently() -> None:
    engine = FeatureEngine()
    for timeframe in TIMEFRAMES:
        feed(engine, walk(12, timeframe=timeframe))
    diagnostics = engine.diagnostics()[0]
    assert {entry.timeframe for entry in diagnostics.timeframes} == set(TIMEFRAMES)
    assert all(entry.barCount == 12 for entry in diagnostics.timeframes)
    assert diagnostics.primaryTimeframe == "S5"
    assert diagnostics.featureVersion == FEATURE_VERSION


def test_history_stays_bounded_for_a_long_running_series() -> None:
    engine = FeatureEngine()
    feed(engine, walk(400))
    state = engine.slots[("capitalbear", 1)].timeframes["M1"]
    assert state.bar_count == 400
    assert len(state.closes) == 256
    assert len(state.snapshots) == 8
    assert len(state.pivots.highs) <= 64 and len(state.pivots.lows) <= 64


def test_every_emitted_value_is_finite_and_inside_its_documented_range() -> None:
    engine = FeatureEngine()
    for index in range(120):
        engine.ingest_second(second(index, 100.0 + math.sin(index / 4)))
    for snapshot in feed(engine, walk(200)):
        payload = snapshot.model_dump(mode="json")
        assert all(math.isfinite(value) for value in numbers(payload))
        momentum, noise = snapshot.momentum, snapshot.noise
        for bounded, ceiling in (
            (momentum.rsi14, 100),
            (momentum.stochK14, 100),
            (momentum.stochD3, 100),
            (noise.choppiness14, 100),
            (noise.efficiencyRatio10, 1),
            (noise.efficiencyRatio20, 1),
            (noise.signFlipRate10, 1),
            (snapshot.priceAction.closeLocation, 1),
            (snapshot.priceAction.bodyToRange, 1),
            (snapshot.priceAction.upperWickToRange, 1),
            (snapshot.priceAction.lowerWickToRange, 1),
        ):
            assert bounded is None or 0 <= bounded <= ceiling
        assert 0 <= snapshot.timeContext.minutePhase < 1
        assert snapshot.quality.historyBars <= 256
        assert snapshot.volatility.atr14 is None or snapshot.volatility.atr14 >= 0


def test_feature_time_is_monotonic_and_identity_is_unique_per_series() -> None:
    engine = FeatureEngine()
    snapshots = feed(engine, walk(80))
    times = [snapshot.featureTime for snapshot in snapshots]
    assert times == sorted(times) and len(set(times)) == len(times)
    identities = {
        (s.platform, s.assetName, s.contextId, s.timeframe, s.featureTime, s.featureVersion)
        for s in snapshots
    }
    assert len(identities) == len(snapshots)


def test_time_context_marks_otc_and_encodes_utc_cyclically() -> None:
    engine = FeatureEngine()
    otc = feed(engine, walk(1))[-1]
    assert otc.timeContext.isOTC is True
    assert otc.timeContext.timeframeSeconds == 60
    assert otc.timeContext.hourUtcSin**2 + otc.timeContext.hourUtcCos**2 == pytest.approx(1.0)
    plain = FeatureEngine()
    snapshot = feed(plain, walk(1, asset="EUR/USD", context=CONTEXT_A))[-1]
    assert snapshot.timeContext.isOTC is False
