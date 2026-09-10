"""T5-T11: each strategy's own opinion, its vetoes, and its handling of absent features."""

import strategy_fixtures as fixtures
from quant_engine.features import FeatureEngine
from quant_engine.strategy import CATALOG, classify, evaluate_strategies
from quant_engine.strategy.base import MIN_QUALITY_FIT
from quant_engine.strategy.common import analysis, market_gate
from quant_engine.strategy.mean_reversion import MeanReversion
from quant_engine.strategy.models import Regime, StrategyEvaluation
from quant_engine.strategy.policy import (
    BREAKOUT,
    MEAN_REVERSION,
    MICRO_IMPULSE,
    MOMENTUM_CONTINUATION,
    STRATEGY_IDS,
    TREND_FOLLOW,
    TREND_PULLBACK,
    policy_for,
)

REGIMES: tuple[Regime, ...] = (
    "TREND_UP",
    "TREND_DOWN",
    "RANGE",
    "BREAKOUT_UP",
    "BREAKOUT_DOWN",
    "VOLATILITY_EXPANSION",
    "VOLATILITY_COMPRESSION",
    "NOISY",
    "UNCERTAIN",
)
"""Every label the regime engine can emit. A strategy that omitted one would silently
fall back to its default fit in a market it was never asked about."""


def read(closes: list[float], **shape: object) -> dict[str, StrategyEvaluation]:
    bundle = fixtures.bundle(closes, **shape)  # type: ignore[arg-type]
    regime = classify(bundle)
    return {item.strategyId: item for item in evaluate_strategies(bundle, regime)}


def codes(evaluation: StrategyEvaluation) -> set[str]:
    return {veto.code for veto in evaluation.vetoes}


# --- T5 trend follow -------------------------------------------------------------------


def test_trend_follow_votes_with_a_coherent_trend() -> None:
    evaluation = read(fixtures.trending())[TREND_FOLLOW]
    assert evaluation.direction == "UP"
    assert evaluation.eligible
    assert evaluation.rawScore > 0.3
    assert evaluation.regimeFit == 1.0
    assert {reason.code for reason in evaluation.reasons} & {"EMA_STACK", "PRICE_SLOPE_10"}


def test_trend_follow_mirrors_on_a_countertrend_without_a_sign_flip_shortcut() -> None:
    up = read(fixtures.trending())[TREND_FOLLOW]
    down = read(fixtures.falling())[TREND_FOLLOW]
    assert down.direction == "DOWN"
    assert down.rawScore < -0.3
    assert abs(up.rawScore + down.rawScore) < 0.1


def test_trend_follow_abstains_in_a_range() -> None:
    evaluation = read(fixtures.ranging(), spread=0.0022)[TREND_FOLLOW]
    assert evaluation.direction == "SKIP"
    assert not evaluation.eligible
    assert codes(evaluation) == {"REGIME_UNSUITABLE"}


def test_trend_follow_declines_to_chase_a_bar_that_has_already_exploded() -> None:
    closes, highs, lows = fixtures.breakout_up()
    evaluation = read(closes, highs=highs, lows=lows)[TREND_FOLLOW]
    assert evaluation.direction == "SKIP"
    assert codes(evaluation) == {"CHASING_RISK"}


# --- T6 momentum -----------------------------------------------------------------------


def test_momentum_votes_when_several_independent_families_agree() -> None:
    evaluation = read(fixtures.trending())[MOMENTUM_CONTINUATION]
    assert evaluation.direction == "UP"
    assert read(fixtures.falling())[MOMENTUM_CONTINUATION].direction == "DOWN"


def test_momentum_reports_neutral_rather_than_averaging_families_that_disagree() -> None:
    evaluation = read(fixtures.pullback(), spread=0.0003)[MOMENTUM_CONTINUATION]
    assert evaluation.direction == "NEUTRAL"
    assert evaluation.eligible  # NEUTRAL is a valid read, not an abstention
    assert evaluation.confidence == 0.0
    assert codes(evaluation) == {"FAMILIES_DISAGREE"}


def test_momentum_abstains_when_the_path_reverses_too_often_for_continuation() -> None:
    evaluation = read(fixtures.noisy(), spread=0.0025)[MOMENTUM_CONTINUATION]
    assert evaluation.direction == "SKIP"
    assert codes(evaluation) & {"SIGN_FLIPS", "CHOPPINESS", "RANGE_OVERLAP", "REGIME_UNSUITABLE"}


# --- T7 breakout -----------------------------------------------------------------------


def test_breakout_votes_on_a_confirmed_break() -> None:
    closes, highs, lows = fixtures.breakout_up()
    evaluation = read(closes, highs=highs, lows=lows)[BREAKOUT]
    assert evaluation.direction == "UP"
    assert evaluation.eligible
    assert {reason.code for reason in evaluation.reasons} & {"THRUST_20", "CONFIRMATION"}


def test_breakout_reports_no_break_when_price_only_reached_the_level() -> None:
    closes, highs, lows = fixtures.touch_only()
    evaluation = read(closes, highs=highs, lows=lows)[BREAKOUT]
    assert evaluation.direction == "NEUTRAL"
    assert codes(evaluation) == {"NO_BREAK"}
    assert evaluation.rawScore == 0.0


def test_breakout_rejects_a_close_that_points_against_the_level_it_crossed() -> None:
    # A bar that closes beyond the prior high but at the bottom of its own range has not
    # broken anything; the wick did.
    closes, highs, lows = fixtures.quiet_band()
    evaluation = read([*closes, 100.11], highs=[*highs, 100.90], lows=[*lows, 99.97])[BREAKOUT]
    assert evaluation.direction == "NEUTRAL"
    assert codes(evaluation) & {"BREAK_REJECTED", "WEAK_CONFIRMATION"}


# --- T8 mean reversion -----------------------------------------------------------------


def test_mean_reversion_fades_a_stretch_below_a_range() -> None:
    evaluation = read(fixtures.range_stretched_low(), spread=0.0022)[MEAN_REVERSION]
    assert evaluation.direction == "UP"
    assert evaluation.regimeFit == 1.0


def test_mean_reversion_mirrors_above_the_range() -> None:
    evaluation = read(fixtures.range_stretched_high(), spread=0.0022)[MEAN_REVERSION]
    assert evaluation.direction == "DOWN"


def test_mean_reversion_refuses_an_oversold_reading_inside_a_downtrend() -> None:
    # The classic failure this strategy has to avoid: RSI at zero in a trend that is working.
    bundle = fixtures.bundle(fixtures.falling())
    assert bundle.primary is not None and bundle.primary.momentum.rsi14 is not None
    assert bundle.primary.momentum.rsi14 < 20
    evaluation = read(fixtures.falling())[MEAN_REVERSION]
    assert evaluation.direction == "SKIP"
    assert codes(evaluation) == {"REGIME_UNSUITABLE"}


def test_mean_reversion_also_guards_on_raw_trend_pressure_not_only_on_the_label() -> None:
    # The regime can read RANGE while the trend score disagrees. The strategy carries its own
    # guard so that a mislabelled environment cannot walk it into a trend.
    bundle = fixtures.bundle(fixtures.range_stretched_low(), spread=0.0022)
    gate = market_gate(bundle)
    assert bundle.primary is not None
    state = analysis(bundle, bundle.primary, gate.qualityFit)
    regime = classify(bundle, gate).model_copy(update={"trendScore": 0.9})
    opinion = MeanReversion().read(state, regime, policy_for(bundle.platform))
    assert opinion.direction == "SKIP"
    assert {veto.code for veto in opinion.vetoes} == {"TREND_PRESSURE"}


# --- T9 micro impulse ------------------------------------------------------------------


def capitalbear(seconds: list[tuple[int, float]], drift: float = 0.0006) -> StrategyEvaluation:
    engine = FeatureEngine()
    fixtures.feed_seconds(engine, seconds)
    bundle = fixtures.bundle(fixtures.trending(drift=drift), platform="capitalbear", engine=engine)
    regime = classify(bundle)
    return {item.strategyId: item for item in evaluate_strategies(bundle, regime)}[MICRO_IMPULSE]


def test_micro_impulse_votes_on_a_clean_one_second_push() -> None:
    evaluation = capitalbear(fixtures.rising_seconds())
    assert evaluation.direction == "UP"
    assert evaluation.platformFit == 1.0
    assert {reason.code for reason in evaluation.reasons} & {
        "MICRO_VELOCITY_5S",
        "MICRO_RETURN_5S",
    }


def test_micro_impulse_abstains_when_the_one_second_stream_keeps_reversing() -> None:
    evaluation = capitalbear(fixtures.alternating_seconds())
    assert evaluation.direction == "SKIP"
    assert codes(evaluation) == {"MICRO_SIGN_FLIPS"}


def test_micro_impulse_abstains_when_half_of_the_seconds_never_arrived() -> None:
    evaluation = capitalbear(fixtures.sparse_seconds())
    assert evaluation.direction == "SKIP"
    assert codes(evaluation) == {"LOW_COVERAGE"}


def test_micro_impulse_is_worth_less_on_the_platform_that_decides_on_minutes() -> None:
    engine = FeatureEngine()
    fixtures.feed_seconds(engine, fixtures.rising_seconds(), platform="iqoption")
    bundle = fixtures.bundle(fixtures.trending(), platform="iqoption", engine=engine)
    evaluation = {item.strategyId: item for item in evaluate_strategies(bundle, classify(bundle))}[
        MICRO_IMPULSE
    ]
    assert evaluation.platformFit < capitalbear(fixtures.rising_seconds()).platformFit


# --- T10 pullback ----------------------------------------------------------------------


def test_pullback_reads_a_retracement_inside_an_intact_trend_as_a_resumption() -> None:
    evaluations = read(fixtures.pullback(), spread=0.0003)
    evaluation = evaluations[TREND_PULLBACK]
    assert evaluation.direction == "UP"
    assert {reason.code for reason in evaluation.reasons} & {"RETRACEMENT", "MOMENTUM_RESET"}


def test_pullback_reports_no_pullback_while_price_is_still_extended() -> None:
    evaluation = read(fixtures.trending())[TREND_PULLBACK]
    assert evaluation.direction == "NEUTRAL"
    assert codes(evaluation) == {"NO_PULLBACK"}


def test_pullback_never_fires_in_a_range() -> None:
    evaluation = read(fixtures.ranging(), spread=0.0022)[TREND_PULLBACK]
    assert evaluation.direction == "SKIP"
    assert codes(evaluation) == {"REGIME_UNSUITABLE"}
    assert evaluation.regimeFit == 0.0


def test_pullback_declines_a_retracement_larger_than_the_trend_it_would_resume() -> None:
    evaluation = read(fixtures.pullback(dip_bars=8, dip_rate=0.0030), spread=0.0003)[TREND_PULLBACK]
    assert evaluation.direction in ("SKIP", "NEUTRAL")
    assert evaluation.rawScore == 0.0


# --- T11 absent features ---------------------------------------------------------------


def test_a_required_feature_that_is_none_makes_the_strategy_abstain() -> None:
    evaluations = read(fixtures.trending(count=12))
    warming = fixtures.bundle(fixtures.trending(count=12))
    assert warming.primary is not None
    assert warming.primary.trend.ema20 is None  # not yet warm, so genuinely absent
    for strategy_id in (TREND_FOLLOW, MOMENTUM_CONTINUATION, TREND_PULLBACK):
        assert evaluations[strategy_id].direction == "SKIP"
        assert codes(evaluations[strategy_id]) == {"MISSING_REQUIRED_FEATURE"}


def test_an_absent_feature_is_never_treated_as_a_zero_reading() -> None:
    # Zero-filling would let a warming series produce a confident neutral instead of an
    # abstention, and would move every aggregate toward the middle.
    evaluations = read(fixtures.trending(count=12))
    assert all(item.rawScore == 0.0 for item in evaluations.values() if not item.eligible)
    assert all(item.confidence == 0.0 for item in evaluations.values() if not item.eligible)
    assert all(item.evidenceCoverage == 0.0 for item in evaluations.values() if not item.eligible)


def test_a_faster_strategy_may_still_evaluate_while_slower_ones_are_warming() -> None:
    # Warming is not a blanket rejection: breakout only needs a ten-bar prior range.
    evaluations = read(fixtures.trending(count=12))
    assert evaluations[BREAKOUT].eligible


def test_dirty_inputs_reduce_quality_fit_until_no_strategy_will_read_them() -> None:
    evaluations = read(fixtures.trending(), quality="DEGRADED", coverage=0.3)
    assert all(item.qualityFit < MIN_QUALITY_FIT for item in evaluations.values())
    assert all(item.direction == "SKIP" for item in evaluations.values())


def test_every_catalog_member_is_evaluated_exactly_once_in_a_fixed_order() -> None:
    bundle = fixtures.bundle(fixtures.trending())
    evaluations = evaluate_strategies(bundle, classify(bundle))
    assert [item.strategyId for item in evaluations] == list(STRATEGY_IDS)


def test_every_strategy_declares_a_usable_contract() -> None:
    # The base class does the eligibility work from these declarations, so an empty or absent
    # one would silently turn a strategy into something that never abstains.
    for strategy in CATALOG:
        assert strategy.id in STRATEGY_IDS
        assert strategy.required, f"{strategy.id} declares no required features"
        assert all(path.startswith(("primary.", "micro.")) for path in strategy.required), (
            f"{strategy.id} declares a feature path outside the bundle"
        )
        assert 0 < strategy.min_evidence < 1
        assert set(strategy.regime_fit) == set(REGIMES)
        assert all(0.0 <= fit <= 1.0 for fit in strategy.regime_fit.values())


def test_a_strategy_applies_the_threshold_it_declares() -> None:
    # Reading the constant directly instead of the attribute would let the two drift apart.
    evaluations = read(fixtures.trending())
    for strategy in CATALOG:
        evaluation = evaluations[strategy.id]
        if evaluation.direction in ("UP", "DOWN"):
            assert abs(evaluation.rawScore) >= strategy.min_evidence
        elif evaluation.direction == "NEUTRAL":
            assert abs(evaluation.rawScore) < strategy.min_evidence
