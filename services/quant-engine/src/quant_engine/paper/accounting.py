"""Simulated money for a resolved paper trade, or an honest ``None``.

The one rule this module exists to enforce: a directional outcome is always knowable from
canonical prices, but a monetary result is not knowable without an explicit stake and payout.
Nothing here reads a broker wallet, scrapes a payout percentage off a platform panel or infers
a rate from anything observed. Real payout tracking is a separate problem and is not Phase 9's.
"""

from __future__ import annotations

from quant_engine.paper.models import PaperOutcome
from quant_engine.paper.policy import PaperSettings


class AccountingSnapshot:
    """The accounting terms a trade was opened under, frozen at entry.

    Snapshotted rather than looked up at resolution: an operator who changes the simulated
    payout rate halfway through a trade must not retroactively rewrite what an already-running
    simulation was measuring.
    """

    __slots__ = ("currency", "payoutRate", "stake")

    def __init__(
        self, currency: str | None, stake: float | None, payout_rate: float | None
    ) -> None:
        configured = currency is not None and stake is not None and payout_rate is not None
        self.currency = currency if configured else None
        self.stake = stake if configured else None
        self.payoutRate = payout_rate if configured else None

    @property
    def configured(self) -> bool:
        return self.currency is not None and self.stake is not None and self.payoutRate is not None


def snapshot_at_entry(settings: PaperSettings) -> AccountingSnapshot:
    return AccountingSnapshot(settings.paperCurrency, settings.paperStake, settings.paperPayoutRate)


def realized_pnl(
    outcome: PaperOutcome, *, stake: float | None, payout_rate: float | None
) -> float | None:
    """Net simulated profit or loss on one resolved trade.

    ``payout_rate`` is the net profit fraction on a win, matching how binary brokers quote it:
    a 50 stake at 0.82 returns 41 of profit on a win and loses the whole 50 on a loss. A draw
    returns the stake, so its result is exactly zero.

    Anything not resolved — and anything resolved without an accounting snapshot — returns
    ``None``. ``None`` means "not known", and it is never collapsed to 0.0.
    """
    if stake is None or payout_rate is None:
        return None
    if outcome == "WIN":
        return stake * payout_rate
    if outcome == "LOSS":
        return -stake
    if outcome == "DRAW":
        return 0.0
    return None
