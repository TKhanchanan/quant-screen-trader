"""Deterministic paper simulation benchmark: thousands of samples, hundreds of trades.

Phase 9 does no arithmetic over market history at all — it compares two prices and a few
timestamps — so it should be invisible next to the OCR and feature work that produced the
decision it is measuring. This measures that claim rather than assuming it, and asserts the
bounds that keep the engine's memory flat however long a session runs.
"""

import json
import time
import tracemalloc

import paper_fixtures as fixtures
from quant_engine.configuration import Platform
from quant_engine.paper import (
    PAPER_DURATION_MS,
    TRADE_HISTORY_CAPACITY,
    PaperEngine,
    PaperSettings,
)

EPOCHS = 600
SLOTS = (1, 4, 7)
PLATFORMS: tuple[Platform, ...] = ("capitalbear", "iqoption")
TICK_MS = 250
"""Four canonical samples a second per slot, which is well above real capture cadence."""


def main() -> None:
    engine = PaperEngine(PaperSettings(paperCurrency="THB", paperStake=50, paperPayoutRate=0.82))
    tracemalloc.start()
    wall, cpu = time.perf_counter(), time.process_time()
    board_seconds = sample_seconds = 0.0
    boards = samples = 0
    for step in range(EPOCHS):
        for platform in PLATFORMS:
            period = PAPER_DURATION_MS[platform]
            as_of = fixtures.EPOCH + step * period
            slot = SLOTS[step % len(SLOTS)]
            # Alternating so both outcomes are exercised: a benchmark that only ever wins
            # would be reporting a price ramp, not the resolution path.
            board = fixtures.board(
                platform=platform,
                slot=slot,
                as_of=as_of,
                direction="UP" if step % 2 == 0 else "DOWN",
            )
            available = as_of + 200
            started = time.perf_counter()
            engine.on_board(board, available)
            board_seconds += time.perf_counter() - started
            boards += 1
            # Every slot keeps ticking while one of them is being measured, so the sweep runs
            # over a realistic live set rather than a single trade in isolation.
            for offset in range(0, period + TICK_MS, TICK_MS):
                for candidate in SLOTS:
                    price = 100 + (step % 7) * 0.01 + offset / 1_000_000
                    sample = fixtures.sample(
                        available + offset, price, platform=platform, slot=candidate
                    )
                    started = time.perf_counter()
                    engine.on_market_sample(sample)
                    sample_seconds += time.perf_counter() - started
                    samples += 1
    elapsed = time.perf_counter() - wall
    state = engine.state()
    stats = engine.stats()
    print(
        json.dumps(
            {
                "boardsOffered": boards,
                "samplesProcessed": samples,
                "tradesOpened": state.resolved + state.invalid + state.cancelled + state.open,
                "tradesResolved": state.resolved,
                "wins": stats.wins,
                "losses": stats.losses,
                "draws": stats.draws,
                "seconds": round(elapsed, 3),
                "samplesPerSecond": round(samples / max(sample_seconds, 1e-9)),
                "boardsPerSecond": round(boards / max(board_seconds, 1e-9)),
                "paperCpuShare": round((board_seconds + sample_seconds) / elapsed, 4),
                "cpuSeconds": round(time.process_time() - cpu, 3),
                "peakPythonBytes": tracemalloc.get_traced_memory()[1],
                "historyRows": len(engine.history),
                "historyCapacity": TRADE_HISTORY_CAPACITY,
                "liveTrades": len(engine.live),
            }
        )
    )
    # Memory is bounded by policy, not by how long the session ran.
    assert len(engine.history) <= TRADE_HISTORY_CAPACITY
    assert len(engine.live) <= 6
    assert len(engine.settlements) <= 256


if __name__ == "__main__":
    main()
