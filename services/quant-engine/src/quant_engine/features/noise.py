"""Path-quality measures. These are numeric facts; naming a regime belongs to Phase 7."""

from __future__ import annotations

import math
from collections.abc import Sequence

from quant_engine.features.math import clamp, finite, mean, safe_div

SIGN_FLIP_WINDOW = 10
OVERLAP_TRANSITIONS = 5


def efficiency_ratio(closes: Sequence[float], window: int) -> float | None:
    """Net displacement divided by the distance actually travelled, over ``window`` steps."""
    if len(closes) < window + 1:
        return None
    segment = list(closes)[-(window + 1) :]
    travelled = sum(abs(segment[i] - segment[i - 1]) for i in range(1, len(segment)))
    ratio = safe_div(abs(segment[-1] - segment[0]), travelled)
    return None if ratio is None else clamp(ratio, 0.0, 1.0)


def choppiness(
    true_ranges: Sequence[float], highs: Sequence[float], lows: Sequence[float], period: int = 14
) -> float | None:
    if len(true_ranges) < period or len(highs) < period or len(lows) < period:
        return None
    total = sum(true_ranges[-period:])
    span = max(highs[-period:]) - min(lows[-period:])
    ratio = safe_div(total, span)
    if ratio is None or ratio <= 0:
        return None
    value = finite(100.0 * math.log10(ratio) / math.log10(period))
    return None if value is None else clamp(value, 0.0, 100.0)


def sign_flip_rate(returns: Sequence[float], window: int = SIGN_FLIP_WINDOW) -> float | None:
    """Share of transitions between the last ``window`` non-zero returns that changed sign."""
    moves = [value for value in returns if value != 0][-window:]
    if len(moves) < 2:
        return None
    flips = sum(1 for i in range(1, len(moves)) if (moves[i] > 0) != (moves[i - 1] > 0))
    return safe_div(flips, len(moves) - 1)


def range_overlap(
    highs: Sequence[float], lows: Sequence[float], transitions: int = OVERLAP_TRANSITIONS
) -> float | None:
    """Mean overlap of adjacent candle ranges, averaged over the valid recent transitions.

    A pair where either candle has no range at all cannot express an overlap fraction, so it
    is skipped rather than counted as total agreement.
    """
    if len(highs) < 2 or len(lows) < 2:
        return None
    ratios: list[float] = []
    start = max(1, len(highs) - transitions)
    for index in range(start, len(highs)):
        previous_range = highs[index - 1] - lows[index - 1]
        current_range = highs[index] - lows[index]
        smallest = min(previous_range, current_range)
        if smallest <= 0:
            continue
        overlap = max(0.0, min(highs[index], highs[index - 1]) - max(lows[index], lows[index - 1]))
        ratios.append(overlap / smallest)
    return mean(ratios)
