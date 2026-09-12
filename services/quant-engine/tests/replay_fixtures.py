"""Deterministic synthetic market history for the replay tests.

Phase 11's input contract *is* a stored ``MarketObservation``, so building one here is reading
the contract rather than bypassing a layer. Everything downstream of it — Phase 5 acceptance,
the builder, the features, the regime, the ensemble, the ranking and the paper outcome — is the
real production code driven by these rows.

The prices are constructed rather than sampled because the properties a replay has to be proved
against cannot be steered out of a live capture on demand: a trend that really trends, a range
that really ranges, a break that really breaks, a gap that is really missing, an asset that
really changes inside one slot, and a future move large enough that a lookahead bug could not
hide. Nothing here is random: every value is a pure function of its index, so the same fixture
is the same history on every machine and every run.

**These are not market data.** A result measured on this fixture is a statement about the
software, never about any market, and every replay of it carries ``SYNTHETIC_BEHAVIOR_TEST``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from quant_engine.configuration import Platform
from quant_engine.market_models import DataQuality, MarketObservation, QualityState

BASE_MS = int(datetime(2026, 3, 2, 6, 0, tzinfo=UTC).timestamp() * 1000)
"""A Monday at 06:00 UTC, so the weekday and hour tables have a known answer."""

DAY_MS = 86_400_000
PARSE_LAG_MS = 50

type Shape = Literal["TREND_UP", "TREND_DOWN", "RANGE", "BREAKOUT", "NOISY", "VOLATILE", "FLAT"]

CAPITALBEAR_ASSETS = ("EUR/USD OTC", "GBP/JPY OTC", "Gold OTC")
IQOPTION_ASSETS = ("EUR/USD", "AUD/JPY", "Apple Inc.")


def identity(label: str) -> UUID:
    """A stable id for a fixture row. uuid5 so the fingerprint is the same on every machine."""
    return uuid5(NAMESPACE_URL, f"qst-replay-fixture/{label}")


MASK64 = (1 << 64) - 1
GOLDEN = 0x9E3779B97F4A7C15


def wobble(index: int) -> float:
    """Deterministic pseudo-noise in [-0.5, 0.5]. No RNG, and no structure between neighbours.

    A SplitMix64 finaliser rather than the obvious ``index * constant % modulus``. That cheaper
    form is a linear congruential sequence: consecutive values differ by a constant, so the
    "noise" is a sawtooth and a five-second move is exactly predictable from the previous one.
    A fixture like that makes every selection a winner and proves nothing — the mixing here
    decorrelates neighbours while staying a pure function of the index on every machine.
    """
    value = (index + GOLDEN) & MASK64
    value ^= value >> 30
    value = (value * 0xBF58476D1CE4E5B9) & MASK64
    value ^= value >> 27
    value = (value * 0x94D049BB133111EB) & MASK64
    value ^= value >> 31
    return (value % 1_000_003) / 1_000_003 - 0.5


DRIFT = 0.07
NOISE = 2.0
REVERSION = 0.06


def path(shape: Shape, count: int, base: float, scale: float) -> list[float]:
    """One price series as a *cumulative* walk, not as a closed form of its index.

    This is the difference between a fixture that tests something and one that does not. A
    closed-form shape — a sine, or a ramp plus bounded noise — is smooth on the horizon the
    paper layer measures, so every selection wins, the baseline win rate is ninety-nine per cent
    and no threshold can beat a baseline like that. The regime tables look right and the whole
    research half of the layer is never exercised.

    A walk behaves the way a price does: the level is the sum of everything that happened, a
    trend is a drift buried in noise that can and does reverse, and a five-second outcome is
    genuinely uncertain. The drift is sized so a trending stretch is right roughly six times in
    ten on CapitalBear's horizon — an edge worth measuring, and nowhere near a certainty.

    Deterministic all the same: the walk is a fold over ``wobble``, which is a pure function of
    the index, so the same fixture is the same series on every machine.
    """
    level = 0.0
    values: list[float] = []
    for index in range(count):
        noise = wobble(index) * NOISE
        if shape == "TREND_UP":
            level += DRIFT + noise
        elif shape == "TREND_DOWN":
            level += -DRIFT + noise
        elif shape == "RANGE":
            level += -REVERSION * level + noise
        elif shape == "BREAKOUT":
            level += (-REVERSION * level + noise) if index < count * 0.6 else (DRIFT * 3 + noise)
        elif shape == "NOISY":
            level += noise * 1.4
        elif shape == "VOLATILE":
            level += noise * (0.5 if index < count * 0.5 else 3.0)
        else:
            level += noise * 0.02
        values.append(base + scale * level)
    return values


def quality_block(state: QualityState = "GOOD") -> DataQuality:
    clean = state in ("GOOD", "DEGRADED")
    return DataQuality(
        state=state,
        confidence=0.99 if clean else 0.2,
        freshness=1.0,
        completeness=1.0,
        sourceReliability=0.99,
        latencyMs=40.0,
    )


def observation(
    *,
    label: str,
    platform: Platform,
    slot: int,
    asset: str,
    context: UUID,
    timestamp: int,
    price: float | None,
    quality: QualityState = "GOOD",
    parser_confidence: float = 0.95,
    source: str = "VISUAL",
) -> MarketObservation:
    return MarketObservation(
        id=identity(label),
        platform=platform,
        slotId=slot,
        assetName=asset,
        contextId=context,
        observedAt=datetime.fromtimestamp(timestamp / 1000, UTC),
        parsedAt=datetime.fromtimestamp((timestamp + PARSE_LAG_MS) / 1000, UTC),
        sourceType=source,  # type: ignore[arg-type]
        price=price,
        payout=0.82,
        timerSeconds=30,
        parserConfidence=parser_confidence,
        dataQuality=quality_block(quality),
        captureLatencyMs=20.0,
        parseLatencyMs=30.0,
        calibrationProfileId=identity("calibration"),
        parserVersion="fixture-1",
    )


def series(
    *,
    platform: Platform,
    slot: int,
    asset: str,
    context: UUID,
    start: int,
    seconds: int,
    shape: Shape,
    base: float,
    scale: float = 0.0001,
    step_ms: int = 1_000,
    offset_ms: int = 0,
    gap: tuple[int, int] | None = None,
    tag: str = "",
) -> list[MarketObservation]:
    """One contiguous run of one asset in one slot, optionally with a hole in the middle.

    A ``gap`` removes whole seconds from the record and puts nothing in their place: the replay
    has to carry the hole through Phase 5's own coverage and quality logic exactly as a live
    capture outage would, rather than being handed an interpolated price it never saw.
    """
    rows: list[MarketObservation] = []
    prices = path(shape, seconds, base, scale)
    for index in range(seconds):
        if gap is not None and gap[0] <= index < gap[1]:
            continue
        timestamp = start + index * step_ms + offset_ms
        rows.append(
            observation(
                label=f"{tag}/{platform}/{slot}/{asset}/{index}",
                platform=platform,
                slot=slot,
                asset=asset,
                context=context,
                timestamp=timestamp,
                price=round(prices[index], 6),
            )
        )
    return rows


SESSIONS: tuple[tuple[int, int, Shape, Shape, Shape], ...] = (
    (0, 0, "TREND_UP", "RANGE", "NOISY"),
    (1, 3, "RANGE", "BREAKOUT", "TREND_DOWN"),
    (2, 6, "BREAKOUT", "VOLATILE", "RANGE"),
    (4, 9, "TREND_DOWN", "NOISY", "TREND_UP"),
    (5, 12, "VOLATILE", "TREND_UP", "BREAKOUT"),
    (7, 15, "NOISY", "RANGE", "VOLATILE"),
)
"""Six sessions across nine calendar days, each starting at a different hour.

Two absences are deliberate. Weekends are missing, the way they are missing from a real record.
And no two sessions share an hour of the day, so the hour and weekday coverage counters have
something to count — a fixture that ran six times at the same hour would let a replay report
six dates of history that were really one time of day repeated."""

CAPITALBEAR_SECONDS = 600
IQOPTION_SECONDS = 4_800
"""IQ Option decides on a one-minute close and ``qfe-v2`` reports WARMING below fifty bars, so a
session has to run past fifty minutes before a single IQ decision can be evaluated at all. Eighty
minutes leaves roughly thirty evaluated closes, which is the smallest honest fixture that can
exercise the sixty-second horizon rather than only the five-second one."""

BASES = (1.0850, 188.40, 2410.0)


def capitalbear_history() -> list[MarketObservation]:
    """Six sessions of three CapitalBear slots at one sample a second.

    Fifteen minutes is a hundred and eighty S5 bars, so a slot matures past ``qfe-v2``'s fifty-bar
    floor inside each session and the rest of the session is evaluated with warm indicators.
    """
    rows: list[MarketObservation] = []
    for day, hour, *shapes in SESSIONS:
        start = BASE_MS + day * DAY_MS + hour * 3_600_000
        for index, shape in enumerate(shapes):
            slot = index + 1
            context = identity(f"cb/{day}/{slot}")
            rows.extend(
                series(
                    platform="capitalbear",
                    slot=slot,
                    asset=CAPITALBEAR_ASSETS[index],
                    context=context,
                    start=start,
                    seconds=CAPITALBEAR_SECONDS,
                    shape=shape,
                    base=BASES[index],
                    scale=BASES[index] * 0.0004,
                    offset_ms=index * 40,
                    gap=(430, 455) if index == 2 else None,
                    tag=f"cb{day}",
                )
            )
    return rows


def iqoption_history() -> list[MarketObservation]:
    """Two shorter IQ Option sessions, including one asset change inside a live slot.

    The slot keeps its number and gets a new asset and a new context, which is exactly what
    happens when an operator retunes a chart. Nothing from the first asset may survive into the
    second, and the replay has to show that through the same reset chain the live engine uses.
    """
    rows: list[MarketObservation] = []
    for order, (day, hour, *shapes) in enumerate(SESSIONS[:1]):
        start = BASE_MS + day * DAY_MS + (hour + 2) * 3_600_000
        for index, shape in enumerate(shapes):
            slot = index + 1
            asset = IQOPTION_ASSETS[index]
            context = identity(f"iq/{day}/{slot}")
            rows.extend(
                series(
                    platform="iqoption",
                    slot=slot,
                    asset=asset,
                    context=context,
                    start=start,
                    seconds=IQOPTION_SECONDS,
                    shape=shape,
                    base=BASES[index],
                    scale=BASES[index] * 0.0006,
                    offset_ms=index * 60,
                    tag=f"iq{day}",
                )
            )
        if order == 0:
            # The same physical slot, a different market. New asset, new context, no shared state.
            rows.extend(
                series(
                    platform="iqoption",
                    slot=1,
                    asset="USD/CHF",
                    context=identity(f"iq/{day}/1/switched"),
                    start=start + IQOPTION_SECONDS * 1_000 + 5_000,
                    seconds=300,
                    shape="TREND_DOWN",
                    base=0.9120,
                    scale=0.0004,
                    tag=f"iqswitch{day}",
                )
            )
    return rows


def synthetic_history() -> list[MarketObservation]:
    """The whole long fixture: both platforms, six dates, every shape, a gap and a slot reuse."""
    return [*capitalbear_history(), *iqoption_history()]


def session(
    *,
    platform: Platform = "capitalbear",
    shapes: tuple[Shape, ...] = ("TREND_UP", "RANGE", "TREND_DOWN"),
    start: int = BASE_MS,
    seconds: int = 420,
    step_ms: int = 1_000,
    tag: str = "session",
    gap: tuple[int, int] | None = None,
) -> list[MarketObservation]:
    """One short cohort: several slots of one platform running together over one stretch.

    The fast fixture. Most replay tests are about ordering, windows, causality and isolation
    rather than about how much history there is, and a test that spends four seconds replaying a
    day to assert a timestamp is a test nobody runs.
    """
    assets = CAPITALBEAR_ASSETS if platform == "capitalbear" else IQOPTION_ASSETS
    rows: list[MarketObservation] = []
    for index, shape in enumerate(shapes):
        rows.extend(
            series(
                platform=platform,
                slot=index + 1,
                asset=assets[index],
                context=identity(f"{tag}/{platform}/{index + 1}"),
                start=start,
                seconds=seconds,
                shape=shape,
                base=BASES[index],
                scale=BASES[index] * 0.0004,
                step_ms=step_ms,
                offset_ms=index * 40,
                gap=gap if index == 0 else None,
                tag=tag,
            )
        )
    return rows


def small_history() -> list[MarketObservation]:
    """One CapitalBear cohort long enough to mature, decide and resolve. Seconds to replay."""
    return session(seconds=420, tag="small")
