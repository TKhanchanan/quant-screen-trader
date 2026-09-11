"""T-CJ, T-CK, T-CL, T-CM, T-G: what reaches the analysis, and what is counted instead.

The durable record is append-only and is read back in whatever order the filesystem hands the
files over, so the dataset builder has three jobs before a single number is computed: collapse
each trade to the state it actually ended in, refuse anything produced under a contract this
version does not understand, and sort what remains by market time.

The data-quality report is asserted as hard as the metrics are. A win rate over the fifth of
decisions that happened to resolve is not a win rate, and the resolution rate is the only thing
that lets a reader tell the two apart.
"""

from __future__ import annotations

from collections.abc import Callable

import analytics_fixtures as fixtures
from quant_engine.analytics import (
    ANALYTICS_VERSION,
    AnalyticsEngine,
    AnalyticsFilters,
    AnalyticsSettings,
    build,
    fingerprint,
    snapshot_id,
    supported,
    terminal_rows,
)
from quant_engine.analytics.models import AnalyticsRow

# --- T-D the canonical input -----------------------------------------------------------


def test_only_resolved_outcomes_enter_the_analysis() -> None:
    trades = [
        fixtures.trade("ok", outcome="WIN", expiry=fixtures.BASE_MS),
        fixtures.trade("pending", status="PENDING_ENTRY", expiry=fixtures.BASE_MS + 1),
        fixtures.trade("open", status="OPEN", expiry=fixtures.BASE_MS + 2),
        fixtures.trade("cancelled", status="CANCELLED", expiry=fixtures.BASE_MS + 3),
        fixtures.trade("invalid", status="INVALID", expiry=fixtures.BASE_MS + 4),
    ]
    dataset = build(trades)
    assert len(dataset.rows) == 1
    assert dataset.rows[0].outcome == "WIN"
    quality = dataset.quality
    assert (quality.pendingEntry, quality.open, quality.cancelled, quality.invalid) == (1, 1, 1, 1)
    assert quality.eligibleTrades == 5


def test_a_cancelled_trade_is_never_counted_as_a_loss() -> None:
    dataset = build(
        [
            fixtures.trade("w", outcome="WIN", expiry=fixtures.BASE_MS),
            fixtures.trade(
                "c", status="CANCELLED", reasons=["CONTEXT_CHANGED"], expiry=fixtures.BASE_MS + 1
            ),
        ]
    )
    snapshot = AnalyticsEngine().analyze(dataset)
    assert snapshot.overallMetrics.losses == 0
    assert snapshot.overallMetrics.winRateExcludingDraws == 1.0
    assert snapshot.quality.contextCancellations == 1


# --- T-G the data quality report -------------------------------------------------------


def test_the_resolution_rate_is_reported_before_any_performance_claim() -> None:
    trades = [
        *[fixtures.trade(f"r-{i}", outcome="WIN", expiry=fixtures.BASE_MS + i) for i in range(2)],
        *[
            fixtures.trade(f"u-{i}", status="PENDING_ENTRY", expiry=fixtures.BASE_MS + 100 + i)
            for i in range(8)
        ],
    ]
    snapshot = AnalyticsEngine().analyze(build(trades))
    assert snapshot.quality.resolvedRate == 0.2
    assert "LOW_RESOLUTION_RATE" in snapshot.warnings


def test_timeouts_are_counted_from_the_reasons_the_paper_layer_recorded() -> None:
    quality = build(
        [
            fixtures.trade(
                "t1", status="INVALID", invalidReasons=["ENTRY_TIMEOUT"], expiry=fixtures.BASE_MS
            ),
            fixtures.trade(
                "t2",
                status="INVALID",
                invalidReasons=["RESOLUTION_TIMEOUT"],
                expiry=fixtures.BASE_MS + 1,
            ),
        ]
    ).quality
    assert quality.entryTimeouts == 1
    assert quality.resolutionTimeouts == 1
    assert quality.resolvedRate == 0.0


def test_a_row_claiming_resolved_without_its_timestamps_is_malformed_not_an_outcome() -> None:
    broken = fixtures.trade("broken", outcome="WIN", expiry=fixtures.BASE_MS).model_copy(
        update={"expiryTime": None}
    )
    dataset = build([broken])
    assert dataset.rows == ()
    assert dataset.quality.malformed == 1
    assert dataset.quality.resolved == 0


# --- T-CJ version mixing ---------------------------------------------------------------


def test_an_unsupported_contract_is_separated_and_never_silently_pooled() -> None:
    supported_row = fixtures.trade("v1", outcome="WIN", expiry=fixtures.BASE_MS)
    future = fixtures.trade("v2", outcome="LOSS", expiry=fixtures.BASE_MS + 1).model_copy(
        update={"paperVersion": "qst-paper-v2"}
    )
    dataset = build([supported_row, future])
    assert len(dataset.rows) == 1
    assert dataset.rows[0].paperVersion == "qst-paper-v1"
    assert dataset.quality.unsupportedVersions == 1
    assert any("qst-paper-v2" in label for label in dataset.quality.unsupportedVersionLabels)
    assert len(dataset.quality.versionsObserved) == 2
    assert "VERSION_MIXED" in AnalyticsEngine().analyze(dataset).warnings


def test_every_upstream_contract_is_checked_and_not_only_the_paper_one() -> None:
    base = fixtures.trade("full", outcome="WIN", expiry=fixtures.BASE_MS)
    assert supported(base) is True
    for field in (
        "featureVersion",
        "regimeVersion",
        "strategyVersion",
        "rankingVersion",
        "paperVersion",
    ):
        assert supported(base.model_copy(update={field: "something-else"})) is False


# --- T-CK dataset determinism ----------------------------------------------------------


def test_the_same_trades_in_a_different_storage_order_produce_the_same_rows() -> None:
    trades, evaluations = fixtures.corpus()
    forward = build(trades, evaluations)
    backward = build(list(reversed(trades)), list(reversed(evaluations)))
    assert forward.rows == backward.rows
    assert forward.fingerprint == backward.fingerprint
    assert forward.sampleStart == backward.sampleStart


def test_only_the_state_a_trade_ended_in_reaches_the_analysis() -> None:
    # Parquet holds one row per lifecycle transition, so one finished trade arrives three times.
    resolved = fixtures.trade("lifecycle", outcome="WIN", expiry=fixtures.BASE_MS)
    pending = resolved.model_copy(
        update={"status": "PENDING_ENTRY", "outcome": "UNRESOLVED", "entryPrice": None}
    )
    opened = resolved.model_copy(update={"status": "OPEN", "outcome": "UNRESOLVED"})
    collapsed = terminal_rows([opened, resolved, pending])
    assert len(collapsed) == 1
    assert collapsed[0].status == "RESOLVED"
    dataset = build([opened, resolved, pending])
    assert len(dataset.rows) == 1
    assert dataset.quality.totalTrades == 1


def test_rows_are_ordered_by_market_time_and_then_by_identity() -> None:
    same_instant = [fixtures.trade(f"tie-{index}", expiry=fixtures.BASE_MS) for index in range(5)]
    first = build(same_instant).rows
    second = build(list(reversed(same_instant))).rows
    assert [row.paperTradeId for row in first] == [row.paperTradeId for row in second]
    assert [str(row.paperTradeId) for row in first] == sorted(
        str(row.paperTradeId) for row in first
    )


# --- T-CM the fingerprint --------------------------------------------------------------


def test_changing_one_outcome_changes_the_fingerprint() -> None:
    trades = fixtures.sequence(fixtures.outcomes("WL", 20))
    before = build(trades).fingerprint
    altered = [*trades[:-1], trades[-1].model_copy(update={"outcome": "DRAW"})]
    assert build(altered).fingerprint != before


def test_the_fingerprint_ignores_nothing_a_metric_depends_on() -> None:
    trades = fixtures.sequence(fixtures.outcomes("W", 10))
    base = build(trades).fingerprint
    for field, value in (
        ("rankScore", 0.99),
        ("ensembleConfidence", 0.11),
        ("agreement", 0.22),
        ("regimeConfidence", 0.33),
        ("leadMargin", None),
        ("primaryRegime", "NOISY"),
        ("priceDeltaBps", 99.0),
        ("realizedPaperPnl", 1.0),
    ):
        altered = [*trades[:-1], trades[-1].model_copy(update={field: value})]
        assert build(altered).fingerprint != base, field


def test_an_empty_history_still_has_a_stable_fingerprint() -> None:
    assert fingerprint(()) == build([]).fingerprint
    assert build([]).fingerprint == build([]).fingerprint


# --- T-CL snapshot determinism ---------------------------------------------------------


def test_the_same_history_and_settings_produce_the_same_snapshot_identity() -> None:
    trades, evaluations = fixtures.corpus()
    first = AnalyticsEngine().analyze(build(trades, evaluations))
    second = AnalyticsEngine().analyze(build(list(reversed(trades)), evaluations))
    assert first.snapshotId == second.snapshotId
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.analyticsVersion == ANALYTICS_VERSION


def test_different_settings_produce_a_different_snapshot_over_the_same_history() -> None:
    dataset = build(*fixtures.corpus())
    coarse = AnalyticsEngine(AnalyticsSettings(binCount=4)).analyze(dataset)
    fine = AnalyticsEngine(AnalyticsSettings(binCount=10)).analyze(dataset)
    assert coarse.snapshotId != fine.snapshotId
    assert coarse.datasetFingerprint == fine.datasetFingerprint
    assert coarse.settingsFingerprint != fine.settingsFingerprint


def test_a_filtered_view_is_its_own_snapshot_rather_than_a_slice_of_another() -> None:
    trades, evaluations = fixtures.corpus()
    filters = AnalyticsFilters(platform="iqoption")
    whole = build(trades, evaluations)
    part = build(trades, evaluations, filters=filters)
    assert part.quality.totalTrades < whole.quality.totalTrades
    assert all(row.platform == "iqoption" for row in part.rows)
    snapshot = AnalyticsEngine().analyze(part, filters)
    assert snapshot.filters.platform == "iqoption"
    assert snapshot.snapshotId != AnalyticsEngine().analyze(whole).snapshotId
    assert snapshot_id(part.fingerprint, snapshot.settingsFingerprint) == snapshot.snapshotId


def test_every_filter_field_narrows_the_population_it_claims_to() -> None:
    trades, evaluations = fixtures.corpus()
    late = fixtures.BASE_MS + 300 * fixtures.STEP_MS
    early = fixtures.BASE_MS + 100 * fixtures.STEP_MS
    cases: list[tuple[AnalyticsFilters, Callable[[AnalyticsRow], bool]]] = [
        (AnalyticsFilters(direction="UP"), lambda row: row.direction == "UP"),
        (AnalyticsFilters(regime="NOISY"), lambda row: row.primaryRegime == "NOISY"),
        (AnalyticsFilters(assetName="Gold OTC"), lambda row: row.assetName == "Gold OTC"),
        (AnalyticsFilters(fromTime=late), lambda row: row.expiryTime >= late),
        (AnalyticsFilters(toTime=early), lambda row: row.expiryTime <= early),
    ]
    for filters, check in cases:
        rows = build(trades, evaluations, filters=filters).rows
        assert rows, filters
        assert all(check(row) for row in rows), filters
