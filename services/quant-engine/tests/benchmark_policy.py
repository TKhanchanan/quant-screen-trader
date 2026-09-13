"""100k deterministic analytical evaluations; no network. SYNTHETIC_BEHAVIOR_TEST."""

import json
import platform
import resource
import time
import tracemalloc
from statistics import median

import paper_fixtures
import policy_fixtures
from quant_engine.policy.engine import PolicyEngine


def main() -> None:
    engine = PolicyEngine()
    snapshot = policy_fixtures.snapshot(negative=True)
    boards = (
        paper_fixtures.board(),
        paper_fixtures.board(regime="NOISY"),
        paper_fixtures.board(confidence=0.1),
    )
    latencies = []
    start = time.perf_counter_ns()
    for i in range(100_000):
        before = time.perf_counter_ns()
        engine.evaluate(boards[i % 3], snapshot, paper_fixtures.EPOCH + 1200)
        latencies.append(time.perf_counter_ns() - before)
    seconds = (time.perf_counter_ns() - start) / 1e9
    ordered = sorted(latencies)
    # Separate memory pass keeps allocation tracing out of measured latency.
    tracemalloc.start()
    for i in range(100_000):
        engine.evaluate(boards[i % 3], snapshot, paper_fixtures.EPOCH + 1200)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(
        json.dumps(
            dict(
                label="SYNTHETIC_BEHAVIOR_TEST",
                evaluations=100_000,
                evaluationsPerSecond=100_000 / seconds,
                p50Microseconds=median(ordered) / 1000,
                p95Microseconds=ordered[94_999] / 1000,
                peakPythonBytes=peak,
                peakProcessBytes=rss if platform.system() == "Darwin" else rss * 1024,
                python=platform.python_version(),
                platform=platform.platform(),
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
