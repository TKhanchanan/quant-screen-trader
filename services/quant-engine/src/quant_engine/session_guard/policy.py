"""Trading-date derivation, session identity and the two stop rules. Pure functions only.

Nothing here reads a clock. Every function takes the instant it is reasoning about, so the same
settlement sequence produces the same session, the same boundaries and the same triggers on
every replay.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

SESSION_NAMESPACE = UUID("3d5c8b21-9a7e-4f10-b6c4-2e8d7a1f0b93")
"""Fixed UUID5 namespace for deterministic daily session identity. Never regenerated: a new
namespace would make today reload as a different session and lose the day's lock."""

DEFAULT_PROFILE = "local"
"""Identity of the account this accounting belongs to. The application has no account model
yet, so one local profile is the honest answer; the field exists so a future multi-account
build cannot silently pool two operators' days into one total."""


def trading_date(epoch_ms: int, timezone: str, reset_hour: int) -> date:
    """Which trading day an instant belongs to, in the operator's own timezone.

    The UTC date is never used. In Bangkok a settlement at 06:30 local is still the previous UTC
    day, and an accounting layer that split the day there would report two half sessions. With a
    reset hour, an instant before it still belongs to the day that began the previous morning.
    """
    local = datetime.fromtimestamp(epoch_ms / 1000, ZoneInfo(timezone))
    return local.date() - timedelta(days=1) if local.hour < reset_hour else local.date()


def boundary_ms(day: date, timezone: str, reset_hour: int) -> int:
    """The instant a trading day opens, in epoch milliseconds."""
    zone = ZoneInfo(timezone)
    return int(datetime.combine(day, time(hour=reset_hour), tzinfo=zone).timestamp() * 1000)


def next_boundary_ms(day: date, timezone: str, reset_hour: int) -> int:
    """When this trading day ends and the next one opens."""
    return boundary_ms(day + timedelta(days=1), timezone, reset_hour)


def session_id(
    *,
    profile: str,
    day: date,
    timezone: str,
    currency: str,
    guard_version: str,
) -> UUID:
    """Deterministic identity for one operator's one trading day under one guard contract.

    UUID5 rather than UUID4: reopening the application must land on the same session, or a day
    that already hit its loss limit would come back as a fresh one with permission restored.
    """
    key = "|".join((profile, day.isoformat(), timezone, currency, guard_version))
    return uuid5(SESSION_NAMESPACE, key)


def target_reached(realized_pnl: float, target: float | None) -> bool:
    """Crossing, not equality. A settlement that takes 575 past a 600 target to 616 has reached
    it; requiring an exact landing would mean the target almost never fires."""
    return target is not None and realized_pnl >= target


def loss_limit_reached(realized_pnl: float, limit: float | None) -> bool:
    """The limit is a positive magnitude compared against a negative total, so -330 has passed
    a limit of 300."""
    return limit is not None and realized_pnl <= -limit


def target_progress(realized_pnl: float, target: float | None) -> float | None:
    """0..1 for a progress bar. A losing day is zero progress towards a profit target, not a
    negative bar, and an overshoot is full rather than more than full."""
    if target is None or target <= 0:
        return None
    return min(1.0, max(0.0, realized_pnl / target))


def loss_progress(realized_pnl: float, limit: float | None) -> float | None:
    if limit is None or limit <= 0:
        return None
    return min(1.0, max(0.0, -realized_pnl / limit))


def remaining_to_target(realized_pnl: float, target: float | None) -> float | None:
    """How much more is needed. Never negative: once the target is met nothing more is."""
    if target is None:
        return None
    return max(0.0, target - realized_pnl)
