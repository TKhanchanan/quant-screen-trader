"""Deterministic feature-engine benchmark: 18 slots, four timeframes, replay candles only."""

import json
import math
import time
import tracemalloc

from features_fixtures import candle, second
from quant_engine.configuration import Platform
from quant_engine.features import FeatureEngine
from quant_engine.market_models import TIMEFRAMES

BARS = 300
SECONDS = 600


def main() -> None:
    engine = FeatureEngine()
    slots: list[tuple[Platform, int, str]] = [
        ("capitalbear" if index < 9 else "iqoption", index % 9 + 1, f"ASSET {index} OTC")
        for index in range(18)
    ]
    tracemalloc.start()
    wall, cpu = time.perf_counter(), time.process_time()
    events = snapshots = 0
    for platform, slot, asset in slots:
        for step in range(SECONDS):
            engine.ingest_second(
                second(step, 100.0 + math.sin(step / 5), platform=platform, slot=slot, asset=asset)
            )
            events += 1
    for step in range(BARS):
        close = 100.0 * (1 + 0.004 * math.sin(step / 3) + 0.002 * math.cos(step / 7))
        for platform, slot, asset in slots:
            for timeframe in TIMEFRAMES:
                events += 1
                if engine.ingest_candle(
                    candle(
                        step,
                        close,
                        close * 1.002,
                        close * 0.998,
                        close,
                        timeframe=timeframe,
                        platform=platform,
                        slot=slot,
                        asset=asset,
                    )
                ):
                    snapshots += 1
    elapsed = time.perf_counter() - wall
    states = [state for slot in engine.slots.values() for state in slot.timeframes.values()]
    print(
        json.dumps(
            {
                "slots": len(engine.slots),
                "events": events,
                "featureSnapshots": snapshots,
                "seconds": elapsed,
                "eventsPerSecond": events / elapsed,
                "cpuSeconds": time.process_time() - cpu,
                "peakPythonBytes": tracemalloc.get_traced_memory()[1],
                "maxCandleHistory": max(len(state.closes) for state in states),
                "maxSnapshotHistory": max(len(state.snapshots) for state in states),
                "maxMicroHistory": max(len(slot.micro.history) for slot in engine.slots.values()),
            }
        )
    )
    assert len(engine.slots) == 18
    assert snapshots == 18 * len(TIMEFRAMES) * BARS
    assert max(len(state.closes) for state in states) == 256, "history must stay bounded"


if __name__ == "__main__":
    main()
