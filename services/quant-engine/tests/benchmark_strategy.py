"""Deterministic strategy-engine benchmark: 18 slots, one ensemble per primary close."""

import json
import math
import time
import tracemalloc

from features_fixtures import candle, second
from quant_engine.configuration import Platform
from quant_engine.features import FeatureEngine
from quant_engine.features.engine import PRIMARY_TIMEFRAME
from quant_engine.strategy import StrategyEngine
from quant_engine.strategy.engine import HISTORY_CAPACITY

BARS = 200
SECONDS = 120


def main() -> None:
    features, strategies = FeatureEngine(), StrategyEngine()
    slots: list[tuple[Platform, int, str]] = [
        ("capitalbear" if index < 9 else "iqoption", index % 9 + 1, f"ASSET {index} OTC")
        for index in range(18)
    ]
    for platform, slot, asset in slots:
        for step in range(SECONDS):
            features.ingest_second(
                second(step, 100.0 + math.sin(step / 4), platform=platform, slot=slot, asset=asset)
            )
    tracemalloc.start()
    wall, cpu = time.perf_counter(), time.process_time()
    ensembles = evaluations = 0
    strategy_seconds = 0.0
    directions: dict[str, int] = {}
    for step in range(BARS):
        close = 100.0 * (1 + 0.004 * math.sin(step / 9) + 0.001 * math.cos(step / 3))
        for platform, slot, asset in slots:
            timeframe = PRIMARY_TIMEFRAME[platform]
            snapshot = features.ingest_candle(
                candle(
                    step,
                    close,
                    close * 1.0015,
                    close * 0.9985,
                    close,
                    timeframe=timeframe,
                    platform=platform,
                    slot=slot,
                    asset=asset,
                )
            )
            if snapshot is None:
                continue
            bundle = features.latest_bundle(platform, slot)
            assert bundle is not None
            started = time.perf_counter()
            result = strategies.evaluate(bundle)
            strategy_seconds += time.perf_counter() - started
            ensembles += 1
            evaluations += len(result.strategies)
            directions[result.direction] = directions.get(result.direction, 0) + 1
    elapsed = time.perf_counter() - wall
    histories = [len(history) for history in strategies.slots.values()]
    print(
        json.dumps(
            {
                "slots": len(strategies.slots),
                "ensembles": ensembles,
                "strategyEvaluations": evaluations,
                "duplicates": strategies.duplicates,
                "seconds": elapsed,
                "strategySeconds": strategy_seconds,
                "ensemblesPerSecond": ensembles / strategy_seconds,
                "replayEnsemblesPerSecond": ensembles / elapsed,
                "cpuSeconds": time.process_time() - cpu,
                "peakPythonBytes": tracemalloc.get_traced_memory()[1],
                "maxHistoryPerSlot": max(histories),
                "directions": directions,
            }
        )
    )
    assert len(strategies.slots) == 18
    assert ensembles == 18 * BARS, "one ensemble per primary close, no more and no fewer"
    assert strategies.duplicates == 0
    assert max(histories) == HISTORY_CAPACITY, "history must stay bounded"


if __name__ == "__main__":
    main()
