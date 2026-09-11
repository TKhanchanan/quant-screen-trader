"""Pure entry, expiry and outcome resolution. No state, no clock, no I/O.

Every rule that decides *which* price a paper trade uses lives here, so the no-lookahead
guarantees are one short module that can be read end to end and tested directly.
"""

from __future__ import annotations

import math
from typing import Literal
from uuid import UUID, uuid5

from quant_engine.configuration import Platform
from quant_engine.market_models import PriceSample, SourceType
from quant_engine.paper.models import PaperDirection, PaperOutcome, PaperTrade
from quant_engine.paper.policy import PAPER_NAMESPACE

type SourceMode = Literal["LIVE", "REPLAY", "SYNTHETIC"]

LIVE_SOURCES = frozenset({"DOM", "VISUAL"})
"""The two live capture paths. The capture pipeline falls back from DOM to OCR mid-series on
purpose, so both describe the same real market and may appear inside one trade. REPLAY and
SYNTHETIC describe different worlds entirely and never mix with them or with each other."""

USABLE_QUALITY = frozenset({"GOOD", "DEGRADED"})
"""The same bar Phase 5 applies before a sample becomes canonical at all. Re-asserted here
rather than assumed, because an entry price is the one number a paper outcome cannot survive
being wrong about."""


def source_mode(source: SourceType) -> SourceMode:
    if source in LIVE_SOURCES:
        return "LIVE"
    return "REPLAY" if source == "REPLAY" else "SYNTHETIC"


def usable(sample: PriceSample) -> bool:
    """Whether this canonical sample is good enough to price a paper trade.

    An unusable sample is skipped, never coerced. There is no fallback price, no last-known
    value and no zero: the trade simply keeps waiting, and times out honestly if nothing
    better arrives."""
    return (
        sample.quality.state in USABLE_QUALITY and math.isfinite(sample.price) and sample.price > 0
    )


def same_identity(trade: PaperTrade, sample: PriceSample) -> bool:
    """A paper trade belongs to exactly one platform, slot, asset and context.

    The physical slot is reused when an operator changes an asset, so matching on the slot
    alone would let a later BTC price settle an EUR/USD trade."""
    return (
        sample.platform == trade.platform
        and sample.slotId == trade.slotId
        and sample.assetName == trade.assetName
        and sample.contextId == trade.contextId
    )


def paper_trade_id(
    *,
    platform: Platform,
    board_as_of: int,
    slot_id: int,
    asset_name: str,
    context_id: UUID,
    ranking_version: str,
    paper_version: str,
) -> UUID:
    """Deterministic identity for one Phase 8 selection under one set of versions.

    UUID5 rather than UUID4: replaying the same canonical observations must reproduce the same
    trade ids, or a replay would append a parallel history instead of reproducing one."""
    key = "|".join(
        (
            platform,
            str(board_as_of),
            str(slot_id),
            asset_name,
            str(context_id),
            ranking_version,
            paper_version,
        )
    )
    return uuid5(PAPER_NAMESPACE, key)


def entry_deadline(trade: PaperTrade, max_entry_delay_ms: int) -> int:
    """Measured from when the decision became available, never from the bar it described."""
    return trade.decisionAvailableAt + max_entry_delay_ms


def resolution_deadline(expiry_target_time: int, max_resolution_lag_ms: int) -> int:
    return expiry_target_time + max_resolution_lag_ms


def accepts_entry(trade: PaperTrade, sample: PriceSample, *, deadline: int) -> bool:
    """The first canonical eligible price at or after ``decisionAvailableAt``.

    Strictly forward-looking. There is no search backwards for a better fill, no use of the
    pre-signal price, no reaching into a candle's low or high, and no backdating to the board's
    close time: a price that existed before the decision did is a price nobody could have
    traded on."""
    return usable(sample) and trade.decisionAvailableAt <= sample.timestamp <= deadline


def accepts_expiry(trade: PaperTrade, sample: PriceSample, *, deadline: int) -> bool:
    """The first canonical eligible price at or after the expiry target.

    "First" is the whole rule. A sample a millisecond before the target never settles the
    trade however favourable it looks, and once a qualifying sample is taken the trade is
    resolved, so a later and better price can never replace it."""
    if trade.expiryTargetTime is None or trade.entrySource is None:
        return False
    if source_mode(sample.sourceType) != source_mode(trade.entrySource):
        return False
    return usable(sample) and trade.expiryTargetTime <= sample.timestamp <= deadline


def outcome_for(direction: PaperDirection, entry_price: float, expiry_price: float) -> PaperOutcome:
    """Strict comparison, with no tolerance band.

    The project has no canonical tick-size contract, so any band would be an invented number
    that quietly reclassified real losses as draws. Equal prices are a DRAW because that is
    what the canonical data says happened."""
    if expiry_price == entry_price:
        return "DRAW"
    rising = expiry_price > entry_price
    return "WIN" if rising == (direction == "UP") else "LOSS"


def price_delta_bps(entry_price: float, expiry_price: float) -> float | None:
    """Descriptive movement in basis points. Not profit, and not payout-adjusted."""
    if entry_price == 0 or not math.isfinite(entry_price):
        return None
    return (expiry_price - entry_price) / entry_price * 10_000
