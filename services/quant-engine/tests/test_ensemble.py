"""T12-T14, T18-T20: the version contract, the as-of boundary, and the ensemble arithmetic."""

import ast
import inspect
from pathlib import Path
from types import ModuleType
from uuid import UUID

import pytest
import strategy_fixtures as fixtures
from quant_engine.features import FeatureEngine
from quant_engine.strategy import (
    REGIME_VERSION,
    STRATEGY_VERSION,
    VETO_CODES,
    common,
    ensemble,
    evaluate_bundle,
)
from quant_engine.strategy.ensemble import (
    MAX_DISAGREEMENT,
    MIN_ACTIVE_WEIGHT,
    NOISE_CEILING,
    Tally,
    confidence_for,
    direction_for,
    noise_damping,
    tally,
    vetoes_for,
)
from quant_engine.strategy.models import (
    SUPPORTED_FEATURE_VERSION,
    Direction,
    RegimeSnapshot,
    StrategyEvaluation,
)
from quant_engine.strategy.policy import (
    BREAKOUT,
    MEAN_REVERSION,
    MOMENTUM_CONTINUATION,
    TREND_FOLLOW,
)


def vote(
    strategy_id: str,
    direction: Direction,
    confidence: float,
    *,
    regime_fit: float = 1.0,
    quality_fit: float = 1.0,
    platform_fit: float = 1.0,
) -> StrategyEvaluation:
    """A hand-built evaluation, so the ensemble arithmetic can be exercised on its own."""
    return StrategyEvaluation(
        strategyId=strategy_id,
        strategyVersion=STRATEGY_VERSION,
        platform="iqoption",
        slotId=1,
        assetName=fixtures.ASSET,
        contextId=UUID("11111111-1111-4111-8111-111111111111"),
        asOf=1,
        eligible=direction != "SKIP",
        direction=direction,
        confidence=confidence,
        rawScore=confidence if direction == "UP" else -confidence if direction == "DOWN" else 0.0,
        regimeFit=regime_fit,
        qualityFit=quality_fit,
        platformFit=platform_fit,
        evidenceCoverage=1.0,
        featureVersion=SUPPORTED_FEATURE_VERSION,
        regimeVersion=REGIME_VERSION,
    )


def regime_reading(noise: float = 0.1, confidence: float = 0.8) -> RegimeSnapshot:
    return RegimeSnapshot(
        platform="iqoption",
        slotId=1,
        assetName=fixtures.ASSET,
        contextId=UUID("11111111-1111-4111-8111-111111111111"),
        asOf=1,
        primaryTimeframe="M1",
        featureVersion=SUPPORTED_FEATURE_VERSION,
        regimeVersion=REGIME_VERSION,
        primaryRegime="TREND_UP",
        trendScore=0.7,
        rangeScore=0.1,
        breakoutScore=0.0,
        noiseScore=noise,
        volatilityExpansionScore=0.2,
        volatilityCompressionScore=0.2,
        directionBias="UP",
        confidence=confidence,
        qualityFit=1.0,
        status="OK",
    )


# --- T12 feature version ---------------------------------------------------------------


def test_a_qfe_v2_bundle_is_accepted() -> None:
    snapshot = evaluate_bundle(fixtures.bundle(fixtures.trending()))
    assert snapshot.featureVersion == "qfe-v2"
    assert snapshot.status == "OK"
    assert snapshot.direction == "UP"


def test_an_older_feature_contract_is_refused_rather_than_silently_consumed() -> None:
    bundle = fixtures.bundle(fixtures.trending())
    snapshot = evaluate_bundle(bundle.model_copy(update={"featureVersion": "qfe-v1"}))
    assert snapshot.direction == "SKIP"
    assert snapshot.status == "INVALID"
    assert {veto.code for veto in snapshot.vetoes} == {"UNSUPPORTED_FEATURE_VERSION"}
    assert snapshot.strategies == []


def test_a_primary_snapshot_from_an_older_contract_is_refused_too() -> None:
    bundle = fixtures.bundle(fixtures.trending())
    assert bundle.primary is not None
    stale = bundle.primary.model_copy(update={"featureVersion": "qfe-v1"})
    snapshot = evaluate_bundle(bundle.model_copy(update={"primary": stale}))
    assert {veto.code for veto in snapshot.vetoes} == {"UNSUPPORTED_FEATURE_VERSION"}


def test_the_supported_contract_is_pinned_as_a_literal() -> None:
    # Deliberately a literal rather than an import of FEATURE_VERSION: a Phase 6 formula
    # change must break Phase 7 loudly, not flow into thresholds nobody re-checked.
    assert SUPPORTED_FEATURE_VERSION == "qfe-v2"
    assert STRATEGY_VERSION == "qst-strategy-v1"
    assert REGIME_VERSION == "qst-regime-v1"


def test_every_row_carries_all_three_version_stamps() -> None:
    snapshot = evaluate_bundle(fixtures.bundle(fixtures.trending()))
    assert (snapshot.featureVersion, snapshot.regimeVersion, snapshot.strategyVersion) == (
        SUPPORTED_FEATURE_VERSION,
        REGIME_VERSION,
        STRATEGY_VERSION,
    )
    assert snapshot.regime.regimeVersion == REGIME_VERSION
    for item in snapshot.strategies:
        assert item.featureVersion == SUPPORTED_FEATURE_VERSION
        assert item.regimeVersion == REGIME_VERSION
        assert item.strategyVersion == STRATEGY_VERSION


# --- T13 as-of -------------------------------------------------------------------------


def test_only_contexts_the_engine_already_joined_as_of_are_consulted() -> None:
    # A higher timeframe changes the reading, and the only way it can reach a strategy is
    # through the bundle the feature engine already joined as-of the primary close. Nothing
    # in Phase 7 fetches a timeframe for itself.
    bare = fixtures.bundle(fixtures.trending())
    assert bare.contexts == {}

    engine = FeatureEngine()
    fixtures.feed(engine, fixtures.bars(fixtures.trending(), timeframe="M1"))
    fixtures.feed(engine, fixtures.bars(fixtures.falling(count=20), timeframe="M5"))
    joined = engine.latest_bundle("iqoption", 1)
    assert joined is not None and "M5" in joined.contexts
    assert joined.contexts["M5"].featureTime <= joined.asOf
    assert evaluate_bundle(joined).strategies != evaluate_bundle(bare).strategies


def test_a_context_that_closes_after_the_bundle_is_refused() -> None:
    bundle = fixtures.bundle(fixtures.trending())
    assert bundle.primary is not None
    future = bundle.primary.model_copy(
        update={"timeframe": "M5", "featureTime": bundle.asOf + 60_000}
    )
    snapshot = evaluate_bundle(bundle.model_copy(update={"contexts": {"M5": future}}))
    assert snapshot.direction == "SKIP"
    assert {veto.code for veto in snapshot.vetoes} == {"CHRONOLOGY_INVALID"}


def test_a_primary_snapshot_ahead_of_its_own_bundle_is_refused() -> None:
    bundle = fixtures.bundle(fixtures.trending())
    snapshot = evaluate_bundle(bundle.model_copy(update={"asOf": bundle.asOf - 1}))
    assert {veto.code for veto in snapshot.vetoes} == {"CHRONOLOGY_INVALID"}


def test_a_bundle_whose_primary_is_not_the_platforms_decision_horizon_is_refused() -> None:
    bundle = fixtures.bundle(fixtures.trending())
    snapshot = evaluate_bundle(bundle.model_copy(update={"primaryTimeframe": "M10"}))
    assert {veto.code for veto in snapshot.vetoes} == {"PRIMARY_TIMEFRAME_MISMATCH"}


# --- T14 determinism -------------------------------------------------------------------


def test_the_same_bundle_always_produces_the_same_snapshot() -> None:
    bundle = fixtures.bundle(fixtures.trending())
    first, second = evaluate_bundle(bundle), evaluate_bundle(bundle)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_two_identical_series_produce_identical_snapshots() -> None:
    first = evaluate_bundle(fixtures.bundle(fixtures.trending()))
    second = evaluate_bundle(fixtures.bundle(fixtures.trending()))
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


# --- T18-T20 the ensemble arithmetic ---------------------------------------------------


def test_agreement_is_the_net_vote_over_the_whole_active_panel() -> None:
    counted = tally([vote(TREND_FOLLOW, "UP", 0.8), vote(BREAKOUT, "UP", 0.8)], 1.0)
    assert counted.weights.up == pytest.approx(0.8 + 0.8)
    assert counted.weights.down == 0.0
    assert counted.disagreement == 0.0
    assert counted.agreement == pytest.approx(1.0)
    assert counted.eligible == 2 and counted.active == 2


def test_a_strategy_that_saw_no_edge_still_consumes_capacity() -> None:
    decided = tally([vote(TREND_FOLLOW, "UP", 0.8)], 1.0)
    with_neutral = tally([vote(TREND_FOLLOW, "UP", 0.8), vote(BREAKOUT, "NEUTRAL", 0.0)], 1.0)
    assert with_neutral.weights.neutral > 0
    assert with_neutral.agreement < decided.agreement
    assert with_neutral.active == 1 and with_neutral.eligible == 2


def test_a_strategy_that_skipped_contributes_nothing_at_all() -> None:
    with_skip = tally([vote(TREND_FOLLOW, "UP", 0.8), vote(BREAKOUT, "SKIP", 0.0)], 1.0)
    alone = tally([vote(TREND_FOLLOW, "UP", 0.8)], 1.0)
    assert with_skip.weights == alone.weights
    assert with_skip.eligible == 1


def test_conflicting_opinions_lower_confidence_rather_than_averaging_away() -> None:
    regime = regime_reading()
    agreeing = tally(
        [
            vote(TREND_FOLLOW, "UP", 0.7),
            vote(BREAKOUT, "UP", 0.7),
            vote(MOMENTUM_CONTINUATION, "UP", 0.7),
        ],
        1.0,
    )
    conflicted = tally(
        [
            vote(TREND_FOLLOW, "UP", 0.7),
            vote(MEAN_REVERSION, "DOWN", 0.5),
            vote(MOMENTUM_CONTINUATION, "NEUTRAL", 0.0),
        ],
        1.0,
    )
    assert conflicted.disagreement > 0
    assert conflicted.agreement < agreeing.agreement
    assert confidence_for("UP", conflicted, regime, 1.0) < confidence_for(
        "UP", agreeing, regime, 1.0
    )


def test_an_almost_even_split_is_skipped_rather_than_resolved_by_a_hair() -> None:
    counted = tally([vote(TREND_FOLLOW, "UP", 0.62), vote(MEAN_REVERSION, "DOWN", 0.60)], 1.0)
    assert counted.disagreement >= MAX_DISAGREEMENT
    vetoes = vetoes_for(regime_reading(), counted)
    assert [veto.code for veto in vetoes] == ["CONFLICT_TOO_HIGH"]
    assert direction_for(counted, vetoes) == "SKIP"


def test_extreme_noise_skips_even_when_one_strategy_votes_strongly() -> None:
    counted = tally([vote(TREND_FOLLOW, "UP", 1.0), vote(BREAKOUT, "UP", 1.0)], 1.0)
    assert counted.agreement == pytest.approx(1.0)
    vetoes = vetoes_for(regime_reading(noise=NOISE_CEILING), counted)
    assert [veto.code for veto in vetoes] == ["EXTREME_NOISE"]
    assert direction_for(counted, vetoes) == "SKIP"
    assert confidence_for("SKIP", counted, regime_reading(noise=NOISE_CEILING), 1.0) == 0.0


def test_noise_below_what_an_ordinary_range_shows_costs_no_capacity() -> None:
    assert noise_damping(0.3) == 1.0
    assert noise_damping(0.45) == 1.0
    assert noise_damping(0.6) < 1.0
    assert noise_damping(NOISE_CEILING) < noise_damping(0.6)


def test_when_every_strategy_abstains_the_ensemble_says_so_explicitly() -> None:
    counted = tally([vote(TREND_FOLLOW, "SKIP", 0.0), vote(BREAKOUT, "SKIP", 0.0)], 1.0)
    assert counted == Tally(counted.weights, 0.0, 0.0, 0, 0, [])
    vetoes = vetoes_for(regime_reading(), counted)
    assert [veto.code for veto in vetoes] == ["NO_ELIGIBLE_STRATEGY"]
    assert direction_for(counted, vetoes) == "SKIP"


def test_one_heavily_damped_opinion_is_not_a_panel() -> None:
    counted = tally([vote(TREND_FOLLOW, "UP", 0.9, quality_fit=0.3)], 1.0)
    assert counted.weights.active < MIN_ACTIVE_WEIGHT
    assert [veto.code for veto in vetoes_for(regime_reading(), counted)] == [
        "INSUFFICIENT_STRATEGY_WEIGHT"
    ]


def test_a_weak_net_vote_is_neutral_not_a_direction() -> None:
    counted = tally(
        [
            vote(TREND_FOLLOW, "UP", 0.10),
            vote(BREAKOUT, "NEUTRAL", 0.0),
            vote(MEAN_REVERSION, "NEUTRAL", 0.0),
        ],
        1.0,
    )
    assert direction_for(counted, []) == "NEUTRAL"


def test_confidence_reflects_breadth_regime_and_quality_and_is_never_a_probability() -> None:
    counted = tally([vote(TREND_FOLLOW, "UP", 0.9), vote(BREAKOUT, "UP", 0.9)], 1.0)
    full = confidence_for("UP", counted, regime_reading(confidence=0.9), 1.0)
    assert confidence_for("UP", counted, regime_reading(confidence=0.2), 1.0) < full
    assert confidence_for("UP", counted, regime_reading(confidence=0.9), 0.5) < full
    # Two of four eligible: perfect agreement still cannot reach certainty on half a panel.
    assert full < 1.0
    assert (
        "winProbability" not in evaluate_bundle(fixtures.bundle(fixtures.trending())).model_dump()
    )


def test_an_extreme_noise_market_skips_end_to_end() -> None:
    snapshot = evaluate_bundle(fixtures.bundle(fixtures.noisy(), spread=0.0025))
    assert snapshot.regime.primaryRegime == "NOISY"
    assert snapshot.direction == "SKIP"
    assert snapshot.confidence == 0.0
    assert snapshot.vetoes and snapshot.vetoes[0].code == "EXTREME_NOISE"


def test_coverage_too_low_to_act_on_skips_and_reports_the_coverage_not_the_panel() -> None:
    snapshot = evaluate_bundle(fixtures.bundle(fixtures.trending(), coverage=0.3))
    assert snapshot.direction == "SKIP"
    assert {veto.code for veto in snapshot.vetoes} == {"LOW_COVERAGE"}


def _reason_codes_in(module: ModuleType) -> set[str]:
    """Every literal ``code=`` the module passes when it builds a Reason."""
    tree = ast.parse(Path(inspect.getfile(module)).read_text())
    return {
        keyword.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg == "code"
        and isinstance(keyword.value, ast.Constant)
        and isinstance(keyword.value.value, str)
    }


def test_every_veto_the_engine_can_emit_is_in_the_documented_catalogue() -> None:
    # VETO_CODES is the published list of reasons a SKIP can carry. A code raised by the gate
    # or the ensemble but missing from it would be an abstention nobody could look up.
    emitted = _reason_codes_in(common) | _reason_codes_in(ensemble)
    assert emitted, "the scan must actually be finding codes"
    assert emitted <= set(VETO_CODES), emitted - set(VETO_CODES)
    assert {"EXTREME_NOISE", "LOW_COVERAGE", "CONFLICT_TOO_HIGH"} <= emitted
