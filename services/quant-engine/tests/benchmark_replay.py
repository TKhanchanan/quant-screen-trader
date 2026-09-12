"""Deterministic replay benchmark: a hundred thousand and a million canonical events.

Two numbers matter here and they are different questions.

**Throughput** — events a second — decides whether a month of recorded history is a coffee break
or an afternoon. **Peak memory** decides whether a record larger than memory can be replayed at
all, which is why the source in this file *generates* its rows rather than holding them: a
benchmark that built a million pydantic models into a list first would be measuring the fixture
and would prove the opposite of what it is meant to prove.

The replay never sleeps. The market-time-per-wall-second ratio is printed beside the throughput
precisely so that is visible: a week of market time must not cost a week.

Run it directly; pytest does not collect it.

    python -m tests.benchmark_replay            # 100k
    python tests/benchmark_replay.py 1000000    # 1M
"""

from __future__ import annotations

import json
import math
import resource
import sys
import time
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

sys.path.insert(0, str(Path(__file__).resolve().parent))

from quant_engine.analytics import build  # noqa: E402
from quant_engine.configuration import Platform  # noqa: E402
from quant_engine.market_models import DataQuality, MarketObservation  # noqa: E402
from quant_engine.paper.policy import PaperSettings  # noqa: E402
from quant_engine.replay import (  # noqa: E402
    REPLAY_VERSION,
    ReplayDatasetSummary,
    ReplayEngine,
    ReplayEvent,
    ReplayManifest,
    WalkForwardSettings,
    analyse,
    market_time_of,
)
from quant_engine.replay.source import _Accumulator  # noqa: E402

BASE_MS = int(datetime(2026, 4, 6, 0, 0, tzinfo=UTC).timestamp() * 1000)
SLOTS = 9
PLATFORMS: tuple[Platform, ...] = ("capitalbear", "iqoption")
ASSETS = ("EUR/USD OTC", "GBP/JPY OTC", "Gold OTC", "AUD/CAD OTC", "USD/JPY OTC")
QUALITY = DataQuality(
    state="GOOD",
    confidence=0.99,
    freshness=1.0,
    completeness=1.0,
    sourceReliability=0.99,
    latencyMs=40.0,
)
MASK64 = (1 << 64) - 1


def wobble(index: int) -> float:
    value = (index + 0x9E3779B97F4A7C15) & MASK64
    value ^= value >> 30
    value = (value * 0xBF58476D1CE4E5B9) & MASK64
    value ^= value >> 27
    value = (value * 0x94D049BB133111EB) & MASK64
    value ^= value >> 31
    return (value % 1_000_003) / 1_000_003 - 0.5


class GeneratedSource:
    """A record produced on demand, in canonical order, and never held.

    Implements the replay source contract exactly as the Parquet reader does, so the engine
    under measurement is the real one. Two passes, like the real reader: one to describe and
    fingerprint, one to feed.
    """

    def __init__(self, events: int, *, platforms: Sequence[Platform] = PLATFORMS) -> None:
        self.events = events
        self.platformNames = tuple(platforms)
        self.seconds = max(1, math.ceil(events / (SLOTS * len(self.platformNames))))
        self.outsideWindow = 0

    def _rows(self) -> Iterator[MarketObservation]:
        produced = 0
        levels = {
            (platform, slot): 0.0 for platform in self.platformNames for slot in range(1, SLOTS + 1)
        }
        for second in range(self.seconds):
            for platform in self.platformNames:
                for slot in range(1, SLOTS + 1):
                    if produced >= self.events:
                        return
                    key = (platform, slot)
                    drift = 0.07 if (slot + second // 600) % 3 else -0.07
                    levels[key] += drift + wobble(produced) * 2.0
                    asset = ASSETS[slot % len(ASSETS)]
                    base = 1.0850 + slot * 0.01
                    stamp = (
                        BASE_MS
                        + second * 1_000
                        + slot * 10
                        + (0 if platform == PLATFORMS[0] else 5)
                    )
                    yield MarketObservation(
                        id=uuid5(NAMESPACE_URL, f"bench/{platform}/{slot}/{second}"),
                        platform=platform,
                        slotId=slot,
                        assetName=asset,
                        contextId=uuid5(NAMESPACE_URL, f"bench-ctx/{platform}/{slot}"),
                        observedAt=datetime.fromtimestamp(stamp / 1000, UTC),
                        parsedAt=datetime.fromtimestamp((stamp + 40) / 1000, UTC),
                        sourceType="SYNTHETIC",
                        price=round(base + base * 0.0004 * levels[key], 6),
                        payout=0.82,
                        timerSeconds=30,
                        parserConfidence=0.95,
                        dataQuality=QUALITY,
                        captureLatencyMs=20.0,
                        parseLatencyMs=20.0,
                        calibrationProfileId=None,
                        parserVersion="benchmark-1",
                    )
                    produced += 1

    def prepare(self) -> ReplayDatasetSummary:
        accumulator = _Accumulator()
        rows = 0
        for row in self._rows():
            rows += 1
            accumulator.add(row)
        return accumulator.summary(
            source_type="memory",
            mode="SYNTHETIC",
            label="benchmark",
            rows_read=rows,
            out_of_order=0,
            filtered=0,
        )

    def stream(
        self, *, start: int | None = None, end: int | None = None, batch_size: int = 4_096
    ) -> Iterator[list[ReplayEvent]]:
        self.outsideWindow = 0
        batch: list[ReplayEvent] = []
        for row in self._rows():
            stamp = market_time_of(row)
            if (start is not None and stamp < start) or (end is not None and stamp > end):
                self.outsideWindow += 1
                continue
            batch.append(ReplayEvent(row, stamp, "SYNTHETIC"))
            if len(batch) >= batch_size:
                yield batch
                batch = []
        if batch:
            yield batch


def peak_bytes() -> int:
    """Peak resident set for this process. Bytes on macOS, kilobytes on Linux."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak if sys.platform == "darwin" else peak * 1024


def measure(events: int) -> dict[str, Any]:
    manifest = ReplayManifest(
        warmupDurationMs=0,
        sourceMode="SYNTHETIC",
        paperSettings=PaperSettings(paperCurrency="THB", paperStake=50, paperPayoutRate=0.82),
        walkForward=WalkForwardSettings(mode="COUNT", foldCount=3),
    )
    source = GeneratedSource(events)
    engine = ReplayEngine(manifest, source, root=Path("/tmp/qst-replay-benchmark"), persist=False)
    started = time.perf_counter()
    result = engine.run()
    replay_seconds = time.perf_counter() - started

    started = time.perf_counter()
    dataset = build(result.trades, tuple(), settings=manifest.analyticsSettings)
    analytics_seconds = time.perf_counter() - started

    started = time.perf_counter()
    folds = analyse(
        dataset.rows,
        settings=manifest.walkForward,
        analytics=manifest.analyticsSettings,
        platforms=manifest.platforms,
        paper=manifest.paperSettings,
    )
    walk_forward_seconds = time.perf_counter() - started

    market_ms = (result.run.finishedMarketTime or 0) - (result.run.startedMarketTime or 0)
    processed = result.run.processedEvents
    return {
        "requestedEvents": events,
        "processedEvents": processed,
        "acceptedEvents": result.run.acceptedEvents,
        "status": result.run.status,
        "error": result.run.error,
        "replaySeconds": round(replay_seconds, 3),
        "eventsPerSecond": round(processed / max(replay_seconds, 1e-9)),
        "marketMinutesPerWallSecond": round(market_ms / 60_000 / max(replay_seconds, 1e-9), 2),
        "marketDurationHours": round(market_ms / 3_600_000, 2),
        "ensembles": result.run.ensemblesProduced,
        "boardsFinalized": result.run.boardsFinalized,
        "boardsSelected": result.run.boardsSelected,
        "paperResolved": result.run.paperResolved,
        "paperOutcomesPerSecond": round(result.run.paperResolved / max(replay_seconds, 1e-9), 1),
        "analyticsSeconds": round(analytics_seconds, 3),
        "walkForwardSeconds": round(walk_forward_seconds, 3),
        "folds": folds.folds,
        "peakResidentBytes": peak_bytes(),
        "bytesPerEvent": round(peak_bytes() / max(processed, 1)),
        "strategyEvidenceTruncated": result.truncated,
        "causalityViolations": result.causality.violations,
    }


def main(argv: Sequence[str]) -> int:
    sizes = [int(value) for value in argv[1:]] or [100_000]
    runs = []
    for size in sizes:
        row = measure(size)
        # A replay that waited out its own history would fail this, and nothing else would.
        assert row["marketMinutesPerWallSecond"] > 1, "replay must not wait out market time"
        assert row["causalityViolations"] == 0
        runs.append(row)
        print(json.dumps(row, indent=2), flush=True)
    print(json.dumps({"replayVersion": REPLAY_VERSION, "runs": runs}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
