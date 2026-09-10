"""T1-T4: regime classification over deterministic series built through the real Phase 6 engine."""

from uuid import UUID

import pytest
import strategy_fixtures as fixtures
from quant_engine.features import FEATURE_VERSION, FeatureBundle
from quant_engine.strategy import REGIME_VERSION, classify
from quant_engine.strategy.models import SUPPORTED_FEATURE_VERSION
from quant_engine.strategy.regime import NOISY_FLOOR, RANGE_FLOOR, TREND_FLOOR


def test_a_coherent_advance_is_read_as_a_trend_up() -> None:
    snapshot = classify(fixtures.bundle(fixtures.trending()))
    assert snapshot.primaryRegime == "TREND_UP"
    assert snapshot.trendScore >= TREND_FLOOR
    assert snapshot.directionBias == "UP"
    assert snapshot.noiseScore < 0.2
    assert snapshot.status == "OK"


def test_a_coherent_decline_mirrors_it() -> None:
    snapshot = classify(fixtures.bundle(fixtures.falling()))
    assert snapshot.primaryRegime == "TREND_DOWN"
    assert snapshot.trendScore <= -TREND_FLOOR
    assert snapshot.directionBias == "DOWN"


def test_the_two_directions_are_not_a_sign_flip_of_one_another() -> None:
    # Mirrored inputs should give mirrored readings, but through the same code path rather
    # than by negating a result. Asymmetric handling anywhere shows up as a mismatch here.
    up = classify(fixtures.bundle(fixtures.trending()))
    down = classify(fixtures.bundle(fixtures.falling()))
    assert up.trendScore == pytest.approx(-down.trendScore, abs=0.08)
    assert up.confidence == pytest.approx(down.confidence, abs=0.08)


def test_an_oscillating_series_with_no_displacement_is_a_range() -> None:
    snapshot = classify(fixtures.bundle(fixtures.ranging(), spread=0.0022))
    assert snapshot.primaryRegime == "RANGE"
    assert snapshot.rangeScore >= RANGE_FLOOR
    assert abs(snapshot.trendScore) < TREND_FLOOR
    assert snapshot.directionBias == "NEUTRAL"


def test_a_range_is_not_merely_a_midrange_rsi() -> None:
    # A steady advance also passes through RSI values a naive rule would call neutral; the
    # range reading has to come from the path, not from an oscillator sitting in the middle.
    trending = classify(fixtures.bundle(fixtures.trending(drift=0.0004)))
    assert trending.rangeScore < RANGE_FLOOR


def test_a_decisive_close_beyond_a_prior_extreme_is_a_breakout() -> None:
    closes, highs, lows = fixtures.breakout_up()
    snapshot = classify(fixtures.bundle(closes, highs=highs, lows=lows))
    assert snapshot.primaryRegime == "BREAKOUT_UP"
    assert snapshot.breakoutScore > 0.5
    assert snapshot.directionBias == "UP"


def test_a_breakout_down_mirrors_it() -> None:
    closes, highs, lows = fixtures.quiet_band()
    snapshot = classify(
        fixtures.bundle([*closes, 99.10], highs=[*highs, 100.03], lows=[*lows, 99.05])
    )
    assert snapshot.primaryRegime == "BREAKOUT_DOWN"
    assert snapshot.breakoutScore < -0.5


def test_reaching_a_level_without_closing_beyond_it_is_not_a_breakout() -> None:
    closes, highs, lows = fixtures.touch_only()
    snapshot = classify(fixtures.bundle(closes, highs=highs, lows=lows))
    assert snapshot.breakoutScore == 0.0
    assert snapshot.primaryRegime != "BREAKOUT_UP"


def test_alternating_returns_are_noise_and_not_a_confident_range() -> None:
    snapshot = classify(fixtures.bundle(fixtures.noisy(), spread=0.0025))
    assert snapshot.primaryRegime == "NOISY"
    assert snapshot.noiseScore >= NOISY_FLOOR
    # Range and noise share their inputs, so the range score is high too. Noise settles the
    # label because it says the structure cannot be read at all, and the contested reading
    # is reported as reduced confidence rather than hidden.
    assert snapshot.rangeScore > RANGE_FLOOR
    assert snapshot.confidence < 0.5
    assert snapshot.directionBias == "NEUTRAL"


def test_confidence_falls_when_a_contradicting_reading_is_also_strong() -> None:
    clean = classify(fixtures.bundle(fixtures.trending()))
    contested = classify(fixtures.bundle(fixtures.pullback(), spread=0.0003))
    assert clean.primaryRegime == contested.primaryRegime == "TREND_UP"
    assert contested.rangeScore > clean.rangeScore
    assert contested.confidence < clean.confidence


def test_confidence_is_scaled_by_how_good_the_inputs_were() -> None:
    clean = classify(fixtures.bundle(fixtures.trending()))
    warming = classify(fixtures.bundle(fixtures.trending(count=12)))
    assert warming.status == "WARMING"
    assert warming.qualityFit < clean.qualityFit
    assert warming.confidence < clean.confidence


def test_every_snapshot_carries_the_three_version_stamps() -> None:
    snapshot = classify(fixtures.bundle(fixtures.trending()))
    assert snapshot.featureVersion == SUPPORTED_FEATURE_VERSION == FEATURE_VERSION
    assert snapshot.regimeVersion == REGIME_VERSION
    assert snapshot.asOf == fixtures.bundle(fixtures.trending()).asOf


def test_an_unusable_bundle_reports_uncertain_and_skip_rather_than_a_guess() -> None:
    bundle = fixtures.bundle(fixtures.trending())
    empty = FeatureBundle(
        platform=bundle.platform,
        slotId=bundle.slotId,
        assetName=bundle.assetName,
        contextId=bundle.contextId,
        asOf=bundle.asOf,
        primaryTimeframe=bundle.primaryTimeframe,
        featureVersion=bundle.featureVersion,
        primary=None,
        micro=bundle.micro,
        contexts={},
    )
    snapshot = classify(empty)
    assert snapshot.primaryRegime == "UNCERTAIN"
    assert snapshot.directionBias == "SKIP"
    assert snapshot.status == "INVALID"
    assert snapshot.confidence == 0.0
    assert {veto.code for veto in snapshot.vetoes} == {"MISSING_PRIMARY_FEATURES"}


def test_reasons_are_codes_with_values_not_a_dump_of_every_feature() -> None:
    snapshot = classify(fixtures.bundle(fixtures.trending()))
    assert 0 < len(snapshot.reasons) <= 8
    assert snapshot.reasons[0].code == "REGIME_TREND_UP"
    assert all(reason.code and reason.message for reason in snapshot.reasons)


def test_a_context_snapshot_from_a_different_series_is_refused() -> None:
    bundle = fixtures.bundle(fixtures.trending())
    other = fixtures.bundle(fixtures.trending(), asset="GBP/USD OTC").primary
    assert other is not None
    mixed = bundle.model_copy(update={"contexts": {"M5": other}})
    snapshot = classify(mixed)
    assert snapshot.status == "INVALID"
    assert {veto.code for veto in snapshot.vetoes} == {"CONTEXT_CHANGED"}


def test_an_identity_change_on_the_primary_snapshot_is_refused() -> None:
    bundle = fixtures.bundle(fixtures.trending())
    assert bundle.primary is not None
    stale = bundle.primary.model_copy(
        update={"contextId": UUID("33333333-3333-4333-8333-333333333333")}
    )
    snapshot = classify(bundle.model_copy(update={"primary": stale}))
    assert snapshot.status == "INVALID"
    assert {veto.code for veto in snapshot.vetoes} == {"CONTEXT_CHANGED"}
