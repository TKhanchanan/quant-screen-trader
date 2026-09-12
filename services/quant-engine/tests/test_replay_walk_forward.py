"""T-DZ..T-EF: walk-forward validation, and the leaks it exists to prevent.

Walk-forward is where a backtest is most easily flattered. Four failures are asserted against
directly: a split that is not chronological, a trade whose outcome straddles a boundary, a
candidate chosen with the test period in view, and a floor quietly lowered until something was
found.

Rows are built here rather than replayed, for the same reason Phase 10's calibration tests build
them: the properties this module has to be proved against — a genuinely separable score band, a
currency that changes mid-record, a test period whose outcomes are wrong on purpose — cannot be
steered out of a price series on demand. The replay tests beside this one drive the real pipeline
end to end and hand whatever it produced to exactly this code.
"""

from __future__ import annotations

from typing import Any

import analytics_fixtures as base
from quant_engine.analytics import AnalyticsSettings, build
from quant_engine.analytics.models import AnalyticsRow
from quant_engine.paper.models import PaperTrade
from quant_engine.paper.policy import PaperSettings
from quant_engine.replay import WalkForwardSettings, analyse, embargo_for, plan_count, select
from quant_engine.replay.walk_forward import _expiries, plan_duration

STEP_MS = 10_000
ANALYTICS = AnalyticsSettings()
PAPER = PaperSettings()
CAPITALBEAR_EMBARGO = 15_000
"""Five seconds of entry bound, five of horizon, five of resolution lag: the longest a
CapitalBear selection can stay alive, and therefore the narrowest honest embargo."""


def corpus(
    count: int = 600,
    *,
    start: int = base.BASE_MS,
    strong_from: int = 0,
    currency_switch: tuple[int, str] | None = None,
    flip_from: int | None = None,
) -> list[PaperTrade]:
    """A record where a high rank score really is better, laid out in market time.

    The separation is deliberate and fixed: rows at or above 0.75 win four times in five, rows
    below win a little less than half the time. A search that cannot find that is broken; a
    search that finds it in the training period and then reports it as stable without it holding
    later is worse.
    """
    rows: list[PaperTrade] = []
    for index in range(count):
        expiry = start + index * STEP_MS
        strong = index % 5 < 2 and index >= strong_from
        score = 0.80 if strong else 0.40
        wins = (index % 5 != 4) if strong else (index % 5 == 0)
        if flip_from is not None and index >= flip_from:
            wins = not wins
        currency = "THB"
        if currency_switch is not None and index >= currency_switch[0]:
            currency = currency_switch[1]
        rows.append(
            base.trade(
                f"wf-{index}",
                outcome="WIN" if wins else "LOSS",
                rank_score=score,
                confidence=0.75 if strong else 0.35,
                agreement=0.7 if strong else 0.3,
                lead_margin=0.2 if strong else 0.05,
                expiry=expiry,
                currency=currency,
            )
        )
    return rows


def rows_of(trades: list[PaperTrade]) -> tuple[AnalyticsRow, ...]:
    return build(trades, settings=ANALYTICS).rows


def counted(**overrides: Any) -> WalkForwardSettings:
    values: dict[str, Any] = {"mode": "COUNT", "foldCount": 2, "trainSegments": 3}
    values.update(overrides)
    return WalkForwardSettings(**values)


def dated(**overrides: Any) -> WalkForwardSettings:
    values: dict[str, Any] = {
        "mode": "DURATION",
        "trainingDurationMs": 2_000_000,
        "validationDurationMs": 600_000,
        "testDurationMs": 600_000,
        "stepDurationMs": 800_000,
    }
    values.update(overrides)
    return WalkForwardSettings(**values)


def study(trades: list[PaperTrade], settings: WalkForwardSettings) -> Any:
    return analyse(
        rows_of(trades),
        settings=settings,
        analytics=ANALYTICS,
        platforms=("capitalbear",),
        paper=PAPER,
    )


# --- T-DZ chronology -------------------------------------------------------------------


def test_the_embargo_is_derived_from_the_paper_contract_not_chosen() -> None:
    assert embargo_for(("capitalbear",), PAPER) == CAPITALBEAR_EMBARGO
    assert embargo_for(("iqoption",), PAPER) == 80_000
    assert embargo_for(("capitalbear", "iqoption"), PAPER) == 80_000


def test_every_fold_runs_train_then_validation_then_test_and_never_overlaps() -> None:
    summary = study(corpus(), counted())
    assert summary.folds >= 1
    assert summary.mode == "COUNT"
    for fold in summary.rows:
        assert fold.trainStart < fold.trainEnd
        assert fold.validationStart > fold.trainEnd
        assert fold.validationEnd > fold.validationStart
        assert fold.testStart > fold.validationEnd
        assert fold.testEnd > fold.testStart
        # The gap between periods is the embargo, present by construction rather than by luck.
        assert fold.validationStart - fold.trainEnd == fold.embargoMs
        assert fold.testStart - fold.validationEnd == fold.embargoMs


def test_folds_roll_forward_and_never_shuffle() -> None:
    summary = study(corpus(), counted(foldCount=3))
    starts = [fold.trainStart for fold in summary.rows]
    assert starts == sorted(starts)
    assert len(set(starts)) == len(starts)


def test_rolling_calendar_windows_step_by_the_configured_duration() -> None:
    rows = rows_of(corpus())
    settings = dated()
    planned = plan_duration(rows, settings, CAPITALBEAR_EMBARGO)
    assert len(planned) >= 2
    assert planned[1].trainStart - planned[0].trainStart == settings.stepDurationMs
    for window in planned:
        assert window.trainEnd - window.trainStart == settings.trainingDurationMs
        assert window.validationStart - window.trainEnd == CAPITALBEAR_EMBARGO


# --- T-EA purge ------------------------------------------------------------------------


def test_a_trade_decided_before_a_window_opened_is_purged_from_it() -> None:
    trades = corpus(60)
    rows = rows_of(trades)
    expiries = _expiries(rows)
    # A window that opens exactly on a row's expiry: that row's decision was taken six seconds
    # earlier, on the other side of the cutoff, so it may not be counted inside.
    start = rows[10].expiryTime
    selection = select(rows, expiries, start, rows[30].expiryTime)
    assert selection.purged == 1
    assert all(row.boardAsOf >= start for row in selection.rows)
    assert rows[10] not in selection.rows


def test_training_evidence_only_contains_outcomes_that_resolved_inside_the_window() -> None:
    rows = rows_of(corpus(80))
    expiries = _expiries(rows)
    start, end = rows[10].expiryTime, rows[40].expiryTime
    selection = select(rows, expiries, start, end)
    assert selection.rows
    for row in selection.rows:
        assert start <= row.expiryTime <= end
        assert row.boardAsOf >= start


def test_every_fold_reports_what_the_purge_and_the_embargo_removed() -> None:
    summary = study(corpus(), counted())
    assert summary.rows
    assert all(fold.purgeMs == CAPITALBEAR_EMBARGO for fold in summary.rows)
    assert any(fold.purgedRows > 0 for fold in summary.rows)
    assert any(fold.embargoedRows > 0 for fold in summary.rows)


# --- T-EB embargo ----------------------------------------------------------------------


def test_no_outcome_can_appear_on_both_sides_of_a_fold_boundary() -> None:
    rows = rows_of(corpus())
    expiries = _expiries(rows)
    for window in plan_count(rows, counted(), CAPITALBEAR_EMBARGO):
        train = select(rows, expiries, window.trainStart, window.trainEnd)
        validation = select(rows, expiries, window.validationStart, window.validationEnd)
        test = select(rows, expiries, window.testStart, window.testEnd)
        ids = [{str(row.paperTradeId) for row in part.rows} for part in (train, validation, test)]
        assert ids[0].isdisjoint(ids[1])
        assert ids[1].isdisjoint(ids[2])
        assert ids[0].isdisjoint(ids[2])
        # And no trade in a later period was still running when the earlier one was cut.
        assert all(row.boardAsOf > window.trainEnd for row in validation.rows)
        assert all(row.boardAsOf > window.validationEnd for row in test.rows)


def test_rows_inside_the_embargo_interval_belong_to_no_period_at_all() -> None:
    rows = rows_of(corpus())
    expiries = _expiries(rows)
    window = plan_count(rows, counted(), CAPITALBEAR_EMBARGO)[0]
    inside = [row for row in rows if window.trainEnd < row.expiryTime < window.validationStart]
    assert inside, "the fixture must actually place outcomes in the gap"
    train = select(rows, expiries, window.trainStart, window.trainEnd)
    validation = select(rows, expiries, window.validationStart, window.validationEnd)
    kept = {str(row.paperTradeId) for row in (*train.rows, *validation.rows)}
    assert all(str(row.paperTradeId) not in kept for row in inside)


# --- T-EC the test period never chooses anything ---------------------------------------


def test_the_search_finds_a_genuinely_separable_band() -> None:
    summary = study(corpus(), counted())
    assert summary.foldsWithCandidate >= 1
    fold = next(item for item in summary.rows if item.candidateThreshold is not None)
    assert fold.candidateThreshold is not None and fold.candidateThreshold <= 0.80
    assert fold.train.lift is not None and fold.train.lift > 0
    assert fold.candidateSourceSnapshotId is not None


def test_rewriting_the_test_period_cannot_change_the_candidate_chosen_from_training() -> None:
    # The threshold is frozen the moment TRAIN has been searched. If TEST could move it, the
    # out-of-sample result would be a measurement of itself.
    settings = counted()
    baseline = study(corpus(), settings)
    ruined = study(corpus(flip_from=480), settings)
    chosen = [(fold.candidateMetric, fold.candidateThreshold) for fold in baseline.rows]
    after = [(fold.candidateMetric, fold.candidateThreshold) for fold in ruined.rows]
    assert chosen[0] == after[0]
    assert (
        baseline.rows[0].train.winRateExcludingDraws == ruined.rows[0].train.winRateExcludingDraws
    )
    # And the damage shows up where it belongs: in the out-of-sample result.
    assert (
        baseline.rows[-1].test.winRateExcludingDraws != ruined.rows[-1].test.winRateExcludingDraws
    )


def test_appending_future_history_leaves_an_earlier_calendar_fold_untouched() -> None:
    settings = dated()
    short = study(corpus(400), settings)
    long = study(corpus(600), settings)
    assert short.folds >= 1
    first, also = short.rows[0], long.rows[0]
    assert (first.trainStart, first.trainEnd) == (also.trainStart, also.trainEnd)
    assert (first.candidateMetric, first.candidateThreshold) == (
        also.candidateMetric,
        also.candidateThreshold,
    )
    assert first.test.winRateExcludingDraws == also.test.winRateExcludingDraws
    assert first.candidateSourceSnapshotId == also.candidateSourceSnapshotId


# --- T-ED no candidate is ever forced --------------------------------------------------


def test_a_record_with_no_separable_band_returns_no_candidate() -> None:
    # Every score identical, so there is nothing to find. The honest answer is nothing found.
    trades = [
        base.trade(
            f"flat-{index}",
            outcome="WIN" if index % 2 else "LOSS",
            rank_score=0.5,
            confidence=0.5,
            agreement=0.5,
            lead_margin=0.1,
            expiry=base.BASE_MS + index * STEP_MS,
        )
        for index in range(600)
    ]
    summary = study(trades, counted())
    assert summary.foldsWithCandidate == 0
    assert "NO_STABLE_CANDIDATE" in summary.warnings
    assert summary.candidateStability == "UNTESTED"


def test_a_thin_record_is_not_rescued_by_lowering_the_floor() -> None:
    summary = study(corpus(60), counted())
    assert summary.foldsWithCandidate == 0
    assert "NO_STABLE_CANDIDATE" in summary.warnings
    # The floor is Phase 10's own, unchanged. Nothing in this layer may move it.
    assert ANALYTICS.minRecommendationSample == 50


def test_too_few_folds_is_reported_rather_than_hidden() -> None:
    summary = study(corpus(), counted(foldCount=1))
    assert summary.folds == 1
    assert "INSUFFICIENT_FOLDS" in summary.warnings


# --- T-EE money ------------------------------------------------------------------------


def test_a_currency_change_between_periods_is_never_called_stable_money_evidence() -> None:
    summary = study(corpus(currency_switch=(360, "USD")), counted())
    assert summary.foldsWithCandidate >= 1
    fold = next(item for item in summary.rows if item.candidateThreshold is not None)
    assert fold.monetaryStable is False
    assert fold.monetaryVerdict == "UNTESTED"
    assert "MONETARY_UNVERIFIED" in fold.warnings
    assert "MONETARY_UNVERIFIED" in summary.warnings


def test_money_is_only_called_stable_in_one_currency_with_nothing_excluded() -> None:
    summary = study(corpus(), counted())
    fold = next(item for item in summary.rows if item.candidateThreshold is not None)
    for period in (fold.train, fold.validation, fold.test):
        assert period.currency == "THB"
        assert period.mixedCurrency is False
        assert period.excludedByCurrency == 0


# --- T-EF the summary ------------------------------------------------------------------


def test_the_summary_reports_every_fold_and_promotes_none_of_them() -> None:
    summary = study(corpus(), counted(foldCount=3))
    assert summary.folds == len(summary.rows)
    assert summary.totalComparisons > 0
    assert "MULTIPLE_TESTING_WARNING" in summary.warnings
    # There is deliberately no best-fold field anywhere on this model.
    assert not [name for name in summary.model_dump() if "best" in name.casefold()]
    assert summary.appliedToLiveExecution is False
    assert all(fold.appliedToLiveExecution is False for fold in summary.rows)


def test_a_threshold_that_moves_between_folds_is_reported_as_unstable() -> None:
    # Two different regimes of the same record: the band that separates outcomes early is not
    # the band that separates them late, and a summary that averaged them would hide it.
    summary = study(corpus(strong_from=300), counted(foldCount=3))
    if summary.thresholdSpread is not None and summary.thresholdSpread > 0.10:
        assert "PARAMETER_INSTABILITY" in summary.warnings
        assert summary.candidateStability == "UNSTABLE"
