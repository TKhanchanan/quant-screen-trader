"""Deterministic ranking benchmark: 18 slots across two platforms, many ranking epochs.

Ranking is arithmetic over opinions that already exist, so it should be invisible next to the
OCR and feature work that produced them. This measures that claim rather than assuming it,
and asserts the bounds that keep the engine's memory flat however long it runs.
"""

import json
import time
import tracemalloc

import opportunity_fixtures as fixtures
from quant_engine.configuration import Platform
from quant_engine.opportunity import BOARD_HISTORY_CAPACITY, RECENT_CAPACITY, OpportunityEngine

EPOCHS = 400
SLOTS = tuple(range(1, 10))
PLATFORMS: tuple[Platform, ...] = ("capitalbear", "iqoption")


def main() -> None:
    engine = OpportunityEngine()
    expected = set(SLOTS)
    tracemalloc.start()
    wall, cpu = time.perf_counter(), time.process_time()
    ranking_seconds = 0.0
    inputs = candidates = 0
    statuses: dict[str, int] = {}
    for step in range(EPOCHS):
        for platform in PLATFORMS:
            as_of = fixtures.next_epoch(platform, periods=step)
            for slot in SLOTS:
                # A confidence that drifts per slot and per epoch, so reorderings, selection
                # changes and refusals all occur rather than one static board being rebuilt.
                # The step is wide enough that the leader sometimes clears the lead margin.
                confidence = 0.20 + 0.06 * ((slot + step) % 9)
                snapshot = fixtures.ensemble(
                    platform=platform,
                    slot=slot,
                    as_of=as_of,
                    direction="UP" if (slot + step) % 2 == 0 else "DOWN",
                    confidence=confidence,
                )
                started = time.perf_counter()
                result = engine.ingest(snapshot, expected)
                ranking_seconds += time.perf_counter() - started
                inputs += 1
                assert result is not None
                candidates += len(result.board.candidates)
                if result.finalized is not None:
                    statuses[result.finalized.status] = statuses.get(result.finalized.status, 0) + 1
    elapsed = time.perf_counter() - wall
    windows = [len(history.entries) for history in engine.slots.values()]
    boards = [len(history) for history in engine.history.values()]
    print(
        json.dumps(
            {
                "slots": len(engine.slots),
                "ensembleInputs": inputs,
                "candidateEvaluations": candidates,
                "boardsGenerated": engine.finalized,
                "duplicates": engine.duplicates,
                "outOfOrder": engine.outOfOrder,
                "staleForEpoch": engine.staleForEpoch,
                "seconds": elapsed,
                "rankingSeconds": ranking_seconds,
                "evaluationsPerSecond": inputs / ranking_seconds,
                "boardsPerSecond": engine.finalized / ranking_seconds,
                "cpuSeconds": time.process_time() - cpu,
                "peakPythonBytes": tracemalloc.get_traced_memory()[1],
                "maxStabilityWindow": max(windows),
                "maxBoardHistory": max(boards),
                "finalizedStatuses": statuses,
            }
        )
    )
    assert len(engine.slots) == 18
    assert inputs == len(PLATFORMS) * len(SLOTS) * EPOCHS
    assert engine.finalized == len(PLATFORMS) * (EPOCHS - 1), "one board per closed epoch"
    assert engine.duplicates == 0 and engine.outOfOrder == 0
    assert max(windows) == RECENT_CAPACITY, "stability windows must stay bounded"
    assert max(boards) == BOARD_HISTORY_CAPACITY, "board history must stay bounded"
    assert statuses.get("READY", 0) > 0, "the selection path must actually be exercised"


if __name__ == "__main__":
    main()
