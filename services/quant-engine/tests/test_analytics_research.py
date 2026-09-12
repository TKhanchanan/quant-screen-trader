"""T-CW, T-AP: a regime is a slice chosen after seeing the history, and is held to that standard.

The research output is the part of Phase 10 a later adaptive phase would read, so it is the part
where a lucky slice does the most damage. A regime table and a strategy pairing are chosen
*after* looking at the history they describe — the same selection bias a threshold search has —
and a tight pooled Wilson bound is not out-of-sample evidence however convincing it looks on one
pass through the data.

So `stable` means the same thing everywhere in this layer: the effect held in every chronological
period against that period's own baseline, with enough sample in each.
"""

from __future__ import annotations

from typing import Any

import analytics_fixtures as fixtures
from quant_engine.analytics import AnalyticsEngine, AnalyticsSettings, build
from quant_engine.analytics.models import ResearchFinding
from quant_engine.strategy.models import Regime

SETTINGS = AnalyticsSettings()
COUNT = 600


def history(
    favoured: Regime,
    chance: int,
    *,
    others: int = 40,
    only_early: bool = False,
    **changes: Any,
) -> list[Any]:
    """A history where one regime wins at ``chance``% and the rest at ``others``%.

    ``only_early`` confines the advantage to the first 60% of the timeline — the exact shape a
    pooled table reports as a discovery and an out-of-sample check refuses.
    """
    regimes: tuple[Regime, ...] = ("TREND_UP", "RANGE", "NOISY", "TREND_DOWN")
    rows = []
    for index in range(COUNT):
        regime = regimes[index % len(regimes)]
        early = index < int(COUNT * 0.6)
        favoured_now = regime == favoured and (early or not only_early)
        rows.append(
            fixtures.trade(
                f"res-{index}",
                outcome="WIN"
                if fixtures.scatter(index) < (chance if favoured_now else others)
                else "LOSS",
                regime=regime,
                direction="UP" if index % 2 else "DOWN",
                expiry=fixtures.BASE_MS + index * 60_000,
                **changes,
            )
        )
    return rows


def finding(findings: list[ResearchFinding], kind: str, subject: str) -> ResearchFinding:
    return next(item for item in findings if item.kind == kind and item.subject == subject)


def test_a_regime_that_worked_throughout_is_marked_stable() -> None:
    snapshot = AnalyticsEngine().analyze(build(history("TREND_UP", 72)))
    trend = finding(snapshot.research, "REGIME", "TREND_UP")
    assert trend.stable is True
    assert trend.reasons == ["CONSISTENT_ACROSS_SPLITS"]
    assert trend.lower95 is not None and trend.lower95 > 0.5
    assert (trend.trainCount, trend.validationCount, trend.testCount) == (90, 30, 30)
    for rate in (trend.trainWinRate, trend.validationWinRate, trend.testWinRate):
        assert rate is not None and rate > 0.5


def test_a_regime_that_only_worked_early_is_an_observation_and_not_a_finding() -> None:
    # Pooled over the whole history this looks strong. It is the single most dangerous shape a
    # research table can contain, because it is exactly what a decayed edge looks like.
    snapshot = AnalyticsEngine().analyze(build(history("TREND_UP", 85, only_early=True)))
    trend = finding(snapshot.research, "REGIME", "TREND_UP")
    assert trend.lower95 is not None and trend.lower95 > 0.5, "pooled evidence must look good"
    assert trend.trainWinRate is not None and trend.trainWinRate > 0.5
    assert trend.stable is False
    assert "DIRECTION_NOT_CONSISTENT" in trend.reasons
    assert any(reason.startswith("NOT_HELD_IN_") for reason in trend.reasons)


def test_a_regime_with_no_pooled_effect_is_never_promoted_by_a_lucky_period() -> None:
    # Every regime at an even split. Some period will still come out ahead by chance, and that
    # must never be enough on its own.
    snapshot = AnalyticsEngine().analyze(build(history("TREND_UP", 50, others=50)))
    regimes = [item for item in snapshot.research if item.kind in ("REGIME", "SKIP_CONDITION")]
    assert len(regimes) == 4
    for item in regimes:
        assert item.stable is False
        assert item.reasons == ["NO_POOLED_EFFECT"]


def test_a_losing_regime_is_reported_as_a_candidate_skip_condition() -> None:
    snapshot = AnalyticsEngine().analyze(build(history("TREND_UP", 8)))
    adverse = finding(snapshot.research, "SKIP_CONDITION", "TREND_UP")
    assert adverse.upper95 is not None and adverse.upper95 < 0.5
    assert adverse.stable is True
    # Reported, and wired to nothing. Phase 10 never blacklists a regime.
    assert adverse.appliedToLiveExecution is False
    assert all(item.appliedToLiveExecution is False for item in snapshot.research)


def test_a_thin_period_blocks_a_finding_even_when_the_pooled_slice_is_large() -> None:
    settings = AnalyticsSettings(trainRatio=0.94, validationRatio=0.03)
    snapshot = AnalyticsEngine(settings).analyze(build(history("TREND_UP", 80)))
    trend = finding(snapshot.research, "REGIME", "TREND_UP")
    assert trend.sampleCount == 150
    assert trend.stable is False
    assert "OUT_OF_SAMPLE_TOO_SMALL" in trend.reasons
    assert any(reason.startswith("THIN_IN_") for reason in trend.reasons)


def test_a_strategy_pairing_needs_the_same_out_of_sample_evidence() -> None:
    rows = history("TREND_UP", 72)
    built = build(rows)
    # trend_follow agrees with the selection in TREND_UP and abstains everywhere else.
    evaluations = [
        fixtures.evaluation(
            row,
            "trend_follow_v1",
            row.direction if row.primaryRegime == "TREND_UP" else "SKIP",
        )
        for row in rows
    ]
    snapshot = AnalyticsEngine().analyze(build(rows, evaluations))
    pairing = finding(snapshot.research, "STRATEGY_REGIME", "trend_follow_v1 in TREND_UP")
    assert pairing.stable is True
    assert (pairing.trainCount, pairing.validationCount, pairing.testCount) == (90, 30, 30)
    assert built.rows, "the same history is analysed with and without the votes"

    decayed = history("TREND_UP", 85, only_early=True)
    fading = [
        fixtures.evaluation(
            row,
            "trend_follow_v1",
            row.direction if row.primaryRegime == "TREND_UP" else "SKIP",
        )
        for row in decayed
    ]
    later = AnalyticsEngine().analyze(build(decayed, fading))
    weak = finding(later.research, "STRATEGY_REGIME", "trend_follow_v1 in TREND_UP")
    assert weak.stable is False
    assert "DIRECTION_NOT_CONSISTENT" in weak.reasons


def test_every_finding_reports_its_periods_so_a_reader_can_check_the_claim() -> None:
    snapshot = AnalyticsEngine().analyze(build(*fixtures.corpus()))
    assert snapshot.research
    for item in snapshot.research:
        assert item.reasons, item.subject
        assert item.trainCount >= 0 and item.validationCount >= 0 and item.testCount >= 0
        if item.stable:
            assert item.trainCount >= SETTINGS.minDisplaySample
            assert item.validationCount >= SETTINGS.minDisplaySample
            assert item.testCount >= SETTINGS.minDisplaySample
