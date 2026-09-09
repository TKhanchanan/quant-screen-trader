"""W1-W7, W9: the numeric primitives and each indicator, against hand-computed values."""

import math

import pytest
from features_fixtures import candle
from quant_engine.features.math import (
    bps,
    clamp,
    log_price_slope_bps,
    log_return_bps,
    mean,
    ols_slope,
    pstdev,
    safe_div,
)
from quant_engine.features.momentum import MACDState, RSIState, StochasticState, roc_bps
from quant_engine.features.noise import choppiness, efficiency_ratio, range_overlap, sign_flip_rate
from quant_engine.features.structure import PivotTracker, prior_range
from quant_engine.features.trend import EMAState, price_slope_bps
from quant_engine.features.volatility import (
    ATRState,
    bollinger,
    range_expansion,
    realized_volatility_bps,
    true_range,
)


def test_safe_primitives_never_produce_nan_or_infinity() -> None:
    assert safe_div(1, 0) is None
    assert safe_div(0, 0) is None
    assert safe_div(3, 2) == 1.5
    assert bps(1.1, 1.0) == pytest.approx(1000)
    assert bps(1.0, 0) is None
    assert log_return_bps(math.e, 1) == pytest.approx(10_000)
    assert log_return_bps(-1, 1) is None
    assert log_return_bps(1, 0) is None
    assert mean([]) is None
    assert mean([1, 2, 3]) == 2
    assert pstdev([5]) is None
    assert pstdev([2, 4]) == 1.0
    assert clamp(-3, 0, 1) == 0 and clamp(9, 0, 1) == 1


def test_ols_slope_matches_manual_least_squares() -> None:
    assert ols_slope([1]) is None
    assert ols_slope([0, 2, 4, 6]) == pytest.approx(2.0)
    assert ols_slope([5, 5, 5]) == pytest.approx(0.0)
    # log(close) rising by a constant factor gives a constant slope in basis points.
    closes = [100 * (1.01**step) for step in range(5)]
    assert log_price_slope_bps(closes) == pytest.approx(math.log(1.01) * 10_000)
    assert log_price_slope_bps([1, -1, 2]) is None


def test_ema_seeds_with_sma_and_then_smooths() -> None:
    ema = EMAState(5)
    for value in [1, 2, 3, 4]:
        assert ema.update(value) is None
    assert ema.update(5) == pytest.approx(3.0)  # SMA seed of 1..5
    assert ema.update(6) == pytest.approx(4.0)  # 1/3 * 6 + 2/3 * 3
    assert ema.slope_bps() is None  # three bars of EMA history are still missing
    for value in [7, 8, 9]:
        ema.update(value)
    assert ema.slope_bps() is not None


def test_ema_of_a_constant_series_is_that_constant() -> None:
    ema = EMAState(9)
    for _ in range(30):
        ema.update(42.0)
    assert ema.value == pytest.approx(42.0)
    assert ema.slope_bps() == pytest.approx(0.0)


def test_wilder_rsi_extremes_flat_and_a_hand_computed_step() -> None:
    rising = RSIState()
    for close in range(1, 16):
        rising.update(float(close))
    assert rising.value == 100.0
    falling = RSIState()
    for close in range(15, 0, -1):
        falling.update(float(close))
    assert falling.value == 0.0
    flat = RSIState()
    for _ in range(20):
        flat.update(5.0)
    assert flat.value == 50.0  # documented neutral: no gains and no losses at all
    # avgGain 13/14, avgLoss 0.5/14 -> RS 26 -> 100 - 100/27
    step = RSIState()
    for close in range(1, 16):
        step.update(float(close))
    assert step.update(14.5) == pytest.approx(100 - 100 / 27)


def test_rsi_stays_inside_its_range_for_arbitrary_walks() -> None:
    state = RSIState()
    price = 100.0
    for index in range(200):
        price *= 1.001 if index % 3 else 0.9985
        value = state.update(price)
        assert value is None or 0 <= value <= 100


def test_true_range_and_wilder_atr() -> None:
    assert true_range(102, 100, None) == 2
    assert true_range(102, 100, 95) == 7
    assert true_range(102, 100, 110) == 10
    atr = ATRState()
    for _ in range(13):
        assert atr.update(2.0) is None
    assert atr.update(2.0) == pytest.approx(2.0)
    assert atr.update(10.0) == pytest.approx(36 / 14)
    assert range_expansion(10.0, None) is None
    assert range_expansion(10.0, 36 / 14) == pytest.approx(10 / (36 / 14))


def test_stochastic_extremes_zero_range_and_smoothing_warm_up() -> None:
    stochastic = StochasticState()
    highs = [float(value) for value in range(1, 15)]
    lows = [value - 1 for value in highs]
    percent_k, percent_d = stochastic.update(highs, lows, 14.0)
    assert percent_k == pytest.approx(100.0)
    assert percent_d is None  # only one %K exists so far
    for _ in range(2):
        percent_k, percent_d = stochastic.update(highs, lows, 14.0)
    assert percent_d == pytest.approx(100.0)
    flat = StochasticState()
    constant = [5.0] * 14
    assert flat.update(constant, constant, 5.0) == (None, None)


def test_macd_of_a_constant_series_collapses_to_zero() -> None:
    macd = MACDState()
    for _ in range(80):
        macd.update(50.0)
    assert macd.macd == pytest.approx(0.0, abs=1e-9)
    assert macd.signal_value == pytest.approx(0.0, abs=1e-9)
    assert macd.histogram == pytest.approx(0.0, abs=1e-9)
    rising = MACDState()
    for step in range(80):
        rising.update(50.0 + step)
    assert rising.macd is not None and rising.macd > 0


def test_roc_and_price_slope_need_their_full_window() -> None:
    closes = [100.0 * (1.01**step) for step in range(11)]
    assert roc_bps(closes[:3], 5) is None
    assert roc_bps(closes, 5) == pytest.approx((closes[-1] / closes[-6] - 1) * 10_000)
    assert price_slope_bps(closes[:4], 5) is None
    assert price_slope_bps(closes, 5) == pytest.approx(math.log(1.01) * 10_000)


def test_bollinger_handles_a_flat_series_and_matches_population_std() -> None:
    flat = bollinger([7.0] * 20)
    # Zero dispersion still has real bands; only the ratios that divide by it are unavailable.
    assert (flat.middle, flat.upper, flat.lower) == (7.0, 7.0, 7.0)
    assert flat.width_bps == pytest.approx(0.0)
    assert flat.percent_b is None and flat.z_score is None
    closes = [float(value) for value in range(1, 21)]
    bands = bollinger(closes)
    expected_std = math.sqrt((20**2 - 1) / 12)
    assert bands.middle == pytest.approx(10.5)
    assert bands.upper == pytest.approx(10.5 + 2 * expected_std)
    assert bands.z_score == pytest.approx((20 - 10.5) / expected_std)
    assert bands.lower is not None and bands.upper is not None
    assert bands.percent_b == pytest.approx((20 - bands.lower) / (bands.upper - bands.lower))
    assert bollinger(closes[:5]).middle is None


def test_realized_volatility_is_population_stdev_of_log_returns() -> None:
    closes = [100.0 * (1.01**step) for step in range(21)]
    assert realized_volatility_bps(closes, 10) == pytest.approx(0.0, abs=1e-6)
    assert realized_volatility_bps(closes[:5], 10) is None
    mixed = [100.0, 101.0, 100.0, 101.0, 100.0, 101.0, 100.0, 101.0, 100.0, 101.0, 100.0]
    value = realized_volatility_bps(mixed, 10)
    assert value is not None and value > 0


def test_efficiency_ratio_separates_a_straight_path_from_a_round_trip() -> None:
    straight = [float(100 + step) for step in range(11)]
    assert efficiency_ratio(straight, 10) == pytest.approx(1.0)
    zigzag = [100.0 + (step % 2) for step in range(11)]
    assert efficiency_ratio(zigzag, 10) == pytest.approx(0.0)
    assert efficiency_ratio(straight[:3], 10) is None
    assert efficiency_ratio([100.0] * 11, 10) is None  # no distance travelled at all


def test_choppiness_matches_the_formula_and_guards_zero_span() -> None:
    ranges = [1.0] * 14
    highs = [float(100 + step) for step in range(14)]
    lows = [value - 1 for value in highs]
    span = max(highs) - min(lows)
    expected = 100 * math.log10(sum(ranges) / span) / math.log10(14)
    assert choppiness(ranges, highs, lows) == pytest.approx(expected)
    assert 0 <= (choppiness(ranges, highs, lows) or 0) <= 100
    assert choppiness(ranges, [5.0] * 14, [5.0] * 14) is None
    assert choppiness(ranges[:3], highs, lows) is None


def test_sign_flip_rate_counts_only_non_zero_returns() -> None:
    assert sign_flip_rate([1, 1, 1, 1]) == pytest.approx(0.0)
    assert sign_flip_rate([1, -1, 1, -1]) == pytest.approx(1.0)
    assert sign_flip_rate([1, 0, 0, -1]) == pytest.approx(1.0)
    assert sign_flip_rate([0, 0]) is None


def test_range_overlap_skips_candles_without_a_range() -> None:
    highs = [10.0, 10.0, 10.0]
    lows = [9.0, 9.0, 9.0]
    assert range_overlap(highs, lows) == pytest.approx(1.0)
    assert range_overlap([10.0, 20.0], [9.0, 19.0]) == pytest.approx(0.0)
    assert range_overlap([10.0, 10.0], [10.0, 9.0]) is None


def test_prior_range_never_includes_the_current_candle() -> None:
    highs = [10.0, 11.0, 12.0, 13.0, 14.0, 99.0]
    lows = [1.0, 2.0, 3.0, 4.0, 5.0, 0.5]
    window = prior_range(highs, lows, 5)
    assert window.high == 14.0  # the 99.0 spike is the current candle
    assert window.low == 1.0
    assert prior_range(highs[:3], lows[:3], 5).high is None


def test_a_pivot_only_exists_once_its_right_hand_bars_have_closed() -> None:
    tracker = PivotTracker()
    shape = [(10.0, 5.0), (11.0, 6.0), (20.0, 7.0), (12.0, 6.5), (11.5, 6.2)]
    for index, (high, low) in enumerate(shape):
        tracker.update(high, low)
        if index < 4:
            assert not tracker.highs, "the candidate pivot must wait for two closed right bars"
    assert list(tracker.highs) == [20.0]


def test_candle_fixture_keeps_close_inside_its_range() -> None:
    fixture = candle(0, 1.0, 1.2, 0.9, 1.1)
    assert fixture.low <= fixture.close <= fixture.high
    assert fixture.state == "CLOSED"
