"""Account-free 18-slot builder benchmark, not a broker capture benchmark."""

import json
import time
import tracemalloc

from quant_engine.market_builder import TimeSeriesBuilder
from test_market import observation


def main() -> None:
    builders = [TimeSeriesBuilder() for _ in range(18)]
    tracemalloc.start()
    wall, cpu = time.perf_counter(), time.process_time()
    count = 0
    for step in range(1201):
        for index, builder in enumerate(builders):
            builder.ingest(
                observation(
                    step / 2,
                    1 + step / 100000,
                    slotId=index % 9 + 1,
                    platform="capitalbear" if index < 9 else "iqoption",
                )
            )
            builder.drain()
            count += 1
    elapsed = time.perf_counter() - wall
    print(
        json.dumps(
            {
                "observations": count,
                "seconds": elapsed,
                "observationsPerSecond": count / elapsed,
                "cpuSeconds": time.process_time() - cpu,
                "peakPythonBytes": tracemalloc.get_traced_memory()[1],
                "maxSamplesPerSlot": max(len(b.samples) for b in builders),
            }
        )
    )
    assert all(
        any(c.timeframe == "M10" and c.state == "CLOSED" for c in b.candles) for b in builders
    )


if __name__ == "__main__":
    main()
