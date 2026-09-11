"""Deterministic session-guard benchmark: ten thousand settlements across many trading days.

The guard adds one addition and two comparisons to each settled outcome, so it should be
invisible next to the OCR and feature work that produced the trade. This measures that claim
rather than assuming it, and asserts the bounds that keep memory flat over a long run of days.
"""

import json
import time
import tracemalloc

import session_guard_fixtures as fixtures
from quant_engine.session_guard import MAX_HISTORY, SessionGuard, SessionGuardSettings

SETTLEMENTS = 10_000
PER_DAY = 250
STEP_MS = 60_000


def main() -> None:
    engine = SessionGuard(
        SessionGuardSettings(
            enabled=True, dailyProfitTarget=5_000, dailyLossLimit=5_000, currency="THB"
        )
    )
    tracemalloc.start()
    wall, cpu = time.perf_counter(), time.process_time()
    guard_seconds = 0.0
    sessions = events = 0
    for index in range(SETTLEMENTS):
        day = 1 + index // PER_DAY
        # A shape that wins and loses, so the threshold checks and the equity path both run.
        amount = 41.0 if index % 3 else -50.0
        settled = fixtures.at(0, day=1) + day * 86_400_000 + (index % PER_DAY) * STEP_MS
        settlement = fixtures.settlement(amount, label=f"bench-{index}", settled_at=settled)
        started = time.perf_counter()
        update = engine.apply_settlement(settlement, unresolved=index % 2)
        guard_seconds += time.perf_counter() - started
        sessions += len(update.sessions)
        events += len(update.events)
    elapsed = time.perf_counter() - wall
    state = engine.state(fixtures.at(12))
    print(
        json.dumps(
            {
                "settlements": SETTLEMENTS,
                "sessionRowsWritten": sessions,
                "eventRowsWritten": events,
                "tradingDays": len(engine.history) + 1,
                "seconds": round(elapsed, 3),
                "settlementsPerSecond": round(SETTLEMENTS / max(guard_seconds, 1e-9)),
                "guardCpuShare": round(guard_seconds / elapsed, 4),
                "cpuSeconds": round(time.process_time() - cpu, 3),
                "peakPythonBytes": tracemalloc.get_traced_memory()[1],
                "historyRows": len(engine.history),
                "historyCapacity": MAX_HISTORY,
                "eventLogRows": len(engine.eventLog),
                "processedIds": len(engine._processed),
                "canOpenNewEntry": state.canOpenNewEntry,
            }
        )
    )
    # Memory is bounded by policy, not by how many days the operator has traded.
    assert len(engine.history) <= MAX_HISTORY
    assert len(engine.eventLog) <= 512
    assert len(engine.notifications) <= 16


if __name__ == "__main__":
    main()
