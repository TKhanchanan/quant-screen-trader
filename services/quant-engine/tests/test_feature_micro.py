"""W10: one-second micro features against hand-computed values."""

import math

import pytest
from quant_engine.features.micro import MicroState


def rising(count: int, *, skip: set[int] | None = None, step: float = 1.01) -> MicroState:
    state = MicroState()
    for index in range(count):
        if skip and index in skip:
            continue
        state.ingest(index, 100.0 * (step**index))
    return state


def test_micro_returns_velocity_and_acceleration_on_a_geometric_path() -> None:
    features = rising(10).features()
    assert features.samples == 10
    assert features.lastSecond == 9
    assert features.microReturn1sBps == pytest.approx((1.01 - 1) * 10_000)
    assert features.microReturn3sBps == pytest.approx((1.01**3 - 1) * 10_000)
    assert features.microReturn5sBps == pytest.approx((1.01**5 - 1) * 10_000)
    assert features.microVelocity3s == pytest.approx(math.log(1.01) * 10_000)
    assert features.microVelocity5s == pytest.approx(math.log(1.01) * 10_000)
    # A constant per-second return means no change in that return.
    assert features.microAcceleration1s == pytest.approx(0.0, abs=1e-9)


def test_micro_volatility_range_and_efficiency() -> None:
    features = rising(31).features()
    assert features.microVol5sBps == pytest.approx(0.0, abs=1e-9)
    assert features.microVol10sBps == pytest.approx(0.0, abs=1e-9)
    assert features.microVol30sBps == pytest.approx(0.0, abs=1e-9)
    assert features.microRange5sBps == pytest.approx((1.01**4 - 1) * 10_000)
    assert features.microRange10sBps == pytest.approx((1.01**9 - 1) * 10_000)
    assert features.microEfficiency5s == pytest.approx(1.0)
    assert features.microEfficiency10s == pytest.approx(1.0)
    assert features.microSignFlipRate10s == pytest.approx(0.0)


def test_micro_sign_flips_on_an_alternating_path() -> None:
    state = MicroState()
    for index in range(12):
        state.ingest(index, 100.0 + (index % 2))
    features = state.features()
    assert features.microSignFlipRate10s == pytest.approx(1.0)
    assert features.microEfficiency5s == pytest.approx(0.0)


def test_micro_coverage_reports_the_seconds_that_actually_exist() -> None:
    state = MicroState()
    for index in range(3, 10):
        state.ingest(index, 100.0)
    features = state.features()
    assert features.microCoverage10s == pytest.approx(0.7)  # seven real seconds out of ten
    assert features.microCoverage30s == pytest.approx(7 / 30)
    assert rising(10).features().microCoverage10s == pytest.approx(1.0)


def test_a_missing_second_is_never_bridged() -> None:
    state = rising(10, skip={8})
    features = state.features()
    assert state.price_at(8) is None
    assert features.microReturn1sBps is None  # second 8 simply does not exist
    assert features.microReturn3sBps is not None
    assert features.microCoverage10s == pytest.approx(0.9)


def test_micro_state_rejects_late_and_non_positive_samples() -> None:
    state = MicroState()
    state.ingest(5, 100.0)
    state.ingest(4, 999.0)
    state.ingest(5, 999.0)
    state.ingest(6, 0.0)
    assert state.samples == 1
    assert list(state.history) == [(5, 100.0)]


def test_micro_state_stays_bounded_and_resets_completely() -> None:
    state = MicroState(capacity=30)
    for index in range(500):
        state.ingest(index, 100.0 + index)
    assert len(state.history) == 30
    state.reset()
    assert state.samples == 0 and not state.history
    assert state.features().microReturn1sBps is None


def test_empty_micro_state_reports_nothing_rather_than_zero() -> None:
    features = MicroState().features()
    assert features.samples == 0
    assert features.lastSecond is None
    assert features.microReturn1sBps is None
    assert features.microVol10sBps is None
    assert features.microCoverage10s is None
