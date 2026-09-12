"""T-EG..T-EM: latency, payout, equity, coverage and the session-guard sandbox.

All research, and all descriptive. Nothing in this file picks a delay, sets a payout, removes an
asset or proposes a daily target — every one of those would be the backtest deciding something,
which is the line Phase 11 does not cross.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import analytics_fixtures as base
import replay_fixtures as fixtures
from quant_engine.analytics import AnalyticsSettings, build
from quant_engine.paper.policy import PaperSettings
from quant_engine.replay import (
    InMemoryObservationSource,
    ReplayEngine,
    ReplayManifest,
    ReplayResult,
    WindowedPaperEngine,
    contributions,
    coverage,
    daily,
    drawdown,
    equity,
    latency_report,
    payout_scenarios,
    rolling,
    run_replay,
    streaks,
)
from quant_engine.session_guard.settings import SessionGuardSettings

ACCOUNTING = PaperSettings(paperCurrency="THB", paperStake=50, paperPayoutRate=0.82)
ANALYTICS = AnalyticsSettings()


def spec(**overrides: Any) -> ReplayManifest:
    values: dict[str, Any] = {
        "warmupDurationMs": 0,
        "sourceMode": "SYNTHETIC",
        "includeIqOption": False,
        "paperSettings": ACCOUNTING,
    }
    values.update(overrides)
    return ReplayManifest(**values)


def run(rows: list[Any], root: Path, *, delay: int = 0, **overrides: Any) -> ReplayResult:
    manifest = spec(**overrides)
    source = InMemoryObservationSource(rows, mode="SYNTHETIC", platforms=manifest.platforms)
    return ReplayEngine(manifest, source, root=root, persist=False, decision_delay_ms=delay).run()


def rows_from(trades: list[Any]) -> tuple[Any, ...]:
    return build(trades, settings=ANALYTICS).rows


# --- T-EG latency ----------------------------------------------------------------------


def test_a_research_delay_moves_the_entry_and_nothing_else(tmp_path: Path) -> None:
    history = fixtures.session(seconds=420, tag="lat")
    baseline = run(history, tmp_path / "a")
    delayed = run(history, tmp_path / "b", delay=500)
    for trade in delayed.trades:
        if trade.entryTime is not None:
            # The decision was not actionable until half a second later, and no earlier price
            # may fill it.
            assert trade.entryTime >= trade.decisionAvailableAt
            assert trade.decisionAvailableAt >= trade.boardAsOf
    original = {
        (trade.boardAsOf, trade.slotId): trade.decisionAvailableAt for trade in baseline.trades
    }
    shifted = {
        (trade.boardAsOf, trade.slotId): trade.decisionAvailableAt for trade in delayed.trades
    }
    assert original and set(original) == set(shifted)
    assert all(shifted[key] == value + 500 for key, value in original.items())


def test_a_research_delay_cannot_change_the_analysis_that_produced_the_signal(
    tmp_path: Path,
) -> None:
    # Phase 7's opinion and Phase 8's board are the same objects with the same identities in
    # every scenario. A latency study measures what the market did next, never what was decided.
    history = fixtures.session(seconds=420, tag="brain")
    baseline = run(history, tmp_path / "a")
    delayed = run(history, tmp_path / "b", delay=1_000)
    assert baseline.run.ensemblesProduced == delayed.run.ensemblesProduced
    assert baseline.run.boardsFinalized == delayed.run.boardsFinalized
    assert baseline.run.boardsSelected == delayed.run.boardsSelected
    assert {str(trade.paperTradeId) for trade in baseline.trades} == {
        str(trade.paperTradeId) for trade in delayed.trades
    }
    signals = {
        (trade.boardAsOf, trade.slotId, trade.direction, trade.rankScore, trade.primaryRegime)
        for trade in baseline.trades
    }
    assert signals == {
        (trade.boardAsOf, trade.slotId, trade.direction, trade.rankScore, trade.primaryRegime)
        for trade in delayed.trades
    }


def test_the_latency_table_reports_every_delay_beside_the_zero_delay_baseline(
    tmp_path: Path,
) -> None:
    history = fixtures.session(seconds=420, tag="table")
    baseline = run(history, tmp_path / "base")
    scenarios = [
        (delay, run(history, tmp_path / str(delay), delay=delay)) for delay in (250, 1_000)
    ]
    table = latency_report(baseline, scenarios)
    assert [row.delayMs for row in table] == [0, 250, 1_000]
    assert table[0].winRateDelta is None  # the baseline is not compared against itself
    assert all(row.simulationOnly is True for row in table)
    assert all(row.selections == baseline.run.boardsSelected for row in table)


# --- T-EH payout -----------------------------------------------------------------------


def test_payout_scenarios_reprice_recorded_outcomes_without_changing_one_direction() -> None:
    rows = rows_from(
        [
            base.trade(
                f"pay-{index}",
                outcome="WIN" if index % 3 else "LOSS",
                expiry=base.BASE_MS + index * 1_000,
            )
            for index in range(60)
        ]
    )
    scenarios = payout_scenarios(rows, [0.70, 0.80, 0.90])
    assert [row.payoutRate for row in scenarios] == [0.70, 0.80, 0.90]
    for row in scenarios:
        assert row.resolved == len(rows)
        assert row.breakEvenWinRate is not None
        # A binary at net payout p needs 1 / (1 + p) of its trades to be right to break even.
        assert abs(row.breakEvenWinRate - 1 / (1 + row.payoutRate)) < 1e-12
        assert row.currency == "THB"
    # Higher payout, same trades, better expectancy. Nothing about the trades moved.
    values = [row.expectancyPerTrade for row in scenarios]
    assert all(value is not None for value in values)
    assert values == sorted(values)  # type: ignore[type-var]


def test_payout_research_is_silent_without_an_explicit_stake() -> None:
    rows = rows_from(
        [
            base.trade(
                f"free-{index}",
                stake=None,
                payout=None,
                currency=None,
                expiry=base.BASE_MS + index * 1_000,
            )
            for index in range(40)
        ]
    )
    assert payout_scenarios(rows, [0.8]) == []
    assert payout_scenarios(rows, []) == []


# --- T-EI equity and drawdown ----------------------------------------------------------


def test_the_equity_path_is_cumulative_and_labelled_as_a_simulation() -> None:
    rows = rows_from(
        [
            base.trade(
                f"eq-{index}",
                outcome="WIN" if index % 4 else "LOSS",
                expiry=base.BASE_MS + index * 1_000,
            )
            for index in range(40)
        ]
    )
    points = equity(rows)
    assert len(points) == len(rows)
    assert [point.settledAt for point in points] == sorted(point.settledAt for point in points)
    running = 0.0
    for point in points:
        running += point.pnl
        assert abs(point.cumulativePnl - running) < 1e-9
        assert point.drawdown >= 0
        assert point.peak >= point.cumulativePnl
        assert point.currency == "THB"


def test_a_relative_drawdown_is_unknown_without_a_stated_starting_balance() -> None:
    rows = rows_from(
        [
            base.trade(f"dd-{index}", outcome="LOSS", expiry=base.BASE_MS + index * 1_000)
            for index in range(10)
        ]
    )
    points = equity(rows)
    worst, relative = drawdown(points, None)
    assert worst == 500.0
    # No account was funded, so no fraction of one is reported.
    assert relative is None
    _, stated = drawdown(points, 2_000.0)
    assert stated == 0.25


def test_streaks_are_descriptive_and_a_draw_ends_a_run() -> None:
    outcomes = ["WIN", "WIN", "WIN", "DRAW", "WIN", "LOSS", "LOSS"]
    rows = rows_from(
        [
            base.trade(f"streak-{index}", outcome=outcome, expiry=base.BASE_MS + index * 1_000)
            for index, outcome in enumerate(outcomes)
        ]
    )
    assert streaks(rows) == (3, 2)


# --- T-EJ coverage and contribution ----------------------------------------------------


def test_coverage_counts_every_regime_the_history_was_in_not_only_the_traded_ones() -> None:
    rows = rows_from(
        [
            base.trade(f"cov-{index}", regime="TREND_UP", expiry=base.BASE_MS + index * 1_000)
            for index in range(30)
        ]
    )
    view = coverage(
        rows,
        regimes={"TREND_UP": 90, "RANGE": 10},
        hour_buckets=frozenset({480_000, 480_001}),
        timezone="Asia/Bangkok",
    )
    assert view.regimeCounts == {"TREND_UP": 90, "RANGE": 10}
    assert view.dominantRegime == "TREND_UP"
    assert view.dominantRegimeShare == 0.9
    assert view.topAsset == "EUR/USD OTC"
    assert view.topAssetShare == 1.0
    assert view.timezone == "Asia/Bangkok"


def test_contribution_is_reported_by_platform_asset_regime_and_direction() -> None:
    rows = rows_from(
        [
            base.trade(
                f"contrib-{index}",
                asset="EUR/USD OTC" if index % 2 else "Gold OTC",
                direction="UP" if index % 3 else "DOWN",
                regime="TREND_UP" if index % 2 else "RANGE",
                expiry=base.BASE_MS + index * 1_000,
            )
            for index in range(40)
        ]
    )
    table = contributions(rows)
    dimensions = {row.dimension for row in table}
    assert dimensions == {"PLATFORM", "ASSET", "REGIME", "DIRECTION"}
    for dimension in dimensions:
        shares = [row.share for row in table if row.dimension == dimension]
        assert abs(sum(shares) - 1.0) < 1e-9


def test_rolling_windows_describe_the_shape_of_the_record_over_time() -> None:
    rows = rows_from(
        [
            base.trade(
                f"roll-{index}",
                outcome="WIN" if index < 150 else "LOSS",
                expiry=base.BASE_MS + index * 1_000,
            )
            for index in range(300)
        ]
    )
    windows = rolling(rows, 100)
    assert len(windows) >= 3
    assert windows[0].winRateExcludingDraws == 1.0
    assert windows[-1].winRateExcludingDraws == 0.0
    assert [window.startTime for window in windows] == sorted(w.startTime for w in windows)


def test_the_daily_table_uses_an_explicit_timezone_and_makes_no_income_claim() -> None:
    rows = rows_from(
        [
            base.trade(
                f"day-{index}",
                outcome="WIN" if index % 3 else "LOSS",
                expiry=base.BASE_MS + index * 3_600_000,
            )
            for index in range(60)
        ]
    )
    table = daily(rows, ANALYTICS.timezone)
    assert table.days >= 2
    assert table.currency == "THB"
    assert table.profitableDays + table.losingDays + table.flatDays == table.days
    assert all(row.timezone == ANALYTICS.timezone for row in table.rows)


# --- T-EK the session-guard sandbox ----------------------------------------------------


def test_a_sandbox_guard_scenario_stops_new_replay_entries_when_its_limit_is_crossed(
    tmp_path: Path,
) -> None:
    history = fixtures.session(seconds=420, tag="guard")
    open_run = run(history, tmp_path / "open")
    assert open_run.run.paperOpened > 2
    engine = ReplayEngine(
        spec(
            sessionGuardScenario=SessionGuardSettings(
                enabled=True, dailyLossLimit=100.0, currency="THB", timezone="UTC"
            )
        ),
        InMemoryObservationSource(history, mode="SYNTHETIC", platforms=("capitalbear",)),
        root=tmp_path / "stopped",
        persist=False,
    )
    stopped = engine.run()
    # Two losing trades at a fifty-baht stake reach a hundred-baht limit, and the sandbox stops
    # taking entries for the rest of that trading day.
    assert stopped.run.paperOpened < open_run.run.paperOpened
    assert engine.engine is not None
    assert cast(WindowedPaperEngine, engine.engine.paper).guardBlocked > 0
    assert stopped.sessions
    assert any(session.lossLimitReachedAt is not None for session in stopped.sessions)
    assert any(session.canOpenNewEntry is False for session in stopped.sessions)


def test_the_guard_report_evaluates_a_configuration_and_never_searches_for_one(
    tmp_path: Path,
) -> None:
    report = run_replay(
        spec(
            sessionGuardScenario=SessionGuardSettings(
                enabled=True,
                dailyProfitTarget=100.0,
                dailyLossLimit=200.0,
                currency="THB",
                timezone="UTC",
            )
        ),
        market_data=tmp_path,
        factory=lambda manifest: InMemoryObservationSource(
            fixtures.session(seconds=420, tag="report"),
            mode="SYNTHETIC",
            platforms=manifest.platforms,
        ),
        persist=False,
    )
    guard = report.summary.sessionGuard
    assert guard is not None
    assert guard.enabled is True
    assert guard.optimized is False
    assert guard.daysEvaluated >= 1
    assert guard.daysTargetReached + guard.daysLossLimitReached + guard.daysNeitherReached == (
        guard.daysEvaluated
    )
    # Nothing in the report names a better target, and there is no field that could hold one.
    assert not [name for name in guard.model_dump() if "best" in name.casefold()]
    assert not [name for name in guard.model_dump() if "optimal" in name.casefold()]


def test_no_guard_scenario_means_no_guard_report(tmp_path: Path) -> None:
    report = run_replay(
        spec(),
        market_data=tmp_path,
        factory=lambda manifest: InMemoryObservationSource(
            fixtures.small_history(), mode="SYNTHETIC", platforms=manifest.platforms
        ),
        persist=False,
    )
    assert report.summary.sessionGuard is None
