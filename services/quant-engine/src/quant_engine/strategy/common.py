"""Shared primitives for regime and strategy scoring.

Three rules hold everywhere in this package.

Every mapping from a measurement to a score is bounded, monotone and named. There are no
bare magic numbers inside expressions: a threshold is a module constant with a docstring.

A missing feature is absent, not zero. ``None`` lowers the coverage of an aggregate and is
reported as such; it never contributes a neutral vote that a real measurement would have to
overcome.

Basis-point evidence is normalized by the series' own volatility before it is scored, so the
same constants mean the same thing on a five-second CapitalBear bar and a ten-minute IQ
Option bar, and on a 0.58 currency cross as much as an 8800 index.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from quant_engine.configuration import Platform
from quant_engine.features.engine import PRIMARY_TIMEFRAME, READY_BARS
from quant_engine.features.math import clamp
from quant_engine.features.models import FeatureBundle, FeatureSnapshot, MicroFeatures
from quant_engine.market_models import Timeframe
from quant_engine.strategy.models import (
    SUPPORTED_FEATURE_VERSION,
    AnalysisStatus,
    Reason,
)

EPSILON: Final = 1e-9

MIN_VOLATILITY_SCALE_BPS: Final = 1.0
"""Floor for the per-bar volatility normalizer. A perfectly flat window would otherwise turn
a rounding-sized move into an infinite number of ATRs."""

MIN_MICRO_SCALE_BPS: Final = 0.5
"""The same floor for the one-second normalizer, which is smaller by construction."""

DEGRADED_QUALITY_FACTOR: Final = 0.7
"""What a DEGRADED primary snapshot is worth. DEGRADED is the honest steady state on live
broker capture, so it reduces confidence rather than rejecting every strategy outright."""

UNMEASURED_QUALITY: Final = 0.5
"""Applied when neither goodRatio10 nor meanCoverage10 exists yet. Claiming clean inputs we
cannot see would be worse than admitting the inputs are unmeasured."""

LOW_COVERAGE_FLOOR: Final = 0.5
"""Below half the expected seconds present, the bar is a sketch of the market, not a record
of it, and the ensemble refuses to act on it."""

MIN_PRIMARY_BARS: Final = 3
"""Under three closed bars nothing in the catalog can express a relationship at all."""

MICRO_EVIDENCE_FLOOR: Final = 0.60
"""One-second evidence is admitted only when most of the interval's seconds actually exist.

Shared by the regime and by every strategy that reads the stream as supporting evidence, so
the answer to "is this stream dense enough to quote" cannot drift between them. The micro
impulse strategy applies its own, separate gate, because for it the stream is the subject
rather than a supporting witness."""


def ramp(value: float | None, zero_at: float, one_at: float) -> float | None:
    """Linear 0..1 ramp between two named points, clamped outside them.

    ``one_at`` may be below ``zero_at``, which reads a descending measurement — low
    efficiency scoring high on "range", for example — without a second function.
    """
    if value is None:
        return None
    span = one_at - zero_at
    if abs(span) < EPSILON:
        return 1.0 if value >= one_at else 0.0
    return clamp((value - zero_at) / span, 0.0, 1.0)


def signed_ramp(value: float | None, half: float) -> float | None:
    """Signed evidence in -1..1 as ``x / (|x| + half)``.

    Smooth, monotone and saturating, so a single extreme reading cannot dominate an
    aggregate. ``half`` is the magnitude that scores exactly ±0.5.
    """
    if value is None or half <= 0:
        return None
    return value / (abs(value) + half)


def oscillator(
    value: float | None, midpoint: float, span: float, deadband: float = 0.0
) -> float | None:
    """Map a bounded oscillator such as RSI or %K onto -1..1 around ``midpoint``.

    ``span`` is the distance from the midpoint that reaches full scale. A reading inside
    ``deadband`` of the midpoint is reported as no directional evidence at all rather than
    as a faint one that would still tilt an aggregate.
    """
    if value is None:
        return None
    offset = value - midpoint
    if abs(offset) <= deadband:
        return 0.0
    reduced = offset - (deadband if offset > 0 else -deadband)
    return clamp(reduced / max(span - deadband, EPSILON), -1.0, 1.0)


def breakout_sign(above: bool | None, below: bool | None) -> float | None:
    """+1 closed beyond the prior high, -1 beyond the prior low, 0 inside, None if unknown."""
    if above is None and below is None:
        return None
    if above:
        return 1.0
    if below:
        return -1.0
    return 0.0


def mean_of(values: list[float | None]) -> float | None:
    """Mean of the present values only. All absent is ``None``, never zero."""
    present = [value for value in values if value is not None]
    if not present:
        return None
    return sum(present) / len(present)


def negate(value: float | None) -> float | None:
    """Flip a signed score while keeping an absent one absent."""
    return None if value is None else -value


def aligned(sign: float, value: float | None) -> float | None:
    """How much a signed reading agrees with ``sign``, as 0..1.

    Opposition scores zero rather than a negative, because the callers grade a setup on what
    supports it; a reading that points the other way withholds support instead of subtracting
    from the evidence that does.
    """
    return None if value is None else clamp(sign * value, 0.0, 1.0)


def max_of(values: list[float | None]) -> float | None:
    """Strongest of the present values. Use where two measurements describe the same thing at
    different sensitivities and averaging them would penalise the slower one for being slow."""
    present = [value for value in values if value is not None]
    return max(present) if present else None


@dataclass(frozen=True, slots=True)
class Member:
    reason: Reason
    contribution: float


class Evidence:
    """Accumulates named, weighted, bounded observations into one signed score.

    The aggregate divides by the weight that was actually present, so an unavailable
    indicator costs coverage rather than dragging the score toward neutral.
    """

    def __init__(self) -> None:
        self._weighted = 0.0
        self._present_weight = 0.0
        self._total_weight = 0.0
        self._members: list[Member] = []

    def add(
        self,
        code: str,
        message: str,
        weight: float,
        score: float | None,
        value: float | None = None,
    ) -> None:
        self._total_weight += weight
        if score is None:
            return
        bounded = clamp(score, -1.0, 1.0)
        self._weighted += bounded * weight
        self._present_weight += weight
        self._members.append(
            Member(Reason(code=code, message=message, value=value), bounded * weight)
        )

    @property
    def score(self) -> float | None:
        if self._present_weight <= EPSILON:
            return None
        return clamp(self._weighted / self._present_weight, -1.0, 1.0)

    @property
    def coverage(self) -> float:
        if self._total_weight <= EPSILON:
            return 0.0
        return clamp(self._present_weight / self._total_weight, 0.0, 1.0)

    @property
    def present(self) -> int:
        return len(self._members)

    def reasons(self, limit: int = 4, floor: float = 0.05) -> list[Reason]:
        """The strongest contributors, not every member: an explanation, not a feature dump."""
        ranked = sorted(self._members, key=lambda member: -abs(member.contribution))
        return [member.reason for member in ranked if abs(member.contribution) >= floor][:limit]


def resolve(bundle: FeatureBundle, path: str) -> object | None:
    """Read a declared feature path such as ``primary.trend.ema9`` or ``micro.microVol10sBps``."""
    root, _, rest = path.partition(".")
    node: object | None
    if root == "primary":
        node = bundle.primary
    elif root == "micro":
        node = bundle.micro
    else:
        return None
    if not rest:
        return node
    for part in rest.split("."):
        if node is None:
            return None
        node = getattr(node, part, None)
    return node


def number(bundle: FeatureBundle, path: str) -> float | None:
    value = resolve(bundle, path)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def volatility_scale(snapshot: FeatureSnapshot) -> float | None:
    """Typical per-bar movement in basis points, used to make bps evidence dimensionless.

    ATR is preferred; realized volatility stands in while ATR is still warming. The two
    measure different things, but both answer "how far does this series usually travel in a
    bar", which is exactly what the thresholds need.
    """
    for value in (
        snapshot.volatility.atr14Bps,
        snapshot.volatility.realizedVol10Bps,
        snapshot.volatility.realizedVol20Bps,
    ):
        if value is not None and value > 0:
            return max(value, MIN_VOLATILITY_SCALE_BPS)
    return None


def micro_scale(micro: MicroFeatures) -> float | None:
    """The same normalizer for one-second evidence."""
    for value in (micro.microVol10sBps, micro.microVol5sBps, micro.microVol30sBps):
        if value is not None and value > 0:
            return max(value, MIN_MICRO_SCALE_BPS)
    return None


@dataclass(frozen=True, slots=True)
class BreakoutConfirmation:
    """How much the bar that crossed a level actually meant it.

    Conviction decides, context only scales. A wide range on its own confirms nothing: a bar
    that travelled far and closed back near its own low with almost no body is a rejection of
    the level, not a break of it, and treating expansion as a peer of the body would score
    that rejection as a breakout. So the body and the close carry the verdict, and expansion
    and path efficiency can only raise or lower what conviction already established.
    """

    body_zero: float
    body_one: float
    close_zero: float
    close_one: float
    expansion_zero: float
    expansion_one: float
    efficiency_zero: float
    efficiency_one: float
    context_floor: float

    def score(
        self,
        *,
        body_to_range: float | None,
        close_location: float | None,
        thrust: float,
        range_expansion: float | None,
        efficiency: float | None,
    ) -> float | None:
        aligned = (
            None
            if close_location is None
            else (close_location if thrust > 0 else 1.0 - close_location)
        )
        conviction = mean_of(
            [
                ramp(body_to_range, self.body_zero, self.body_one),
                ramp(aligned, self.close_zero, self.close_one),
            ]
        )
        if conviction is None:
            return None
        context = mean_of(
            [
                ramp(range_expansion, self.expansion_zero, self.expansion_one),
                ramp(efficiency, self.efficiency_zero, self.efficiency_one),
            ]
        )
        scale = 1.0 if context is None else self.context_floor + (1 - self.context_floor) * context
        return clamp(conviction * scale, 0.0, 1.0)


@dataclass(frozen=True, slots=True)
class Gate:
    """Whether a bundle can support analysis at all, and what its inputs are worth."""

    usable: bool
    status: AnalysisStatus
    qualityFit: float
    vetoes: tuple[Reason, ...] = field(default=())


def _identity_matches(bundle: FeatureBundle, snapshot: FeatureSnapshot) -> bool:
    return (
        snapshot.platform == bundle.platform
        and snapshot.slotId == bundle.slotId
        and snapshot.assetName == bundle.assetName
        and snapshot.contextId == bundle.contextId
    )


def _quality_fit(primary: FeatureSnapshot) -> float:
    clean = mean_of([primary.quality.goodRatio10, primary.quality.meanCoverage10])
    maturity = clamp(primary.barCount / READY_BARS, 0.0, 1.0)
    status_factor = DEGRADED_QUALITY_FACTOR if primary.status == "DEGRADED" else 1.0
    return clamp(
        (UNMEASURED_QUALITY if clean is None else clean) * maturity * status_factor, 0.0, 1.0
    )


def market_gate(bundle: FeatureBundle) -> Gate:
    """Hard preconditions first, then what the surviving inputs are worth.

    A failure here is fatal for the whole panel: no strategy may vote on a bundle whose
    version, identity or chronology cannot be trusted. Warming and degraded inputs are not
    failures — they reduce ``qualityFit`` and let each strategy decide for itself.
    """
    vetoes: list[Reason] = []

    def reject(code: str, message: str, value: float | None = None) -> Gate:
        vetoes.append(Reason(code=code, message=message, value=value))
        return Gate(usable=False, status="INVALID", qualityFit=0.0, vetoes=tuple(vetoes))

    if bundle.featureVersion != SUPPORTED_FEATURE_VERSION:
        return reject(
            "UNSUPPORTED_FEATURE_VERSION",
            f"Bundle is {bundle.featureVersion}; strategies require {SUPPORTED_FEATURE_VERSION}",
        )
    primary = bundle.primary
    if primary is None:
        return reject("MISSING_PRIMARY_FEATURES", "No primary timeframe snapshot in the bundle")
    if primary.featureVersion != SUPPORTED_FEATURE_VERSION:
        return reject(
            "UNSUPPORTED_FEATURE_VERSION",
            f"Primary snapshot is {primary.featureVersion}, not {SUPPORTED_FEATURE_VERSION}",
        )
    if primary.status == "INVALID":
        return reject("INVALID_PRIMARY_FEATURES", "Primary snapshot is INVALID")
    if bundle.primaryTimeframe != PRIMARY_TIMEFRAME[bundle.platform]:
        return reject(
            "PRIMARY_TIMEFRAME_MISMATCH",
            f"{bundle.platform} decides on {PRIMARY_TIMEFRAME[bundle.platform]},"
            f" not {bundle.primaryTimeframe}",
        )
    if primary.timeframe != bundle.primaryTimeframe or not _identity_matches(bundle, primary):
        return reject("CONTEXT_CHANGED", "Primary snapshot belongs to a different series")
    for name, snapshot in bundle.contexts.items():
        if not _identity_matches(bundle, snapshot) or snapshot.timeframe != name:
            return reject("CONTEXT_CHANGED", f"{name} context belongs to a different series")
        if snapshot.featureTime > bundle.asOf:
            return reject(
                "CHRONOLOGY_INVALID",
                f"{name} context closes after the bundle as-of time",
                float(snapshot.featureTime - bundle.asOf),
            )
    if primary.featureTime > bundle.asOf:
        return reject(
            "CHRONOLOGY_INVALID",
            "Primary snapshot closes after the bundle as-of time",
            float(primary.featureTime - bundle.asOf),
        )
    if primary.barCount < MIN_PRIMARY_BARS:
        return reject(
            "INSUFFICIENT_DATA",
            f"Only {primary.barCount} closed bars on the primary timeframe",
            float(primary.barCount),
        )

    coverage = primary.quality.meanCoverage10
    if coverage is not None and coverage < LOW_COVERAGE_FLOOR:
        vetoes.append(
            Reason(
                code="LOW_COVERAGE",
                message=f"Mean ten-bar coverage {coverage:.2f} below {LOW_COVERAGE_FLOOR:.2f}",
                value=coverage,
            )
        )
    status: AnalysisStatus = "OK" if primary.status == "READY" else primary.status
    return Gate(usable=True, status=status, qualityFit=_quality_fit(primary), vetoes=tuple(vetoes))


@dataclass(frozen=True, slots=True)
class Analysis:
    """Everything a strategy is allowed to read, resolved once per evaluation.

    Contexts come only from the bundle, which Phase 6 already joined as-of the primary close.
    Nothing here can reach past that boundary to fetch a fresher timeframe.
    """

    bundle: FeatureBundle
    primary: FeatureSnapshot
    micro: MicroFeatures
    qualityFit: float
    volScale: float | None
    microScale: float | None

    @property
    def platform(self) -> Platform:
        return self.bundle.platform

    def context(self, timeframe: Timeframe) -> FeatureSnapshot | None:
        return self.bundle.contexts.get(timeframe)

    def norm(self, value_bps: float | None) -> float | None:
        """A basis-point measurement expressed in multiples of a typical bar's movement."""
        if value_bps is None or self.volScale is None:
            return None
        return value_bps / self.volScale

    def micro_norm(self, value_bps: float | None) -> float | None:
        if value_bps is None or self.microScale is None:
            return None
        return value_bps / self.microScale


def analysis(bundle: FeatureBundle, primary: FeatureSnapshot, quality_fit: float) -> Analysis:
    return Analysis(
        bundle=bundle,
        primary=primary,
        micro=bundle.micro,
        qualityFit=quality_fit,
        volScale=volatility_scale(primary),
        microScale=micro_scale(bundle.micro),
    )


CONTEXT_STACK_HALF: Final = 0.60
"""Higher-timeframe EMA separation, in that timeframe's own bar movements, scoring ±0.5."""
CONTEXT_SLOPE_HALF: Final = 0.30
"""Higher-timeframe per-bar slope, in its own bar movements, scoring ±0.5."""


def context_direction(snapshot: FeatureSnapshot) -> float | None:
    """A compact signed read of one higher-timeframe snapshot, in -1..1.

    Used as weighted agreement, never as a gate: a strategy is not required to find perfect
    alignment across timeframes, only to be told how much the wider structure concurs. The
    snapshot is normalized by its own volatility, so an M10 read is comparable with an M1 one.
    """
    if snapshot.status == "INVALID":
        return None
    scale = volatility_scale(snapshot)
    if scale is None:
        return None
    trend = snapshot.trend
    return mean_of(
        [
            signed_ramp(
                None if trend.ema9To20Bps is None else trend.ema9To20Bps / scale,
                CONTEXT_STACK_HALF,
            ),
            signed_ramp(
                None if trend.priceToEma20Bps is None else trend.priceToEma20Bps / scale,
                CONTEXT_STACK_HALF,
            ),
            signed_ramp(
                None if trend.priceSlope10 is None else trend.priceSlope10 / scale,
                CONTEXT_SLOPE_HALF,
            ),
        ]
    )
