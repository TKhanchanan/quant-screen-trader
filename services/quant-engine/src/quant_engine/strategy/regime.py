"""Deterministic market regime classification from Phase 6 facts.

Nothing here recomputes an indicator. Every score is a bounded, weighted reading of features
the feature engine already produced, normalized by the series' own volatility so that one
set of constants describes a five-second bar and a ten-minute bar alike.

The output is a primary label plus every supporting score, never a single label pretending
the evidence was unambiguous. When two readings are both strong the label still has to pick
one, and the confidence it reports falls accordingly.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from quant_engine.features.math import clamp
from quant_engine.features.models import FeatureBundle
from quant_engine.strategy.common import (
    EPSILON,
    MICRO_EVIDENCE_FLOOR,
    Analysis,
    BreakoutConfirmation,
    Evidence,
    Gate,
    analysis,
    breakout_sign,
    market_gate,
    mean_of,
    oscillator,
    ramp,
    signed_ramp,
)
from quant_engine.strategy.models import (
    REGIME_VERSION,
    SUPPORTED_FEATURE_VERSION,
    Direction,
    Reason,
    Regime,
    RegimeSnapshot,
)
from quant_engine.strategy.policy import PlatformPolicy, policy_for

# --- Trend -----------------------------------------------------------------------------
TREND_STRETCH_HALF: Final = 0.90
"""Distance of price from an EMA, in typical bar movements, that scores ±0.5."""
TREND_STACK_HALF: Final = 0.50
"""Separation between two EMAs, in typical bar movements, that scores ±0.5."""
TREND_SLOPE_HALF: Final = 0.22
"""Per-bar slope, in typical bar movements per bar, that scores ±0.5."""
TREND_RSI_SPAN: Final = 25.0
TREND_RSI_DEADBAND: Final = 4.0
"""RSI within four points of fifty is not directional evidence in either direction."""
TREND_COHERENCE_ZERO: Final = 0.18
TREND_COHERENCE_ONE: Final = 0.62
"""Efficiency ratio mapped onto a coherence gain. A stacked, sloping set of EMAs reached by
a path that wandered is a weaker trend than the same stack reached in a straight line."""
TREND_COHERENCE_FLOOR: Final = 0.35
"""How much of the directional evidence survives a completely incoherent path."""

# --- Range -----------------------------------------------------------------------------
RANGE_EFFICIENCY_ZERO: Final = 0.55
RANGE_EFFICIENCY_ONE: Final = 0.12
RANGE_CHOP_ZERO: Final = 38.2
RANGE_CHOP_ONE: Final = 61.8
"""The conventional choppiness index boundaries, kept because they are widely understood."""
RANGE_OVERLAP_ZERO: Final = 0.30
RANGE_OVERLAP_ONE: Final = 0.80
RANGE_STACK_WIDE: Final = 0.60
RANGE_STACK_TIGHT: Final = 0.08
RANGE_SLOPE_STEEP: Final = 0.35
RANGE_SLOPE_FLAT: Final = 0.04

# --- Breakout --------------------------------------------------------------------------
BREAKOUT_WINDOW_WEIGHTS: Final = ((20, 1.4), (10, 1.0), (5, 0.6))
"""A close beyond a twenty-bar extreme says more than one beyond a five-bar extreme."""
BREAKOUT_BB_Z_HALF: Final = 1.6
BREAKOUT_MICRO_HALF: Final = 2.0
BREAKOUT_CONFIRMATION: Final = BreakoutConfirmation(
    body_zero=0.35,
    body_one=0.75,
    close_zero=0.55,
    close_one=0.90,
    expansion_zero=0.95,
    expansion_one=1.90,
    efficiency_zero=0.25,
    efficiency_one=0.65,
    context_floor=0.40,
)
BREAKOUT_CONFIRMATION_UNKNOWN: Final = 0.35
"""What an unconfirmable level cross is worth. A bar that closed beyond a prior extreme with
no measurable body or close location behind it is a fact, not yet a breakout."""

# --- Noise -----------------------------------------------------------------------------
NOISE_FLIP_ZERO: Final = 0.35
NOISE_FLIP_ONE: Final = 0.80
NOISE_EFFICIENCY_ZERO: Final = 0.55
NOISE_EFFICIENCY_ONE: Final = 0.12
NOISE_OVERLAP_ZERO: Final = 0.45
NOISE_OVERLAP_ONE: Final = 0.92
NOISE_CHOP_ZERO: Final = 45.0
NOISE_CHOP_ONE: Final = 72.0
NOISE_MICRO_FLIP_ZERO: Final = 0.40
NOISE_MICRO_FLIP_ONE: Final = 0.85
NOISE_QUALITY_ZERO: Final = 0.95
NOISE_QUALITY_ONE: Final = 0.55

# --- Volatility state ------------------------------------------------------------------
VOL_EXPANSION_RANGE_ZERO: Final = 1.00
VOL_EXPANSION_RANGE_ONE: Final = 2.10
VOL_EXPANSION_RATIO_ZERO: Final = 1.00
VOL_EXPANSION_RATIO_ONE: Final = 1.70
VOL_EXPANSION_BAND_ZERO: Final = 1.60
VOL_EXPANSION_BAND_ONE: Final = 3.20
VOL_COMPRESSION_RANGE_ZERO: Final = 0.95
VOL_COMPRESSION_RANGE_ONE: Final = 0.35
VOL_COMPRESSION_RATIO_ZERO: Final = 1.00
VOL_COMPRESSION_RATIO_ONE: Final = 0.55
VOL_COMPRESSION_BAND_ZERO: Final = 1.50
VOL_COMPRESSION_BAND_ONE: Final = 0.70

# --- Label selection -------------------------------------------------------------------
NOISY_FLOOR: Final = 0.62
BREAKOUT_FLOOR: Final = 0.34
TREND_FLOOR: Final = 0.32
RANGE_FLOOR: Final = 0.52
VOLATILITY_FLOOR: Final = 0.60
"""Minimum score for a label to be claimed at all. Below every floor the regime is
UNCERTAIN, which is a real answer and not a failure."""

REGIME_CONFLICT_WEIGHT: Final = 0.70
"""Most of the confidence a fully contested label can lose.

Confidence is scaled by the share of the evidence that contradicts the winning label, so an
uncontradicted label keeps all of its strength and a perfectly tied one keeps 65% of it. A
contested reading is weaker, not absent, so this cannot drive confidence to zero on its own;
the claiming floors and the quality fit are what do that."""
UNCERTAIN_CONFIDENCE_FACTOR: Final = 0.60
"""UNCERTAIN earned no label, so its confidence is discounted even when scores are middling."""
REGIME_BIAS_TREND_WEIGHT: Final = 1.0
REGIME_BIAS_BREAKOUT_WEIGHT: Final = 0.8
REGIME_BIAS_FLOOR: Final = 0.25
"""Blended trend and breakout evidence needed before the regime reports a directional bias."""

MAX_REGIME_REASONS: Final = 8


def _trend_score(state: Analysis, policy: PlatformPolicy) -> tuple[float | None, Evidence]:
    """Signed trend evidence, weighted toward the persistent structure.

    The regime describes the environment a strategy will be operating in, so the slow members
    carry the most weight and the last two bars carry the least. A three-bar dip inside an
    eighty-bar advance is a pullback within a trend, not the end of one, and a regime that
    flips on it would tell every strategy the wrong thing about where it is. Reading those
    fast members is the strategies' own job.
    """
    trend, price = state.primary.trend, state.primary.priceAction
    evidence = Evidence()
    evidence.add(
        "EMA_STACK_5_9",
        "EMA5 against EMA9",
        0.7,
        signed_ramp(state.norm(trend.ema5To9Bps), TREND_STACK_HALF),
        trend.ema5To9Bps,
    )
    evidence.add(
        "EMA_STACK_9_20",
        "EMA9 against EMA20",
        1.2,
        signed_ramp(state.norm(trend.ema9To20Bps), TREND_STACK_HALF),
        trend.ema9To20Bps,
    )
    evidence.add(
        "EMA_STACK_20_50",
        "EMA20 against EMA50",
        1.2,
        signed_ramp(state.norm(trend.ema20To50Bps), TREND_STACK_HALF),
        trend.ema20To50Bps,
    )
    evidence.add(
        "PRICE_TO_EMA9",
        "Price against EMA9",
        0.6,
        signed_ramp(state.norm(trend.priceToEma9Bps), TREND_STRETCH_HALF),
        trend.priceToEma9Bps,
    )
    evidence.add(
        "PRICE_TO_EMA20",
        "Price against EMA20",
        1.0,
        signed_ramp(state.norm(trend.priceToEma20Bps), TREND_STRETCH_HALF),
        trend.priceToEma20Bps,
    )
    for code, label, weight, value in (
        ("EMA5_SLOPE", "EMA5 slope", 0.4, trend.ema5Slope3),
        ("EMA9_SLOPE", "EMA9 slope", 0.9, trend.ema9Slope3),
        ("EMA20_SLOPE", "EMA20 slope", 1.2, trend.ema20Slope3),
        ("PRICE_SLOPE_5", "Five-bar price slope", 0.4, trend.priceSlope5),
        ("PRICE_SLOPE_10", "Ten-bar price slope", 1.0, trend.priceSlope10),
        ("PRICE_SLOPE_20", "Twenty-bar price slope", 1.2, trend.priceSlope20),
    ):
        evidence.add(code, label, weight, signed_ramp(state.norm(value), TREND_SLOPE_HALF), value)
    evidence.add(
        "RSI_PRESSURE",
        "RSI14 away from fifty",
        0.5,
        oscillator(state.primary.momentum.rsi14, 50.0, TREND_RSI_SPAN, TREND_RSI_DEADBAND),
        state.primary.momentum.rsi14,
    )
    evidence.add(
        "CLOSE_LOCATION",
        "Close within the bar range",
        0.25,
        None if price.closeLocation is None else (price.closeLocation - 0.5) * 2.0,
        price.closeLocation,
    )
    micro = state.micro
    if (micro.microCoverage10s or 0.0) >= MICRO_EVIDENCE_FLOOR:
        evidence.add(
            "MICRO_VELOCITY",
            "One-second velocity",
            0.5 * policy.microWeight,
            signed_ramp(state.micro_norm(micro.microVelocity5s), TREND_SLOPE_HALF),
            micro.microVelocity5s,
        )
    raw = evidence.score
    if raw is None:
        return None, evidence
    noise = state.primary.noise
    coherence = mean_of(
        [
            ramp(noise.efficiencyRatio10, TREND_COHERENCE_ZERO, TREND_COHERENCE_ONE),
            ramp(noise.efficiencyRatio20, TREND_COHERENCE_ZERO, TREND_COHERENCE_ONE),
        ]
    )
    # An unmeasurable path cannot be evidence against the trend either; the warm-up it
    # implies is already discounted through qualityFit.
    gain = (
        1.0
        if coherence is None
        else TREND_COHERENCE_FLOOR + (1 - TREND_COHERENCE_FLOOR) * coherence
    )
    return clamp(raw * gain, -1.0, 1.0), evidence


def _breakout_score(state: Analysis, policy: PlatformPolicy) -> tuple[float, Evidence]:
    structure, price = state.primary.structure, state.primary.priceAction
    volatility, noise = state.primary.volatility, state.primary.noise
    thrusts: list[float | None] = []
    evidence = Evidence()
    for window, weight in BREAKOUT_WINDOW_WEIGHTS:
        sign = breakout_sign(
            getattr(structure, f"abovePriorHigh{window}"),
            getattr(structure, f"belowPriorLow{window}"),
        )
        thrusts.append(sign)
        evidence.add(
            f"THRUST_{window}", f"Close beyond the prior {window}-bar extreme", weight, sign, sign
        )
    thrust = mean_of(thrusts)
    if thrust is None or abs(thrust) < EPSILON:
        # No prior extreme was actually taken out. Touching a level is not breaking it.
        return 0.0, evidence
    evidence.add(
        "CLOSE_LOCATION",
        "Close within the bar range",
        0.7,
        None if price.closeLocation is None else (price.closeLocation - 0.5) * 2.0,
        price.closeLocation,
    )
    evidence.add(
        "BOLLINGER_Z",
        "Close against the Bollinger mean",
        0.7,
        signed_ramp(volatility.bbZScore, BREAKOUT_BB_Z_HALF),
        volatility.bbZScore,
    )
    if (state.micro.microCoverage10s or 0.0) >= MICRO_EVIDENCE_FLOOR:
        evidence.add(
            "MICRO_PUSH",
            "One-second push into the break",
            0.6 * policy.microWeight,
            signed_ramp(state.micro_norm(state.micro.microReturn5sBps), BREAKOUT_MICRO_HALF),
            state.micro.microReturn5sBps,
        )
    direction = evidence.score
    if direction is None or direction * thrust <= 0:
        # The bar closed beyond a level but the close itself points the other way.
        return 0.0, evidence
    confirmation = BREAKOUT_CONFIRMATION.score(
        body_to_range=price.bodyToRange,
        close_location=price.closeLocation,
        thrust=thrust,
        range_expansion=volatility.rangeExpansion,
        efficiency=noise.efficiencyRatio10,
    )
    strength = BREAKOUT_CONFIRMATION_UNKNOWN if confirmation is None else confirmation
    return clamp(direction * strength, -1.0, 1.0), evidence


def _range_score(state: Analysis, breakout: float) -> float | None:
    trend, noise = state.primary.trend, state.primary.noise
    evidence = Evidence()
    evidence.add(
        "LOW_EFFICIENCY",
        "Efficiency ratio",
        1.2,
        ramp(noise.efficiencyRatio10, RANGE_EFFICIENCY_ZERO, RANGE_EFFICIENCY_ONE),
        noise.efficiencyRatio10,
    )
    evidence.add(
        "CHOPPINESS",
        "Choppiness index",
        1.2,
        ramp(noise.choppiness14, RANGE_CHOP_ZERO, RANGE_CHOP_ONE),
        noise.choppiness14,
    )
    evidence.add(
        "RANGE_OVERLAP",
        "Overlap of adjacent bar ranges",
        1.0,
        ramp(noise.rangeOverlap5, RANGE_OVERLAP_ZERO, RANGE_OVERLAP_ONE),
        noise.rangeOverlap5,
    )
    stack = state.norm(trend.ema9To20Bps)
    evidence.add(
        "TIGHT_EMA_STACK",
        "EMA9 and EMA20 separation",
        0.9,
        None if stack is None else ramp(abs(stack), RANGE_STACK_WIDE, RANGE_STACK_TIGHT),
        trend.ema9To20Bps,
    )
    slope = state.norm(trend.priceSlope10)
    evidence.add(
        "FLAT_SLOPE",
        "Ten-bar price slope magnitude",
        0.9,
        None if slope is None else ramp(abs(slope), RANGE_SLOPE_STEEP, RANGE_SLOPE_FLAT),
        trend.priceSlope10,
    )
    score = evidence.score
    if score is None:
        return None
    # The absence of a breakout is not evidence of a range; every quiet market lacks one, and
    # counting it would put a floor under the range score in a trend. A breakout that *is*
    # present, though, contradicts a range directly, so it damps the score instead.
    return clamp(score * (1.0 - abs(breakout)), 0.0, 1.0)


def _noise_score(state: Analysis) -> float | None:
    noise, quality = state.primary.noise, state.primary.quality
    evidence = Evidence()
    evidence.add(
        "SIGN_FLIPS",
        "Return sign flip rate",
        1.2,
        ramp(noise.signFlipRate10, NOISE_FLIP_ZERO, NOISE_FLIP_ONE),
        noise.signFlipRate10,
    )
    evidence.add(
        "LOW_EFFICIENCY",
        "Efficiency ratio",
        1.0,
        ramp(noise.efficiencyRatio10, NOISE_EFFICIENCY_ZERO, NOISE_EFFICIENCY_ONE),
        noise.efficiencyRatio10,
    )
    evidence.add(
        "RANGE_OVERLAP",
        "Overlap of adjacent bar ranges",
        0.8,
        ramp(noise.rangeOverlap5, NOISE_OVERLAP_ZERO, NOISE_OVERLAP_ONE),
        noise.rangeOverlap5,
    )
    evidence.add(
        "CHOPPINESS",
        "Choppiness index",
        0.8,
        ramp(noise.choppiness14, NOISE_CHOP_ZERO, NOISE_CHOP_ONE),
        noise.choppiness14,
    )
    if (state.micro.microCoverage10s or 0.0) >= MICRO_EVIDENCE_FLOOR:
        evidence.add(
            "MICRO_SIGN_FLIPS",
            "One-second sign flip rate",
            0.7,
            ramp(state.micro.microSignFlipRate10s, NOISE_MICRO_FLIP_ZERO, NOISE_MICRO_FLIP_ONE),
            state.micro.microSignFlipRate10s,
        )
    evidence.add(
        "POOR_QUALITY",
        "Share of clean recent bars",
        0.6,
        ramp(quality.goodRatio10, NOISE_QUALITY_ZERO, NOISE_QUALITY_ONE),
        quality.goodRatio10,
    )
    evidence.add(
        "POOR_COVERAGE",
        "Mean recent coverage",
        0.6,
        ramp(quality.meanCoverage10, NOISE_QUALITY_ZERO, NOISE_QUALITY_ONE),
        quality.meanCoverage10,
    )
    return evidence.score


def _volatility_states(state: Analysis) -> tuple[float | None, float | None]:
    volatility = state.primary.volatility
    fast, slow = volatility.realizedVol10Bps, volatility.realizedVol20Bps
    ratio = None if fast is None or not slow else fast / slow
    width = volatility.bbWidthBps
    atr = volatility.atr14Bps
    band = None if width is None or not atr else width / atr
    expansion = mean_of(
        [
            ramp(volatility.rangeExpansion, VOL_EXPANSION_RANGE_ZERO, VOL_EXPANSION_RANGE_ONE),
            ramp(ratio, VOL_EXPANSION_RATIO_ZERO, VOL_EXPANSION_RATIO_ONE),
            ramp(band, VOL_EXPANSION_BAND_ZERO, VOL_EXPANSION_BAND_ONE),
        ]
    )
    compression = mean_of(
        [
            ramp(volatility.rangeExpansion, VOL_COMPRESSION_RANGE_ZERO, VOL_COMPRESSION_RANGE_ONE),
            ramp(ratio, VOL_COMPRESSION_RATIO_ZERO, VOL_COMPRESSION_RATIO_ONE),
            ramp(band, VOL_COMPRESSION_BAND_ZERO, VOL_COMPRESSION_BAND_ONE),
        ]
    )
    return expansion, compression


def _candidates(
    trend: float,
    range_score: float,
    breakout: float,
    noise: float,
    expansion: float,
    compression: float,
) -> list[tuple[Regime, float, float]]:
    """(label, strength, floor) in tiebreak priority order; equal strengths keep this order."""
    return [
        ("NOISY", noise, NOISY_FLOOR),
        ("BREAKOUT_UP", max(breakout, 0.0), BREAKOUT_FLOOR),
        ("BREAKOUT_DOWN", max(-breakout, 0.0), BREAKOUT_FLOOR),
        ("TREND_UP", max(trend, 0.0), TREND_FLOOR),
        ("TREND_DOWN", max(-trend, 0.0), TREND_FLOOR),
        ("RANGE", range_score, RANGE_FLOOR),
        ("VOLATILITY_EXPANSION", expansion, VOLATILITY_FLOOR),
        ("VOLATILITY_COMPRESSION", compression, VOLATILITY_FLOOR),
    ]


CONFLICTING: Mapping[Regime, tuple[Regime, ...]] = {
    "TREND_UP": ("RANGE", "NOISY", "TREND_DOWN", "BREAKOUT_DOWN"),
    "TREND_DOWN": ("RANGE", "NOISY", "TREND_UP", "BREAKOUT_UP"),
    "BREAKOUT_UP": ("RANGE", "NOISY", "TREND_DOWN", "BREAKOUT_DOWN"),
    "BREAKOUT_DOWN": ("RANGE", "NOISY", "TREND_UP", "BREAKOUT_UP"),
    "RANGE": ("TREND_UP", "TREND_DOWN", "BREAKOUT_UP", "BREAKOUT_DOWN", "NOISY"),
    "NOISY": ("TREND_UP", "TREND_DOWN", "BREAKOUT_UP", "BREAKOUT_DOWN", "RANGE"),
    "VOLATILITY_EXPANSION": ("RANGE", "VOLATILITY_COMPRESSION"),
    "VOLATILITY_COMPRESSION": ("VOLATILITY_EXPANSION", "TREND_UP", "TREND_DOWN"),
    "UNCERTAIN": (),
}
"""Which other readings would contradict each label.

Two labels that merely coexist are not in conflict: a breakout up and a trend up describe the
same market, and a strong one of each should not talk the other down. A range and noise do
conflict, because they disagree about whether the structure can be read at all — and that is
the difference between an excursion worth fading and a path worth leaving alone."""


def _label(
    candidates: list[tuple[Regime, float, float]], noise: float
) -> tuple[Regime, float, float]:
    """Winning label, its strength, and the strongest contradicting strength.

    Noise is settled first and is not a peer of the others. A path this incoherent cannot
    support a structural reading at all, so whatever the trend and range measures appear to
    say about it is a description of the noise rather than of the market.
    """
    scores = {label: strength for label, strength, _ in candidates}

    def conflict(winner: Regime) -> float:
        return max((scores.get(other, 0.0) for other in CONFLICTING[winner]), default=0.0)

    if noise >= NOISY_FLOOR:
        return "NOISY", noise, conflict("NOISY")
    qualified = [item for item in candidates if item[0] != "NOISY" and item[1] >= item[2]]
    if not qualified:
        ranked = sorted(
            (strength for label, strength, _ in candidates if label != "NOISY"), reverse=True
        )
        best = ranked[0] if ranked else 0.0
        return (
            "UNCERTAIN",
            best * UNCERTAIN_CONFIDENCE_FACTOR,
            (ranked[1] if len(ranked) > 1 else 0.0),
        )
    winner = max(qualified, key=lambda item: item[1])
    return winner[0], winner[1], conflict(winner[0])


def _bias(regime: Regime, trend: float, breakout: float) -> Direction:
    if regime == "NOISY":
        return "NEUTRAL"
    total = REGIME_BIAS_TREND_WEIGHT + REGIME_BIAS_BREAKOUT_WEIGHT
    blended = (trend * REGIME_BIAS_TREND_WEIGHT + breakout * REGIME_BIAS_BREAKOUT_WEIGHT) / total
    if blended >= REGIME_BIAS_FLOOR:
        return "UP"
    if blended <= -REGIME_BIAS_FLOOR:
        return "DOWN"
    return "NEUTRAL"


def _rejected(bundle: FeatureBundle, gate: Gate) -> RegimeSnapshot:
    return RegimeSnapshot(
        platform=bundle.platform,
        slotId=bundle.slotId,
        assetName=bundle.assetName,
        contextId=bundle.contextId,
        asOf=bundle.asOf,
        primaryTimeframe=bundle.primaryTimeframe,
        featureVersion=bundle.featureVersion,
        regimeVersion=REGIME_VERSION,
        primaryRegime="UNCERTAIN",
        trendScore=0.0,
        rangeScore=0.0,
        breakoutScore=0.0,
        noiseScore=0.0,
        volatilityExpansionScore=0.0,
        volatilityCompressionScore=0.0,
        directionBias="SKIP",
        confidence=0.0,
        qualityFit=0.0,
        reasons=[],
        vetoes=list(gate.vetoes),
        status=gate.status,
    )


def classify(bundle: FeatureBundle, gate: Gate | None = None) -> RegimeSnapshot:
    """Read one FeatureBundle into a regime. Pure: the same bundle always yields the same
    snapshot, and nothing outside the bundle is consulted."""
    resolved = market_gate(bundle) if gate is None else gate
    primary = bundle.primary
    if not resolved.usable or primary is None:
        return _rejected(bundle, resolved)

    state = analysis(bundle, primary, resolved.qualityFit)
    policy = policy_for(bundle.platform)
    trend_raw, trend_evidence = _trend_score(state, policy)
    breakout, breakout_evidence = _breakout_score(state, policy)
    trend = 0.0 if trend_raw is None else trend_raw
    range_raw = _range_score(state, breakout)
    noise_raw = _noise_score(state)
    expansion_raw, compression_raw = _volatility_states(state)
    range_score = 0.0 if range_raw is None else range_raw
    noise = 0.0 if noise_raw is None else noise_raw
    expansion = 0.0 if expansion_raw is None else expansion_raw
    compression = 0.0 if compression_raw is None else compression_raw

    label, strength, runner_up = _label(
        _candidates(trend, range_score, breakout, noise, expansion, compression), noise
    )
    contested = runner_up / max(strength + runner_up, EPSILON)
    consistency = clamp(1.0 - REGIME_CONFLICT_WEIGHT * contested, 0.0, 1.0)
    reasons: list[Reason] = [
        Reason(code=f"REGIME_{label}", message=f"Primary regime {label}", value=strength)
    ]
    reasons.extend(trend_evidence.reasons(limit=3))
    reasons.extend(breakout_evidence.reasons(limit=2))
    if noise >= NOISY_FLOOR:
        reasons.append(Reason(code="HIGH_NOISE", message="Path quality is poor", value=noise))
    return RegimeSnapshot(
        platform=bundle.platform,
        slotId=bundle.slotId,
        assetName=bundle.assetName,
        contextId=bundle.contextId,
        asOf=bundle.asOf,
        primaryTimeframe=bundle.primaryTimeframe,
        featureVersion=SUPPORTED_FEATURE_VERSION,
        regimeVersion=REGIME_VERSION,
        primaryRegime=label,
        trendScore=clamp(trend, -1.0, 1.0),
        rangeScore=clamp(range_score, 0.0, 1.0),
        breakoutScore=clamp(breakout, -1.0, 1.0),
        noiseScore=clamp(noise, 0.0, 1.0),
        volatilityExpansionScore=clamp(expansion, 0.0, 1.0),
        volatilityCompressionScore=clamp(compression, 0.0, 1.0),
        directionBias=_bias(label, trend, breakout),
        confidence=clamp(strength * consistency * resolved.qualityFit, 0.0, 1.0),
        qualityFit=resolved.qualityFit,
        reasons=reasons[:MAX_REGIME_REASONS],
        vetoes=list(resolved.vetoes),
        status=resolved.status,
    )
