"""The cached analysis of the durable record, and the one flag that keeps a read honest.

Rebuilding walks the whole Phase 9 Parquet history, which is far too slow to do inside a
diagnostics poll, so the loaded history is cached and the analysis is recomputed from memory.
While a rebuild is running the service says so rather than serving a half-built answer — the
same rule every other reader in this engine follows, for the same reason: a metric assembled
from a dataset that is still being replaced is not a smaller answer, it is a wrong one.

Nothing here writes to the Phase 9 record. The loader reads; the analysis is a pure function of
what it read; and the optional sink is handed a finished snapshot to persist beside the record,
never into it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from quant_engine.analytics.dataset import build
from quant_engine.analytics.engine import AnalyticsEngine
from quant_engine.analytics.models import (
    AnalyticsDataset,
    AnalyticsFilters,
    AnalyticsSettings,
    AnalyticsSnapshot,
)
from quant_engine.paper.models import PaperTrade
from quant_engine.strategy.models import StrategyEvaluation

type History = tuple[Sequence[PaperTrade], Sequence[StrategyEvaluation]]
type HistoryLoader = Callable[[], History]
type SnapshotSink = Callable[[AnalyticsSnapshot], None]


class AnalyticsService:
    """One instance owns the cached history, the cached unfiltered snapshot, and the busy flag."""

    def __init__(
        self,
        loader: HistoryLoader | None = None,
        *,
        settings: AnalyticsSettings | None = None,
        sink: SnapshotSink | None = None,
    ) -> None:
        self.engine = AnalyticsEngine(settings)
        self.loader = loader
        self.sink = sink
        self.rebuilding = False
        self.loaded = False
        self.loadError: str | None = None
        self.rebuilds = 0
        self.stale = False
        """Set the moment Phase 9 resolves an outcome the cached analysis has never seen.

        A cached snapshot is a statement about a record that has since changed, and serving it
        as if it were current is the quiet failure this flag exists to prevent: the panel would
        keep reporting yesterday's win rate through a whole session and give no sign of it. The
        flag costs one assignment on the ingestion path and the rebuild happens on the next
        read, off that thread."""
        self.pendingOutcomes = 0
        self.trades: Sequence[PaperTrade] = ()
        self.evaluations: Sequence[StrategyEvaluation] = ()
        self.dataset: AnalyticsDataset | None = None
        self.snapshot: AnalyticsSnapshot | None = None

    @property
    def settings(self) -> AnalyticsSettings:
        return self.engine.settings

    def mark_stale(self, outcomes: int = 1) -> None:
        """Record that the durable record has moved on. Called from the ingestion path.

        Deliberately does no work: a rebuild reads the whole Parquet history, and doing that
        inside the thread that is trying to keep up with nine live charts would make the
        analysis layer cost the capture layer its samples.
        """
        self.stale = True
        self.pendingOutcomes += max(outcomes, 0)

    def adopt(
        self, trades: Sequence[PaperTrade], evaluations: Sequence[StrategyEvaluation] = ()
    ) -> AnalyticsSnapshot:
        """Analyse an explicitly supplied history. The path tests and offline tools use."""
        self.trades = trades
        self.evaluations = evaluations
        self.loaded = True
        self.loadError = None
        self.stale = False
        self.pendingOutcomes = 0
        return self._recompute()

    def refresh(self) -> AnalyticsSnapshot | None:
        """Reload the durable record and recompute. Blocking; call it off the request thread.

        A failure leaves the previous analysis in place, names the reason, and leaves the stale
        flag up so the next read tries again. An unreadable history is a diagnostics outage, and
        replacing a real snapshot with an empty one would turn it into a claim that nothing ever
        happened.
        """
        if self.loader is None:
            return self.snapshot
        self.rebuilding = True
        try:
            self.trades, self.evaluations = self.loader()
            self.loaded = True
            self.loadError = None
            self.stale = False
            self.pendingOutcomes = 0
            return self._recompute()
        except Exception as error:
            self.loadError = f"Analytics history unreadable: {error}"[:200]
            return self.snapshot
        finally:
            self.rebuilding = False

    def _recompute(self) -> AnalyticsSnapshot:
        self.dataset = build(self.trades, self.evaluations, settings=self.settings)
        self.snapshot = self.engine.analyze(self.dataset)
        self.rebuilds += 1
        if self.sink is not None:
            try:
                self.sink(self.snapshot)
            except Exception:
                # Persistence is a convenience for a later phase, never a precondition for
                # answering the question that was asked.
                self.loadError = "Analytics snapshot could not be persisted"
        return self.snapshot

    @property
    def current(self) -> bool:
        """Whether the cached analysis still describes the record it was built from."""
        return self.loaded and not self.stale

    def view(self, filters: AnalyticsFilters | None = None) -> AnalyticsSnapshot | None:
        """The unfiltered snapshot, or a fresh one over the filtered slice of the same history.

        Filtering rebuilds from the cached trades rather than slicing the finished snapshot: a
        win rate over one platform is not a subset of a pooled win rate, and a data-quality
        report has to describe the same population as the metrics beside it.
        """
        if filters is None or filters.empty:
            return self.snapshot
        if not self.loaded:
            return None
        dataset = build(self.trades, self.evaluations, settings=self.settings, filters=filters)
        return self.engine.analyze(dataset, filters)

    def rows_for(self, filters: AnalyticsFilters | None = None) -> AnalyticsDataset | None:
        if filters is None or filters.empty:
            return self.dataset
        if not self.loaded:
            return None
        return build(self.trades, self.evaluations, settings=self.settings, filters=filters)
