"""Deterministic analytics benchmark: ten thousand and a hundred thousand resolved outcomes.

Phase 10 runs on demand rather than in the ingestion path, so the number that matters is whether
an operator can open the panel and get an answer — not whether it is fast enough to sit behind a
tick. Both sizes are measured because the shapes differ: at ten thousand the dataset build
dominates, and at a hundred thousand the segment tables and the threshold grid do.

Memory is reported rather than asserted tight. The rows are the memory, and a hundred thousand
of them is the honest cost of analysing a hundred thousand outcomes; what must stay bounded is
the *output*, so the snapshot's table sizes are asserted instead.
"""

import json
import sys
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import analytics_fixtures as fixtures  # noqa: E402
from quant_engine.analytics import (  # noqa: E402
    ANALYTICS_VERSION,
    AnalyticsEngine,
    AnalyticsSettings,
    build,
)
from quant_engine.analytics.models import MAX_CELLS, MAX_SEGMENTS  # noqa: E402
from quant_engine.paper.models import PaperTrade  # noqa: E402
from quant_engine.strategy.models import Regime, StrategyEvaluation  # noqa: E402

SIZES = (10_000, 100_000)
STRATEGIES_PER_TRADE = 2
"""Two votes per outcome rather than six. The join is linear either way, and a benchmark that
spent most of its time building fixtures would be measuring the fixtures."""


def history(size: int) -> tuple[list[PaperTrade], list[StrategyEvaluation]]:
    trades: list[PaperTrade] = []
    evaluations: list[StrategyEvaluation] = []
    regimes: tuple[Regime, ...] = ("TREND_UP", "RANGE", "NOISY", "TREND_DOWN")
    for index in range(size):
        strong = index % 5 < 2
        row = fixtures.trade(
            f"bench-{index}",
            outcome="WIN" if fixtures.scatter(index) < (67 if strong else 34) else "LOSS",
            platform="iqoption" if index % 4 == 0 else "capitalbear",
            slot=1 + index % 9,
            asset=fixtures.ASSETS[index % len(fixtures.ASSETS)],
            direction="UP" if index % 2 else "DOWN",
            rank_score=round(
                min(0.74 + (index % 4) * 0.05 if strong else 0.22 + (index % 6) * 0.06, 0.99), 4
            ),
            confidence=round(min(0.30 + (index % 7) * 0.10, 0.99), 4),
            agreement=round(min(0.10 + (index % 9) * 0.10, 0.99), 4),
            regime=regimes[index % len(regimes)],
            regime_confidence=round(min(0.25 + (index % 8) * 0.09, 0.99), 4),
            lead_margin=round(0.02 + (index % 5) * 0.07, 4),
            expiry=fixtures.BASE_MS + index * 1_000,
        )
        trades.append(row)
        for position in range(STRATEGIES_PER_TRADE):
            evaluations.append(
                fixtures.evaluation(
                    row,
                    fixtures.STRATEGIES[position],
                    "UP" if (index + position) % 3 else "NEUTRAL",
                )
            )
    return (trades, evaluations)


def main() -> None:
    settings = AnalyticsSettings()
    engine = AnalyticsEngine(settings)
    results = []
    for size in SIZES:
        trades, evaluations = history(size)
        # Timed without tracemalloc: the allocation tracker roughly doubles the cost of exactly
        # the row-building this measures, and a benchmark that reports the profiler's overhead
        # as the layer's cost is worse than no benchmark.
        started = time.perf_counter()
        dataset = build(trades, evaluations, settings=settings)
        build_seconds = time.perf_counter() - started
        started = time.perf_counter()
        snapshot = engine.analyze(dataset)
        analyse_seconds = time.perf_counter() - started
        tracemalloc.start()
        measured = build(trades, evaluations, settings=settings)
        engine.analyze(measured)
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        del measured
        assert snapshot.totalResolved == size
        # The output stays a report however large the input is.
        assert len(snapshot.assetMetrics) <= MAX_SEGMENTS
        assert len(snapshot.strategyRegimeMatrix.cells) <= MAX_CELLS
        assert len(snapshot.hourMetrics) <= 24
        assert len(snapshot.weekdayMetrics) <= 7
        results.append(
            {
                "resolvedTrades": size,
                "strategyVotes": len(evaluations),
                "datasetBuildSeconds": round(build_seconds, 3),
                "analyticsSeconds": round(analyse_seconds, 3),
                "totalSeconds": round(build_seconds + analyse_seconds, 3),
                "tradesPerSecond": round(size / max(build_seconds + analyse_seconds, 1e-9)),
                "peakPythonBytes": peak,
                "bytesPerResolvedTrade": round(peak / size),
                "segmentRows": len(snapshot.assetMetrics)
                + len(snapshot.regimeMetrics)
                + len(snapshot.hourMetrics)
                + len(snapshot.weekdayMetrics),
                "matrixCells": len(snapshot.strategyRegimeMatrix.cells)
                + len(snapshot.rankConfidenceMatrix.cells),
                "thresholdCandidates": len(snapshot.thresholdCandidates),
                "comparisonsEvaluated": snapshot.comparisonsEvaluated,
                "warnings": snapshot.warnings,
            }
        )
        del trades, evaluations, dataset
    print(json.dumps({"analyticsVersion": ANALYTICS_VERSION, "runs": results}, indent=2))


if __name__ == "__main__":
    main()
