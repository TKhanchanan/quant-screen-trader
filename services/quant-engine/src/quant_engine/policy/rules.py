"""Small, predetermined scopes; no threshold search or conjunction optimization."""

from __future__ import annotations

from quant_engine.analytics.models import AnalyticsRow
from quant_engine.opportunity.models import OpportunityCandidate
from quant_engine.policy.models import Scope

SPECIFICITY = {
    "global": 0,
    "platform": 1,
    "regime": 2,
    "strategy-regime": 3,
    "rankScore": 2,
    "ensembleConfidence": 2,
}


def matches(
    scope: Scope, item: AnalyticsRow | OpportunityCandidate, strategies: tuple[str, ...] = ()
) -> bool:
    if scope.platform is not None and item.platform != scope.platform:
        return False
    if scope.scopeType == "global":
        return True
    if scope.scopeType == "platform":
        return item.platform == scope.scopeValue
    if scope.scopeType == "regime":
        return item.primaryRegime == scope.scopeValue
    if scope.scopeType == "strategy-regime":
        strategy, regime = scope.scopeValue.split("|", 1)
        if isinstance(item, AnalyticsRow):
            strategies = tuple(
                v.strategyId
                for v in item.strategyVotes
                if v.eligible and v.direction == item.direction
            )
        return item.primaryRegime == regime and strategy in strategies
    value = item.rankScore if scope.scopeType == "rankScore" else item.ensembleConfidence
    return (
        scope.bandStart is not None
        and scope.bandEnd is not None
        and scope.bandStart <= value
        and (value < scope.bandEnd or value == scope.bandEnd == 1)
    )


def candidate_scopes(train: tuple[AnalyticsRow, ...]) -> tuple[Scope, ...]:
    """Only identities observed in TRAIN; fixed 0.2-wide bands, never best cuts from TEST."""
    scopes: list[Scope] = []
    for platform in sorted({r.platform for r in train}):
        scopes.append(Scope(scopeType="platform", scopeValue=platform, platform=platform))
        rows = [r for r in train if r.platform == platform]
        scopes.extend(
            Scope(scopeType="regime", scopeValue=regime, platform=platform)
            for regime in sorted({r.primaryRegime for r in rows})
        )
        pairs = sorted(
            {
                v.strategyId + "|" + r.primaryRegime
                for r in rows
                for v in r.strategyVotes
                if v.eligible and v.direction == r.direction
            }
        )
        scopes.extend(
            Scope(scopeType="strategy-regime", scopeValue=pair, platform=platform) for pair in pairs
        )
        for metric in ("rankScore", "ensembleConfidence"):
            for i in range(5):
                scopes.append(
                    Scope(scopeType=metric, platform=platform, bandStart=i / 5, bandEnd=(i + 1) / 5)
                )
    return tuple(scopes)
