"""Timing policy and operator settings for ``qst-paper-v1``.

Every number here is part of the version contract. A paper trade's meaning is "this direction
was right five seconds later", so changing a horizon, an entry bound or a resolution bound
changes what every stored outcome means. None of these are tunable at runtime: they are
constants, the settings model defaults to them, and a test asserts the two agree. Changing one
requires a new ``PAPER_VERSION``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, Self
from uuid import UUID

from pydantic import Field, model_validator

from quant_engine.configuration import Model, Platform

PAPER_DURATION_MS: Final[Mapping[Platform, int]] = MappingProxyType(
    {"capitalbear": 5_000, "iqoption": 60_000}
)
"""The simulated holding horizon per platform, in market time.

CapitalBear settles five-second bars and IQ Option one-minute bars, so the two platforms are
measuring different questions and their outcomes are never pooled without naming the platform.
These are simulation horizons chosen to match each broker's own shortest contract; they are not
read from the broker, and nothing here schedules a real expiry."""

CAPITALBEAR_MAX_ENTRY_DELAY_MS: Final = 5_000
IQOPTION_MAX_ENTRY_DELAY_MS: Final = 10_000
MAX_ENTRY_DELAY_MS: Final[Mapping[Platform, int]] = MappingProxyType(
    {"capitalbear": CAPITALBEAR_MAX_ENTRY_DELAY_MS, "iqoption": IQOPTION_MAX_ENTRY_DELAY_MS}
)
"""How long a paper intent may wait for its first post-decision price before it is abandoned.

One horizon on CapitalBear. A decision that could not be entered inside the bar it was made
for describes a market that has already moved on, and inventing an entry for it would be the
one thing this layer exists to avoid."""

CAPITALBEAR_MAX_RESOLUTION_LAG_MS: Final = 5_000
IQOPTION_MAX_RESOLUTION_LAG_MS: Final = 10_000
MAX_RESOLUTION_LAG_MS: Final[Mapping[Platform, int]] = MappingProxyType(
    {
        "capitalbear": CAPITALBEAR_MAX_RESOLUTION_LAG_MS,
        "iqoption": IQOPTION_MAX_RESOLUTION_LAG_MS,
    }
)
"""How late the first at-or-after-expiry price may arrive and still settle the trade. A price
taken long after the horizon is a different market, so the trade is marked INVALID instead."""

MAX_OPEN_PER_SLOT: Final = 1
"""One live paper trade per platform and slot. Paper positions are never stacked."""

MAX_OPEN_PER_PLATFORM: Final = 3
"""Simulation-state protection, not bankroll sizing. Phase 8 names one leader per epoch, and
separate slots are separate analytical selections, so concurrent trades across slots are
allowed — but the count is bounded so a pathological stretch cannot grow state without limit.
This number is not tuned and carries no opinion about risk."""

TRADE_HISTORY_CAPACITY: Final = 512
"""Bounded per process. Parquet is the durable record; memory holds a diagnostic tail."""

SETTLEMENT_HISTORY_CAPACITY: Final = 256
SELECTION_MEMORY: Final = 512
"""How many recent Phase 8 selections are remembered for duplicate suppression, per platform."""

PAPER_NAMESPACE: Final = UUID("7f3a1d2e-9c4b-4a6d-8e7f-0a1b2c3d4e5f")
"""Fixed UUID5 namespace for deterministic paper trade identity. Never regenerated: a new
namespace would give the same replayed selection a different id."""

ENABLED_ENV = "QST_PAPER_ENABLED"
REQUIRE_READY_ENV = "QST_PAPER_REQUIRE_READY_BOARD"
CURRENCY_ENV = "QST_PAPER_CURRENCY"
STAKE_ENV = "QST_PAPER_STAKE"
PAYOUT_ENV = "QST_PAPER_PAYOUT_RATE"


class PaperSettings(Model):
    """What an operator may configure, and what is frozen by the version contract.

    Only ``enabled``, ``requireReadyBoard`` and the three accounting fields are settable from
    the environment. The horizons and bounds are fields so a stored trade can be read back
    against the policy it ran under, but they default to the module constants and are not
    environment-tunable: changing them would silently redefine ``qst-paper-v1``.
    """

    enabled: bool = True

    capitalbearDurationMs: int = Field(default=PAPER_DURATION_MS["capitalbear"], gt=0, le=3_600_000)
    iqoptionDurationMs: int = Field(default=PAPER_DURATION_MS["iqoption"], gt=0, le=3_600_000)

    maxEntryDelayMsCapitalBear: int = Field(
        default=CAPITALBEAR_MAX_ENTRY_DELAY_MS, gt=0, le=600_000
    )
    maxEntryDelayMsIqOption: int = Field(default=IQOPTION_MAX_ENTRY_DELAY_MS, gt=0, le=600_000)

    maxResolutionLagMsCapitalBear: int = Field(
        default=CAPITALBEAR_MAX_RESOLUTION_LAG_MS, gt=0, le=600_000
    )
    maxResolutionLagMsIqOption: int = Field(
        default=IQOPTION_MAX_RESOLUTION_LAG_MS, gt=0, le=600_000
    )

    requireReadyBoard: bool = True
    """Default: only a READY board with a selected candidate creates a paper intent. Phase 8's
    own gates are never lowered to manufacture more paper trades."""

    paperCurrency: str | None = Field(default=None, min_length=1, max_length=8)
    paperStake: float | None = Field(default=None, gt=0, le=1_000_000)
    paperPayoutRate: float | None = Field(default=None, ge=0, le=10)
    """Net profit fraction on a win, not a gross multiple: 0.82 means a 50 stake returns 41 of
    profit. There is no default. A directional outcome is always knowable; simulated money is
    not, so it is reported only when an operator has stated all three of these."""

    @model_validator(mode="after")
    def accounting_is_all_or_nothing(self) -> Self:
        configured = (self.paperCurrency, self.paperStake, self.paperPayoutRate)
        if any(value is not None for value in configured) and any(
            value is None for value in configured
        ):
            raise ValueError("Paper accounting requires currency, stake and payout rate together")
        return self

    @property
    def accountingConfigured(self) -> bool:
        return (
            self.paperCurrency is not None
            and self.paperStake is not None
            and self.paperPayoutRate is not None
        )

    def duration_ms(self, platform: Platform) -> int:
        return self.capitalbearDurationMs if platform == "capitalbear" else self.iqoptionDurationMs

    def max_entry_delay_ms(self, platform: Platform) -> int:
        return (
            self.maxEntryDelayMsCapitalBear
            if platform == "capitalbear"
            else self.maxEntryDelayMsIqOption
        )

    def max_resolution_lag_ms(self, platform: Platform) -> int:
        return (
            self.maxResolutionLagMsCapitalBear
            if platform == "capitalbear"
            else self.maxResolutionLagMsIqOption
        )


def _flag(raw: str | None, default: bool) -> bool:
    if raw is None or not raw.strip():
        return default
    return raw.strip().casefold() in ("1", "true", "yes", "on")


def _number(raw: str | None) -> float | None:
    if raw is None or not raw.strip():
        return None
    return float(raw.strip())


def settings_from_environment(environ: Mapping[str, str] | None = None) -> PaperSettings:
    """Build settings from the process environment, the project's existing trusted channel.

    There is deliberately no HTTP endpoint that writes these. A browser-reachable way to set a
    simulated stake would be a mutation surface on a measurement layer for no benefit, and the
    read API is local-only and read-only for the same reason.

    A malformed or half-configured accounting block raises. The caller reports the message and
    continues with accounting off, because refusing to simulate direction over a typo in a
    currency code would lose real evidence for no safety gain.
    """
    values = os.environ if environ is None else environ
    currency = (values.get(CURRENCY_ENV) or "").strip() or None
    return PaperSettings(
        enabled=_flag(values.get(ENABLED_ENV), True),
        requireReadyBoard=_flag(values.get(REQUIRE_READY_ENV), True),
        paperCurrency=currency,
        paperStake=_number(values.get(STAKE_ENV)),
        paperPayoutRate=_number(values.get(PAYOUT_ENV)),
    )
