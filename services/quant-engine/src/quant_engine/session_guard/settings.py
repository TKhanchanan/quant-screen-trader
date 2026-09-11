"""Operator configuration for the daily session guard.

Every field here is a *limit*, never a lever. Nothing in this model can make the quant layers
produce a different decision: the guard reads these to decide whether the session may still
accept new entries, and for nothing else.

There are no default amounts. A profit target and a loss limit are statements about a specific
operator's money and risk, and inventing either would be the guard quietly deciding for them.
"""

from __future__ import annotations

from typing import Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, model_validator

from quant_engine.configuration import Model

DEFAULT_TIMEZONE = "Asia/Bangkok"
DEFAULT_CURRENCY = "THB"


class SessionGuardSettings(Model):
    """What an operator may configure. Invalid values are refused, never quietly corrected.

    A target of zero, a negative limit or an unknown timezone is a mistake in something that
    decides when trading stops. Coercing any of them to a "sensible" value would mean the guard
    is enforcing a rule the operator never asked for, so each is rejected with its reason.
    """

    enabled: bool = False
    """Off by default. A risk control that switched itself on with invented numbers would be
    enforcing somebody else's idea of a good day."""

    dailyProfitTarget: float | None = Field(default=None, gt=0, le=100_000_000)
    dailyLossLimit: float | None = Field(default=None, gt=0, le=100_000_000)
    """Both are magnitudes. The loss limit is stated positive and compared against a negative
    realized P/L, so "300" means "stop at minus three hundred"."""

    currency: str = Field(default=DEFAULT_CURRENCY, min_length=1, max_length=8)
    """The only currency this session will aggregate. A settlement in any other is counted in
    the win/loss tally and excluded from the money, never converted."""

    timezone: str = Field(default=DEFAULT_TIMEZONE, min_length=1, max_length=64)
    resetHour: int = Field(default=0, ge=0, le=23, strict=True)
    """When one trading day becomes the next, in the configured timezone. ``5`` makes the day
    run 05:00 to 04:59 the following morning, which is how an overnight session is actually
    counted."""

    notifyOnProfitTarget: bool = True
    notifyOnLossLimit: bool = True

    closeAppOnProfitTarget: bool = True
    closeAppOnLossLimit: bool = True
    waitForOpenTradesBeforeClose: bool = True
    """Closing while Phase 9 still has unresolved outcomes would throw away measurements that
    are seconds from completing, so the default waits for them."""

    lockAfterProfitTarget: bool = True
    lockAfterLossLimit: bool = True
    """A locked day cannot be re-armed. Re-arming after a loss limit is the whole failure mode
    this layer exists to prevent."""

    @model_validator(mode="after")
    def known_timezone(self) -> Self:
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError, KeyError) as error:
            raise ValueError(f"Unknown IANA timezone: {self.timezone}") from error
        return self

    @property
    def enforcing(self) -> bool:
        """Whether the guard can actually stop anything. Enabled with neither a target nor a
        limit is a valid, honest configuration: it accounts the day and refuses nothing."""
        return self.enabled and (
            self.dailyProfitTarget is not None or self.dailyLossLimit is not None
        )

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)
