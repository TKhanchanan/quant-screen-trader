"""Outcome analytics and score calibration (Phase 10).

Consumes resolved Phase 9 paper outcomes and answers one question: *did the scores produced by
Phases 7 and 8 correspond to better outcomes?* It groups recorded results by score band, regime,
strategy, asset, platform, hour and weekday, reports where higher scores did and did not come
with better results, and searches recorded history for thresholds that would have separated
outcomes — on a chronological train split, evaluated out of sample, and marked unstable when
they do not hold up.

**It measures. It does not act.** Nothing in this package changes a Phase 6 formula, a Phase 7
threshold, a Phase 8 gate, a Phase 9.5 limit or an execution setting, and there is no code path
from any of it to one. A discovered threshold is a research output; Phase 10 ships no way to
apply one, no button that would, and no consumer that reads one. Tests assert that the package
binds no execution, session-guard or stake name and imports nothing that could reach a broker.

Two words are used carefully. ``rankScore`` and ``ensembleConfidence`` were produced by layers
that had never observed an outcome, so a bin's empirical win rate is an *outcome curve* and not
a *probability calibration* — which is also why there is deliberately no Brier score anywhere in
this package.
"""

from quant_engine.analytics.calibration import (
    bin_index,
    calibration_report,
    monotonicity,
    score_bins,
)
from quant_engine.analytics.dataset import (
    EXPORT_COLUMNS,
    GENERATED_FROM,
    build,
    export_mapping,
    fingerprint,
    selects,
    settings_fingerprint,
    supported,
    terminal_rows,
)
from quant_engine.analytics.engine import AnalyticsEngine, research_findings, snapshot_id
from quant_engine.analytics.metrics import (
    bootstrap_mean,
    correlation_for,
    money_metrics,
    outcome_metrics,
    sample_label,
    spearman,
    wilson_interval,
)
from quant_engine.analytics.models import (
    ANALYTICS_VERSION,
    MIN_ASSET_SAMPLE,
    MIN_DISPLAY_SAMPLE,
    MIN_RECOMMENDATION_SAMPLE,
    SUPPORTED_FEATURE_VERSION,
    SUPPORTED_PAPER_VERSION,
    SUPPORTED_RANKING_VERSION,
    SUPPORTED_REGIME_VERSION,
    SUPPORTED_STRATEGY_VERSION,
    WARNING_CODES,
    AnalyticsDataset,
    AnalyticsFilters,
    AnalyticsRow,
    AnalyticsSettings,
    AnalyticsSnapshot,
    CalibrationReport,
    Correlation,
    DataQualityReport,
    Matrix,
    MatrixCell,
    MoneyMetrics,
    OutcomeMetrics,
    ResearchFinding,
    ScoreBin,
    SegmentMetrics,
    SplitMetrics,
    StrategyContribution,
    StrategyVoteRow,
    TemporalSplitReport,
    ThresholdCandidate,
)
from quant_engine.analytics.segmentation import (
    REGIMES,
    WEEKDAYS,
    by_asset,
    by_hour,
    by_platform,
    by_quality,
    by_regime,
    by_weekday,
    rank_confidence_matrix,
    regime_direction_matrix,
    strategy_contributions,
    strategy_regime_matrix,
)
from quant_engine.analytics.service import AnalyticsService, HistoryLoader, SnapshotSink
from quant_engine.analytics.thresholds import (
    METRICS,
    TemporalSplit,
    chronological_split,
    discover,
    evaluate,
    folds,
    split_report,
)

__all__ = [
    "ANALYTICS_VERSION",
    "EXPORT_COLUMNS",
    "GENERATED_FROM",
    "METRICS",
    "MIN_ASSET_SAMPLE",
    "MIN_DISPLAY_SAMPLE",
    "MIN_RECOMMENDATION_SAMPLE",
    "REGIMES",
    "SUPPORTED_FEATURE_VERSION",
    "SUPPORTED_PAPER_VERSION",
    "SUPPORTED_RANKING_VERSION",
    "SUPPORTED_REGIME_VERSION",
    "SUPPORTED_STRATEGY_VERSION",
    "WARNING_CODES",
    "WEEKDAYS",
    "AnalyticsDataset",
    "AnalyticsEngine",
    "AnalyticsFilters",
    "AnalyticsRow",
    "AnalyticsService",
    "AnalyticsSettings",
    "AnalyticsSnapshot",
    "CalibrationReport",
    "Correlation",
    "DataQualityReport",
    "HistoryLoader",
    "Matrix",
    "MatrixCell",
    "MoneyMetrics",
    "OutcomeMetrics",
    "ResearchFinding",
    "ScoreBin",
    "SegmentMetrics",
    "SnapshotSink",
    "SplitMetrics",
    "StrategyContribution",
    "StrategyVoteRow",
    "TemporalSplit",
    "TemporalSplitReport",
    "ThresholdCandidate",
    "bin_index",
    "bootstrap_mean",
    "build",
    "by_asset",
    "by_hour",
    "by_platform",
    "by_quality",
    "by_regime",
    "by_weekday",
    "calibration_report",
    "chronological_split",
    "correlation_for",
    "discover",
    "evaluate",
    "export_mapping",
    "fingerprint",
    "folds",
    "money_metrics",
    "monotonicity",
    "outcome_metrics",
    "rank_confidence_matrix",
    "regime_direction_matrix",
    "research_findings",
    "sample_label",
    "score_bins",
    "selects",
    "settings_fingerprint",
    "snapshot_id",
    "spearman",
    "split_report",
    "strategy_contributions",
    "strategy_regime_matrix",
    "supported",
    "terminal_rows",
    "wilson_interval",
]
