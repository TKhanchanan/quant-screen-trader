"""Deterministic FeatureBundle fixtures for the strategy tests.

Every bundle here is produced by running real candles through the real Phase 6 engine.
Nothing constructs a FeatureSnapshot by hand: a strategy proved correct against a
hand-written feature set would not have been proved against the engine it actually consumes,
and a Phase 6 formula change has to be able to break these tests.

The shapes are chosen so that the *inputs* are unambiguous — a monotone advance really is a
trend, an alternating series really is noise — rather than tuned until an assertion passes.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from features_fixtures import CONTEXT_A, candle, second
from quant_engine.configuration import Platform
from quant_engine.features import FeatureBundle, FeatureEngine
from quant_engine.features.engine import PRIMARY_TIMEFRAME
from quant_engine.market_models import Candle, QualityState, Timeframe
from quant_engine.strategy.models import EnsembleSnapshot, StrategyEvaluation

ASSET = "EUR/USD OTC"
TREND_BARS = 80
"""Past the fifty-bar READY floor, so a trending fixture is never merely WARMING."""


def bars(
    closes: Sequence[float],
    *,
    spread: float = 0.0004,
    timeframe: Timeframe = "M1",
    platform: Platform = "iqoption",
    slot: int = 1,
    asset: str = ASSET,
    context: object = CONTEXT_A,
    highs: Sequence[float] | None = None,
    lows: Sequence[float] | None = None,
    quality: QualityState = "GOOD",
    coverage: float = 1.0,
) -> list[Candle]:
    """Candles whose open is the previous close, with a symmetric range around each bar."""
    built: list[Candle] = []
    previous: float | None = None
    for index, close in enumerate(closes):
        open_ = previous if previous is not None else close
        high = highs[index] if highs is not None else max(open_, close) * (1 + spread)
        low = lows[index] if lows is not None else min(open_, close) * (1 - spread)
        built.append(
            candle(
                index,
                open_,
                high,
                low,
                close,
                timeframe=timeframe,
                platform=platform,
                slot=slot,
                asset=asset,
                context=context,  # type: ignore[arg-type]
                quality=quality,
                coverage=coverage,
            )
        )
        previous = close
    return built


def feed(engine: FeatureEngine, candles: Sequence[Candle]) -> None:
    for item in candles:
        engine.ingest_candle(item)


def feed_seconds(
    engine: FeatureEngine,
    samples: Sequence[tuple[int, float]],
    *,
    platform: Platform = "capitalbear",
    slot: int = 1,
    asset: str = ASSET,
    context: object = CONTEXT_A,
) -> None:
    """Seconds are addressed by their own epoch second, so a fixture can leave one out."""
    for stamp, price in samples:
        engine.ingest_second(
            second(
                stamp,
                price,
                platform=platform,
                slot=slot,
                asset=asset,
                context=context,  # type: ignore[arg-type]
            )
        )


def bundle(
    closes: Sequence[float],
    *,
    platform: Platform = "iqoption",
    timeframe: Timeframe | None = None,
    slot: int = 1,
    asset: str = ASSET,
    engine: FeatureEngine | None = None,
    **shape: object,
) -> FeatureBundle:
    """One FeatureBundle for one series, joined by the feature engine exactly as it is live."""
    active = FeatureEngine() if engine is None else engine
    feed(
        active,
        bars(
            closes,
            timeframe=timeframe or PRIMARY_TIMEFRAME[platform],
            platform=platform,
            slot=slot,
            asset=asset,
            **shape,  # type: ignore[arg-type]
        ),
    )
    built = active.latest_bundle(platform, slot)
    assert built is not None, "the fixture produced no bundle"
    return built


def trending(count: int = TREND_BARS, drift: float = 0.0015, start: float = 100.0) -> list[float]:
    """A coherent advance: every bar closes above the last, so efficiency stays near one."""
    return [start * (1 + drift * index) for index in range(count)]


def falling(count: int = TREND_BARS, drift: float = 0.0015, start: float = 100.0) -> list[float]:
    return [start * (1 - drift * index) for index in range(count)]


def ranging(count: int = TREND_BARS, amplitude: float = 0.0015) -> list[float]:
    """A six-bar oscillation with no net displacement: high overlap, low efficiency."""
    return [100.0 * (1 + amplitude * math.sin(index * math.pi / 3)) for index in range(count)]


def noisy(count: int = TREND_BARS, amplitude: float = 0.0020) -> list[float]:
    """Alternating returns: the sign changes on every single bar."""
    return [100.0 * (1 + amplitude * ((-1) ** index)) for index in range(count)]


def quiet_band(
    count: int = 60, width: float = 0.02
) -> tuple[list[float], list[float], list[float]]:
    """A narrow range with a real bar range, ready for a level to be broken out of."""
    closes = [100.0 + width * math.sin(index * math.pi / 4) for index in range(count)]
    return closes, [close + 0.03 for close in closes], [close - 0.03 for close in closes]


def breakout_up() -> tuple[list[float], list[float], list[float]]:
    """The band, then one decisive bar: wide range, full body, close at its own high."""
    closes, highs, lows = quiet_band()
    return [*closes, 100.90], [*highs, 100.95], [*lows, 99.97]


def touch_only() -> tuple[list[float], list[float], list[float]]:
    """The same reach beyond the band, rejected: the close comes back inside it."""
    closes, highs, lows = quiet_band()
    return [*closes, 100.02], [*highs, 100.90], [*lows, 99.97]


RANGE_STRETCH = 0.0032
"""One bar's excursion beyond the band the range has been holding, as a share of price."""


def range_stretched_low(count: int = 83) -> list[float]:
    """A range whose last bar overshoots below the band it has been holding.

    The bar count places the oscillation at its low quarter first, so the excursion extends a
    move already under way rather than reversing one.
    """
    return [*ranging(count=count), 100.0 * (1 - RANGE_STRETCH)]


def range_stretched_high(count: int = 80) -> list[float]:
    """The mirror: the same range overshooting above its band."""
    return [*ranging(count=count), 100.0 * (1 + RANGE_STRETCH)]


def pullback(drift: float = 0.0015, dip_bars: int = 3, dip_rate: float = 0.0016) -> list[float]:
    """A long advance, a short retracement against it, then one bar turning back up."""
    closes = trending(count=TREND_BARS, drift=drift)
    price = closes[-1]
    for _ in range(dip_bars):
        price *= 1 - dip_rate
        closes.append(price)
    closes.append(price * (1 + dip_rate * 0.4))
    return closes


def rising_seconds(
    count: int = 40, rate: float = 0.00025, start: float = 100.0
) -> list[tuple[int, float]]:
    """A clean one-second advance with no missing seconds."""
    return [(index, start * (1 + rate * index)) for index in range(count)]


def alternating_seconds(
    count: int = 40, rate: float = 0.0004, start: float = 100.0
) -> list[tuple[int, float]]:
    """Every second reverses the last: high one-second volatility, no direction."""
    return [(index, start * (1 + rate * ((-1) ** index))) for index in range(count)]


def sparse_seconds(start: float = 100.0, rate: float = 0.00025) -> list[tuple[int, float]]:
    """A stream with holes: half of the last ten seconds never arrived.

    The five-second velocity is still computable, so this exercises the coverage gate itself
    rather than the missing-feature gate that a completely empty stream would hit first.
    """
    stamps = [0, 1, 2, 3, 4, 5, 10, 12, 13, 14, 15]
    return [(stamp, start * (1 + rate * stamp)) for stamp in stamps]


def by_id(snapshot: EnsembleSnapshot, strategy_id: str) -> StrategyEvaluation:
    return next(item for item in snapshot.strategies if item.strategyId == strategy_id)


def veto_codes(item: EnsembleSnapshot | StrategyEvaluation) -> set[str]:
    return {veto.code for veto in item.vetoes}
