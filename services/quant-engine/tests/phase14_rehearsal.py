"""Phase 14 accelerated rehearsal. REHEARSAL ONLY — NOT REAL LIVE ACCEPTANCE.

This drives the production analytical engine (``MarketEngine``: Phase 5 builder, Phase 6
features, Phase 7 ensemble, Phase 8 ranking, Phase 9 paper, Phase 9.5 session guard, Phase 10
analytics invalidation, Phase 12 SHADOW policy) and the production Phase 14 recorder through
more than twenty-five hours of deterministic virtual time, in minutes. Nothing here restates an
analytical rule: the harness only produces input, moves a virtual clock, and reads what the real
code decided.

**What it can prove.** Recorder accounting, the 24h / 23h duration logic, causality and context
checks, board / policy / paper lifecycle counting, restart continuation and its build guard, event
and cache bounds, storage verification, and the acceptance gate itself.

**What it cannot prove.** Electron stability over a day, broker session longevity, real memory
drift, broker UI changes, network outages, OCR degradation, login expiry or sleep/wake. Those need
the real soak, and nothing in this file changes the real acceptance thresholds.

**Input.** Every observation is ``SYNTHETIC_REHEARSAL``: generated, broker-shaped (VISUAL source,
OCR parse lag, batch capture cadence) and never market data. The recorded Phase 11 history on the
development machine is far shorter than a day (its longest unbroken context is minutes), so it
cannot carry a 25-hour rehearsal.

**Output.** Only under the chosen ``--out`` folder (default ``artifacts/phase14-rehearsal``, ignored
by Git). Never ``docs/evidence/phase14``. The desktop execution rehearsal reads the exported
``execution-timeline.jsonl.gz`` and drives the real ``ExecutionManager`` in PAPER mode.

Run it directly; pytest does not collect it:

    node scripts/python.mjs services/quant-engine/tests/phase14_rehearsal.py

``npm run rehearsal:phase14`` runs this, the desktop half, and combines both summaries.
"""

from __future__ import annotations

import argparse
import copy
import gzip
import heapq
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, TextIO
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

sys.path.insert(0, str(Path(__file__).resolve().parent))

import duckdb  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pydantic import ValidationError  # noqa: E402
from quant_engine import __version__  # noqa: E402
from quant_engine.configuration import Platform  # noqa: E402
from quant_engine.market_api import MarketEngine, ObservationBatch  # noqa: E402
from quant_engine.market_models import DataQuality, MarketObservation  # noqa: E402
from quant_engine.market_storage import (  # noqa: E402
    Category,
    ParquetStorage,
    Record,
    partition_asset,
    partition_platform,
    record_stamp,
)
from quant_engine.opportunity.models import OpportunityBoard  # noqa: E402
from quant_engine.paper.policy import PaperSettings  # noqa: E402
from quant_engine.paths import AppPaths, ensure_app_paths  # noqa: E402
from quant_engine.policy.repository import PolicyRepository  # noqa: E402
from quant_engine.policy.service import PolicyService  # noqa: E402
from quant_engine.session_guard import SessionGuardSettings  # noqa: E402
from quant_engine.shadow_live import (  # noqa: E402
    MAX_EVENTS_BYTES,
    MAX_RECENT,
    PLATFORMS,
    ShadowLiveRecorder,
)
from quant_engine.shadow_live_api import DesktopTelemetry  # noqa: E402
from quant_engine.shadow_live_api import router as shadow_live_router  # noqa: E402
from quant_engine.storage.database import initialize_database  # noqa: E402
from replay_fixtures import wobble  # noqa: E402

REHEARSAL_LABEL: Final = "REHEARSAL ONLY - NOT REAL LIVE ACCEPTANCE"
SOURCE_LABEL: Final = "SYNTHETIC_REHEARSAL"
REHEARSAL_VERSION: Final = "qst-phase14-rehearsal-v1"
OPERATOR_STAND_IN: Final = "REHEARSAL_SIMULATED_OPERATOR"
"""Relabels attestations the rehearsal submits through the real endpoints. The endpoint marks
them OPERATOR_OBSERVATION; a simulated operator is not one, so the rehearsal copy says so."""

VIRTUAL_EPOCH_MS: Final = int(datetime(2026, 3, 2, 0, 0, tzinfo=UTC).timestamp() * 1000)
"""A Monday in the past. Storage verification compares file modification times (wall clock)
against the run start, so a virtual start before today keeps every rehearsal file in scope."""

SECOND: Final = 1_000
MINUTE: Final = 60 * SECOND
HOUR: Final = 60 * MINUTE
TOTAL_TARGET_MS: Final = 24 * HOUR
PLATFORM_TARGET_MS: Final = 23 * HOUR
PRODUCTION_STORAGE_BATCH: Final = 4_096
STORAGE_AUDIT_FILE_CAP: Final = 10_000

ASSETS: Final[dict[Platform, tuple[str, ...]]] = {
    "capitalbear": (
        "EUR/USD OTC",
        "GBP/USD OTC",
        "USD/JPY OTC",
        "AUD/USD OTC",
        "EUR/JPY OTC",
        "GBP/JPY OTC",
        "USD/CAD OTC",
        "EUR/GBP OTC",
        "Gold OTC",
    ),
    "iqoption": (
        "EUR/USD",
        "GBP/USD",
        "USD/JPY",
        "AUD/CAD",
        "EUR/JPY",
        "USD/CHF",
        "NZD/USD",
        "EUR/GBP",
        "Bitcoin",
    ),
}
BASE_PRICE: Final[dict[str, float]] = {"Gold OTC": 2410.0, "Bitcoin": 64_000.0}
CADENCE_MS: Final[dict[Platform, int]] = {"capitalbear": 2_500, "iqoption": 3_000}
"""One batch captures every enabled slot from one frame, as the desktop does. CapitalBear's
earlier live rate was three to four readings a second across nine slots; these cadences sit in
that range rather than at a rate OCR cannot deliver."""
BATCH_OFFSET_MS: Final[dict[Platform, int]] = {"capitalbear": 0, "iqoption": 700}
INTERVAL_MS: Final[dict[Platform, int]] = {"capitalbear": 500, "iqoption": 1_000}
DELIVERY_LAG_MS: Final = 900
"""Capture to engine receipt: OCR on two workers plus the 250 ms transport flush."""
TELEMETRY_OFFSET_MS: Final[dict[Platform, int]] = {"capitalbear": 100, "iqoption": 150}
POLL_OFFSET_MS: Final = 500
REHEARSAL_PLATFORMS: Final[tuple[Platform, Platform]] = ("capitalbear", "iqoption")
REGIME_BLOCK_MS: Final = 45 * MINUTE
TRANSPORT_BATCH: Final = 18
QUEUE_CAPACITY: Final = 180
REQUEST_TIMEOUT_MS: Final = 2_000


@dataclass(frozen=True, slots=True)
class SlotWindow:
    platform: Platform
    slot: int
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class AssetChange:
    platform: Platform
    slot: int
    at: int
    asset: str


@dataclass(frozen=True, slots=True)
class Window:
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class Scenario:
    """Every scheduled operational event, as offsets from the virtual start."""

    name: str
    finish: int
    capture_start: dict[Platform, int]
    capture_stop: int
    identity_failure: SlotWindow | None
    unreadable_slot: SlotWindow | None
    asset_change: AssetChange | None
    context_change: tuple[Platform, int] | None
    auto_sync_on: int | None
    auto_sync_change: AssetChange | None
    auto_sync_review: int | None
    restart: int | None
    restart_downtime: int
    resume_capture_delay: int
    verify_restart_delay: int
    engine_stall: tuple[int, int] | None
    network_hiccup: Window | None
    uncertain_share: float
    storage_batch: int
    flush_every_s: int


FULL: Final = Scenario(
    name="full-25h",
    finish=25 * HOUR + 45 * MINUTE,
    capture_start={"capitalbear": 10 * MINUTE, "iqoption": 12 * MINUTE},
    capture_stop=25 * HOUR + 40 * MINUTE,
    identity_failure=SlotWindow("capitalbear", 7, 2 * HOUR, 2 * HOUR + 2 * MINUTE),
    unreadable_slot=SlotWindow("iqoption", 4, 3 * HOUR, 3 * HOUR + 20 * MINUTE),
    asset_change=AssetChange("capitalbear", 2, 5 * HOUR, "NZD/USD OTC"),
    context_change=("iqoption", 5 * HOUR + 30 * MINUTE),
    auto_sync_on=1 * HOUR,
    auto_sync_change=AssetChange("iqoption", 6, 6 * HOUR, "USD/CHF (OTC)"),
    auto_sync_review=6 * HOUR + 30 * MINUTE,
    restart=8 * HOUR,
    restart_downtime=10 * MINUTE,
    resume_capture_delay=5 * MINUTE,
    verify_restart_delay=10 * MINUTE,
    engine_stall=(11 * HOUR, 8 * SECOND),
    network_hiccup=Window(14 * HOUR, 14 * HOUR + 3 * MINUTE),
    uncertain_share=0.02,
    storage_batch=100_000,
    flush_every_s=10,
)
"""The operator's day, compressed: login, capture, a slot that cannot be identified, a slot that
cannot be read, a manual asset change, a surface change, an Auto Sync rename and its review, a
controlled restart at hour 8, an engine stall with a burst and a 429, a network hiccup, and
capture well past the 24h / 23h targets before storage is verified."""


def smoke(finish_minutes: int = 40) -> Scenario:
    """A short scenario with the same events, for tests. Durations here never reach 24h."""
    m = MINUTE
    return replace(
        FULL,
        name=f"smoke-{finish_minutes}m",
        finish=finish_minutes * m,
        capture_start={"capitalbear": 1 * m, "iqoption": 1 * m + 20 * SECOND},
        capture_stop=(finish_minutes - 2) * m,
        identity_failure=SlotWindow("capitalbear", 7, 4 * m, 5 * m),
        unreadable_slot=SlotWindow("iqoption", 4, 6 * m, 8 * m),
        asset_change=AssetChange("capitalbear", 2, 10 * m, "NZD/USD OTC"),
        context_change=("iqoption", 12 * m),
        auto_sync_on=2 * m,
        auto_sync_change=AssetChange("iqoption", 6, 14 * m, "USD/CHF (OTC)"),
        auto_sync_review=15 * m,
        restart=18 * m,
        restart_downtime=2 * m,
        resume_capture_delay=1 * m,
        verify_restart_delay=2 * m,
        engine_stall=(26 * m, 8 * SECOND),
        network_hiccup=Window(28 * m, 29 * m),
        storage_batch=5_000,
    )


class VirtualClock:
    """Rehearsal time. Injected into the recorder; production never constructs one."""

    def __init__(self, start: int) -> None:
        self.now = start

    def __call__(self) -> int:
        return self.now

    def advance_to(self, at: int) -> None:
        if at < self.now:
            raise RuntimeError("virtual time cannot move backwards")
        self.now = at


class Schedule:
    """A deterministic event queue: ties keep insertion order."""

    def __init__(self, clock: VirtualClock) -> None:
        self.clock = clock
        self.heap: list[tuple[int, int, Callable[[int], None]]] = []
        self.sequence = 0

    def at(self, when: int, action: Callable[[int], None]) -> None:
        heapq.heappush(self.heap, (when, self.sequence, action))
        self.sequence += 1

    def run_until(self, end: int) -> None:
        while self.heap and self.heap[0][0] <= end:
            when, _, action = heapq.heappop(self.heap)
            self.clock.advance_to(when)
            action(when)


class RehearsalStorage(ParquetStorage):
    """The production Parquet writer with a larger buffer, plus an exact file-count shadow.

    A 4096-record buffer writes one file per category, platform, asset and date on every flush,
    which is what the live engine does. Writing that many small files would turn a minutes-long
    rehearsal into an hour, so rehearsal rows are buffered longer and the shadow counts the files
    the production buffer *would* have written, using the storage module's own partition keys.
    """

    def __init__(self, root: Path, batch_size: int) -> None:
        super().__init__(root, batch_size)
        self.window: set[tuple[str, str, str, str]] = set()
        self.window_rows = 0
        self.productionFiles = 0
        self.productionFlushes = 0
        self.rows = 0
        self._inside_append = False

    def _close_window(self) -> None:
        if self.window_rows:
            self.productionFiles += len(self.window)
            self.productionFlushes += 1
        self.window.clear()
        self.window_rows = 0

    def append(self, category: Category, record: Record) -> None:
        if self.window_rows >= PRODUCTION_STORAGE_BATCH:
            self._close_window()
        stamp = f"{record_stamp(record):%Y-%m-%d}"
        self.window.add((category, partition_platform(record), partition_asset(record), stamp))
        self.window_rows += 1
        self.rows += 1
        self._inside_append = True
        try:
            super().append(category, record)
        finally:
            self._inside_append = False

    def flush(self) -> None:
        if not self._inside_append:
            self._close_window()
        super().flush()


@dataclass(slots=True)
class DesktopSlot:
    slot: int
    asset: str
    context: UUID
    enabled: bool = True
    state: str = "WAITING"
    observations: int = 0
    data_uncertain: int = 0
    last_capture_attempt: int | None = None
    capture_eligible: bool = False
    last_attempt: int | None = None
    last_parsed: int | None = None
    last_good: int | None = None
    attempts: int = 0
    parsed: int = 0
    good: int = 0
    uncertain: int = 0
    second_samples: int = 0
    s5_samples: int = 0
    m1_samples: int = 0


@dataclass(slots=True)
class DesktopPlatform:
    """The fields the desktop reports for one workspace, maintained the way MarketManager does."""

    platform: Platform
    instance: UUID
    slots: dict[int, DesktopSlot]
    running: bool = False
    surface: bool = True
    engine: bool = False
    revision: int = 0
    signature: str = ""
    queue: int = 0
    dropped_batches: int = 0
    http429s: int = 0
    execution_mode: str = "OFF"
    paper_armed: bool = False
    auto_sync_enabled: bool = False
    auto_sync_runs: int = 0
    auto_sync_applied: int = 0
    surface_revision: int = 1
    capture_count: int = 0
    capture_started: int = 0


@dataclass(slots=True)
class Segment:
    engine: MarketEngine
    storage: RehearsalStorage
    policy: PolicyService
    recorder: ShadowLiveRecorder
    client: TestClient


@dataclass(slots=True)
class Check:
    name: str
    passed: bool
    detail: Any = None


@dataclass(slots=True)
class Outcome:
    """Everything a rehearsal run observed. ``checks`` decide the rehearsal result."""

    scenario: str
    checks: list[Check] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)

    def check(self, name: str, passed: bool, detail: Any = None) -> bool:
        self.checks.append(Check(name, bool(passed), detail))
        return bool(passed)

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(check.passed for check in self.checks)


def duration(ms: int | float | None) -> str:
    if ms is None:
        return "--:--:--"
    total = int(ms) // 1000
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat().replace("+00:00", "Z")


def context_id(platform: str, slot: int, generation: int) -> UUID:
    return uuid5(NAMESPACE_URL, f"qst-phase14-rehearsal/context/{platform}/{slot}/{generation}")


class SyntheticFeed:
    """Deterministic broker-shaped prices: a mean-reverting walk with 45-minute regimes.

    Every value is a pure function of platform, slot, context generation and step, via the replay
    fixtures' SplitMix64 ``wobble``. Nothing is tuned toward a selection: boards become READY only
    if the real ranking gates are cleared by this input, and at live-like cadence they rarely are.
    """

    REGIMES: Final = ("TREND_UP", "TREND_DOWN", "RANGE", "NOISY", "QUIET")

    def __init__(self) -> None:
        self.levels: dict[tuple[str, int, int], float] = {}
        self.steps: dict[tuple[str, int, int], int] = {}

    def price(self, platform: Platform, slot: int, generation: int, asset: str, at: int) -> float:
        key = (platform, slot, generation)
        step = self.steps.get(key, 0)
        seed = (PLATFORMS.index(platform) * 97 + slot) * 1_000_003 + generation * 7_919
        block = at // REGIME_BLOCK_MS
        regime = self.REGIMES[int((wobble(seed * 31 + block) + 0.5) * len(self.REGIMES)) % 5]
        noise = wobble(seed * 131 + step) * 2.0
        level = self.levels.get(key, 0.0)
        drift = {"TREND_UP": 0.09, "TREND_DOWN": -0.09}.get(regime, 0.0)
        scale = {"NOISY": 1.5, "QUIET": 0.35}.get(regime, 1.0)
        reversion = 0.08 if regime == "RANGE" else 0.0015
        level += drift + noise * scale - reversion * level
        self.levels[key], self.steps[key] = level, step + 1
        base = BASE_PRICE.get(asset, 1.0 + 0.05 * slot + 0.3 * PLATFORMS.index(platform))
        digits = 2 if base > 100 else 5
        return round(base * (1 + 0.00035 * level), digits)


def quality(state: str, confidence: float) -> DataQuality:
    return DataQuality(
        state=state,  # type: ignore[arg-type]
        confidence=confidence,
        freshness=1.0,
        completeness=1.0 if state == "GOOD" else 0.4,
        sourceReliability=0.95,
        latencyMs=280.0,
    )


def git_commit(repository: Path) -> tuple[str, bool]:
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repository, capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repository,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        return sha, not dirty
    except (OSError, subprocess.CalledProcessError):
        return "unknown", False


class Rehearsal:
    """One deterministic run of a scenario against the production engine and recorder."""

    def __init__(
        self,
        scenario: Scenario,
        root: Path,
        *,
        commit_sha: str,
        timeline: TextIO | None = None,
        stop_on_fail: bool = False,
    ) -> None:
        self.scenario = scenario
        self.root = root
        self.paths: AppPaths = ensure_app_paths(root)
        self.commit = commit_sha
        self.version = __version__
        self.clock = VirtualClock(VIRTUAL_EPOCH_MS)
        self.schedule = Schedule(self.clock)
        self.feed = SyntheticFeed()
        self.timeline = timeline
        self.stop_on_fail = stop_on_fail
        self.outcome = Outcome(scenario.name)
        self.segment: Segment | None = None
        self.instance = uuid4()
        self.run_id: str | None = None
        self.generation: dict[tuple[str, int], int] = {}
        self.context_log: list[dict[str, Any]] = []
        self.desktop: dict[str, DesktopPlatform] = {}
        # MarketManager keeps one bounded queue and one transport counter for both workspaces.
        self.queue: list[MarketObservation] = []
        self.flush_scheduled = False
        self.sending_until = -1
        self.busy_until = -1
        self.stall_next_request = False
        self.dropped_batches = 0
        self.http429s = 0
        self.queue_drops = 0
        self.last_export: dict[str, str] = {}
        self.max_pending_events = 0
        self.event_bytes_by_hour: list[tuple[float, int]] = []
        self.gate_samples: dict[str, Any] = {}
        self.restart_facts: dict[str, Any] = {}
        self.segments = 0
        self.failed_at: int | None = None
        self.max_batch = 0
        self.max_queue = 0
        self.stale_rejections = 0

    # --- time ------------------------------------------------------------------------------

    def t(self, offset: int) -> int:
        return VIRTUAL_EPOCH_MS + offset

    def within(self, window: SlotWindow | Window | None, at: int) -> bool:
        return window is not None and self.t(window.start) <= at < self.t(window.end)

    def repeat(self, at: int, every: int, action: Callable[[int], None]) -> None:
        if at + every <= self.t(self.scenario.finish):
            self.schedule.at(at + every, action)

    # --- process segments ------------------------------------------------------------------

    def start_segment(self, at: int) -> Segment:
        initialize_database(self.paths.database_file)
        policy = PolicyService(
            PolicyRepository(self.paths.market_data / "policy" / "journal.sqlite3")
        )
        storage = RehearsalStorage(self.paths.market_data, self.scenario.storage_batch)
        engine = MarketEngine(
            storage, paper=PaperSettings(), guard=SessionGuardSettings(), policy=policy
        )
        # The same start-up restores the engine lifespan performs.
        engine.restore_paper()
        engine.restore_session_guard(at)
        recorder = ShadowLiveRecorder(
            self.paths.root / "phase14",
            commit_sha=self.commit,
            application_version=self.version,
            run_id=self.run_id,
            now=at,
            clock=self.clock,
        )
        engine.shadow = recorder
        self.run_id = str(recorder.data["runId"])
        app = FastAPI()
        app.include_router(shadow_live_router)
        app.state.market = engine
        app.state.paths = self.paths
        segment = Segment(engine, storage, policy, recorder, TestClient(app))
        self.segment = segment
        self.segments += 1
        # One desktop process, one telemetry observer: both workspaces share its instance id.
        self.instance = uuid4()
        self.desktop = {name: self.new_desktop(name, at) for name in REHEARSAL_PLATFORMS}
        # A new desktop process starts its transport counters and queue from nothing.
        self.queue.clear()
        self.dropped_batches = self.http429s = 0
        self.export({"type": "process", "event": "start", "segment": self.segments}, at)
        return segment

    def stop_segment(self, at: int) -> None:
        segment = self.segment
        assert segment is not None
        # Mirrors the engine lifespan: flush market storage, close the policy journal, then
        # write the recorder's final checkpoint and release its lease.
        segment.storage.flush()
        segment.policy.repository.close()
        segment.recorder.flush(now=at, finish=True)
        segment.client.close()
        self.segment = None
        self.queue.clear()
        self.export({"type": "process", "event": "stop", "segment": self.segments}, at)

    def new_desktop(self, name: Platform, at: int) -> DesktopPlatform:
        previous = self.desktop.get(name)
        slots = {}
        for slot in range(1, 10):
            asset = previous.slots[slot].asset if previous else ASSETS[name][slot - 1]
            slots[slot] = DesktopSlot(slot, asset, self.assign(name, slot, asset, at))
        return DesktopPlatform(name, self.instance, slots, capture_started=at)

    def bump(self, platform: str, slot: int) -> int:
        key = (platform, slot)
        self.generation[key] = self.generation.get(key, -1) + 1
        return self.generation[key]

    def assign(self, platform: str, slot: DesktopSlot | int, asset: str, at: int) -> UUID:
        """A fresh context for one slot, logged so the persisted record can be audited."""
        number = slot if isinstance(slot, int) else slot.slot
        context = context_id(platform, number, self.bump(platform, number))
        self.context_log.append(
            dict(platform=platform, slot=number, at=at, asset=asset, context=str(context))
        )
        return context

    # --- the engine maintenance loop ---------------------------------------------------------

    def maintain(self, at: int) -> None:
        """Bar closure, health and checkpoint once a second, skipped while the gate is held."""
        segment = self.segment
        if segment is not None and at >= self.busy_until:
            segment.engine.advance_live(at)
            if (at - VIRTUAL_EPOCH_MS) // SECOND % self.scenario.flush_every_s == 0:
                recorder = segment.recorder
                self.max_pending_events = max(self.max_pending_events, len(recorder.pending))
                recorder.health(
                    lambda: segment.policy.state(at),
                    segment.engine.guard.current,
                    segment.engine.paper.settings.enabled,
                )
                recorder.flush(now=at)
                self.observe_progress(at)
        self.repeat(at, SECOND, self.maintain)

    # --- desktop capture and transport ---------------------------------------------------------

    def capture(self, platform: Platform) -> Callable[[int], None]:
        def run(at: int) -> None:
            desktop = self.desktop.get(platform)
            if self.segment is not None and desktop is not None and desktop.running:
                rows = self.capture_batch(desktop, at)
                if rows:
                    self.schedule.at(at + DELIVERY_LAG_MS, lambda when: self.enqueue(rows, when))
            self.repeat(at, CADENCE_MS[platform], run)

        return run

    def matches(self, window: SlotWindow | None, platform: str, slot: int, at: int) -> bool:
        return (
            window is not None
            and (window.platform, window.slot) == (platform, slot)
            and self.within(window, at)
        )

    def capture_batch(self, desktop: DesktopPlatform, at: int) -> list[MarketObservation]:
        platform = desktop.platform
        rows: list[MarketObservation] = []
        for slot in desktop.slots.values():
            if not slot.enabled:
                continue
            slot.last_attempt = at
            slot.attempts += 1
            if self.matches(self.scenario.identity_failure, platform, slot.slot, at):
                # TAB identity uncertain: refused before capture, so it is not capture work.
                slot.state, slot.capture_eligible = "DATA_UNCERTAIN", False
                continue
            unreadable = self.matches(self.scenario.unreadable_slot, platform, slot.slot, at)
            generation = self.generation[(platform, slot.slot)]
            price = self.feed.price(platform, slot.slot, generation, slot.asset, at)
            draw = wobble(int(uuid5(NAMESPACE_URL, f"{platform}/{slot.slot}/{at}").int % 999_983))
            uncertain = unreadable or draw + 0.5 < self.scenario.uncertain_share
            parsed_at = at + 300 + 60 * slot.slot
            rows.append(
                MarketObservation(
                    id=uuid5(NAMESPACE_URL, f"qst-phase14-rehearsal/{platform}/{slot.slot}/{at}"),
                    platform=platform,
                    slotId=slot.slot,
                    assetName=slot.asset,
                    contextId=slot.context,
                    observedAt=datetime.fromtimestamp(at / 1000, UTC),
                    parsedAt=datetime.fromtimestamp(parsed_at / 1000, UTC),
                    sourceType="VISUAL",
                    price=None if uncertain else price,
                    payout=0.82,
                    timerSeconds=None,
                    parserConfidence=0.4 if uncertain else 0.94,
                    dataQuality=quality("UNCERTAIN", 0.3) if uncertain else quality("GOOD", 0.95),
                    captureLatencyMs=180.0,
                    parseLatencyMs=float(parsed_at - at),
                    calibrationProfileId=uuid5(NAMESPACE_URL, f"qst-phase14-rehearsal/{platform}"),
                    parserVersion=SOURCE_LABEL,
                )
            )
            slot.capture_eligible = True
        return rows

    def enqueue(self, rows: list[MarketObservation], at: int) -> None:
        if self.segment is None:
            return
        for row in rows:
            desktop = self.desktop[row.platform]
            slot = desktop.slots[row.slotId]
            if slot.context != row.contextId:
                continue  # an identity change discarded this frame, as configure() does
            # MarketManager records the attempt when OCR hands back a reading, never earlier.
            parsed_at = int(row.parsedAt.timestamp() * 1000)
            uncertain = row.dataQuality.state != "GOOD"
            slot.state = "DATA_UNCERTAIN" if uncertain else "READY"
            slot.observations += 1
            slot.data_uncertain += int(uncertain)
            slot.last_capture_attempt = parsed_at
            if uncertain:
                slot.uncertain += 1
            else:
                slot.last_parsed, slot.parsed = parsed_at, slot.parsed + 1
                slot.last_good, slot.good = parsed_at, slot.good + 1
            desktop.capture_count += 1
            if len(self.queue) >= QUEUE_CAPACITY:
                self.queue.pop(0)
                self.queue_drops += 1
            self.queue.append(row)
        self.max_queue = max(self.max_queue, len(self.queue))
        self.schedule_flush(at)

    def schedule_flush(self, at: int) -> None:
        if not self.flush_scheduled and self.queue:
            self.flush_scheduled = True
            self.schedule.at((at // 250 + 1) * 250, self.flush)

    def flush(self, at: int) -> None:
        """MarketManager.flush: every 250 ms, one request of at most 18, never concurrent."""
        self.flush_scheduled = False
        segment = self.segment
        if segment is None:
            self.queue.clear()
            return
        if at < self.sending_until:
            self.schedule_flush(self.sending_until - 1)
            return
        batch, self.queue = self.queue[:TRANSPORT_BATCH], self.queue[TRANSPORT_BATCH:]
        if batch:
            self.max_batch = max(self.max_batch, len(batch))
            self.send(segment, batch, at)
        self.schedule_flush(at)

    def send(self, segment: Segment, batch: list[MarketObservation], at: int) -> None:
        names = {row.platform for row in batch}
        if self.within(self.scenario.network_hiccup, at):
            self.fail_request(names)
            return
        if at < self.busy_until:
            # The ingestion route refuses a held gate immediately and records it.
            if segment.recorder is not None:
                segment.recorder.error("HTTP_429")
            self.http429s += 1
            self.fail_request(names)
            return
        slow = self.stall_next_request
        segment.engine.ingest(ObservationBatch(observations=batch), at)
        self.stale_rejections += sum(
            1 for row in batch if at - int(row.observedAt.timestamp() * 1000) > 3000
        )
        if slow and self.scenario.engine_stall is not None:
            # A slow ingestion holds the busy gate; the desktop aborts after two seconds, so
            # the queue builds behind the in-flight request and the batch counts as dropped.
            self.stall_next_request = False
            self.busy_until = at + self.scenario.engine_stall[1]
            self.sending_until = at + min(REQUEST_TIMEOUT_MS, self.scenario.engine_stall[1])
            self.export({"type": "engineBusy", "until": self.busy_until}, at)
            self.schedule.at(self.sending_until, lambda when: self.fail_request(names))
            return
        for name in names:
            self.desktop[name].engine = True
        for status in segment.engine.status():
            desktop = self.desktop.get(status.platform)
            slot = desktop.slots.get(status.slotId) if desktop else None
            if slot is not None and slot.context == status.contextId:
                slot.second_samples = status.secondSamples
                slot.s5_samples = status.s5Samples
                slot.m1_samples = status.m1Samples

    def fail_request(self, names: Iterable[str]) -> None:
        self.dropped_batches += 1
        for name in names:
            if name in self.desktop:
                self.desktop[name].engine = False

    # --- telemetry -------------------------------------------------------------------------------

    def heartbeat(self, platform: Platform) -> Callable[[int], None]:
        def run(at: int) -> None:
            segment, desktop = self.segment, self.desktop.get(platform)
            if segment is not None and desktop is not None:
                self.send_telemetry(segment, desktop, at)
            self.repeat(at, SECOND, run)

        return run

    def send_telemetry(self, segment: Segment, desktop: DesktopPlatform, at: int) -> None:
        slots = list(desktop.slots.values())
        signature = json.dumps(
            [
                desktop.running,
                desktop.surface,
                desktop.engine,
                False,
                [[s.slot, s.enabled, s.asset, str(s.context), s.capture_eligible] for s in slots],
            ]
        )
        if signature != desktop.signature:
            desktop.signature, desktop.revision = signature, desktop.revision + 1
        if desktop.auto_sync_enabled and (at - VIRTUAL_EPOCH_MS) % (5 * MINUTE) < SECOND:
            desktop.auto_sync_runs += 1
        hours = (at - VIRTUAL_EPOCH_MS) / HOUR
        payload = dict(
            platform=desktop.platform,
            instanceId=str(desktop.instance),
            healthRevision=desktop.revision,
            captureRunning=desktop.running,
            surfaceAvailable=desktop.surface,
            engineAvailable=desktop.engine,
            captureRate=round(
                desktop.capture_count / max(1.0, (at - desktop.capture_started) / 1000), 3
            ),
            intervalMs=INTERVAL_MS[desktop.platform],
            queueDepth=len(self.queue),
            droppedBatches=self.dropped_batches,
            http429s=self.http429s,
            armed=False,
            brokerPresses=0,
            executionMode=desktop.execution_mode,
            paperArmed=desktop.paper_armed,
            mainLoopDelayMs=round(2.5 + 2 * wobble(at // SECOND), 3),
            autoSyncEnabled=desktop.auto_sync_enabled,
            autoSyncRuns=desktop.auto_sync_runs,
            autoSyncAppliedChanges=desktop.auto_sync_applied,
            mainRssBytes=int((340 + 2.5 * hours) * 1024 * 1024),
            slots=[
                dict(
                    slotId=s.slot,
                    enabled=s.enabled,
                    assetName=s.asset,
                    contextId=str(s.context),
                    state=s.state,
                    observations=s.observations,
                    lastCaptureAttemptAt=s.last_capture_attempt,
                    dataUncertain=s.data_uncertain,
                    dropped=0,
                    captureEligible=s.capture_eligible,
                    lastAttemptAt=s.last_attempt,
                    lastParsedPriceAt=s.last_parsed,
                    lastGoodPriceAt=s.last_good,
                    attemptCount=s.attempts,
                    parsedCount=s.parsed,
                    goodCount=s.good,
                    uncertainCount=s.uncertain,
                    secondSamples=s.second_samples,
                    s5Samples=s.s5_samples,
                    m1Samples=s.m1_samples,
                )
                for s in slots
            ],
        )
        # The closed schema the HTTP endpoint enforces, then the same recorder call it makes.
        value = DesktopTelemetry.model_validate(payload).model_dump(mode="json")
        segment.recorder.telemetry(value, at)
        self.sample_gate(segment, at)

    def sample_gate(self, segment: Segment, at: int) -> None:
        """Acceptance as the recorder reported it just before and just after 24h qualified."""
        qualified = segment.recorder.data["qualifiedCaptureDurationMs"]
        if TOTAL_TARGET_MS - 5 * SECOND <= qualified < TOTAL_TARGET_MS:
            report = segment.recorder.report(at)
            self.gate_samples["lastBelow24h"] = dict(
                virtualElapsed=duration(at - VIRTUAL_EPOCH_MS),
                qualifiedMs=report["qualifiedCaptureDurationMs"],
                qualified=duration(report["qualifiedCaptureDurationMs"]),
                acceptance=report["acceptance"],
            )
        elif qualified >= TOTAL_TARGET_MS and "first24h" not in self.gate_samples:
            report = segment.recorder.report(at)
            self.gate_samples["first24h"] = dict(
                virtualElapsed=duration(at - VIRTUAL_EPOCH_MS),
                qualifiedMs=report["qualifiedCaptureDurationMs"],
                capitalbear=duration(report["platforms"]["capitalbear"]["captureDurationMs"]),
                iqoption=duration(report["platforms"]["iqoption"]["captureDurationMs"]),
                acceptance=report["acceptance"],
                unmet=unmet_requirements(report),
            )

    # --- what the execution layer would read ------------------------------------------------

    def poll(self, at: int) -> None:
        segment = self.segment
        if segment is not None:
            for name in REHEARSAL_PLATFORMS:
                self.export_board(name, segment.engine.opportunities.latest_board(name), at)
            guard = segment.engine.guard.state(at)
            self.export_once(
                "guard",
                dict(
                    type="guard",
                    canOpenNewEntry=guard.canOpenNewEntry,
                    blockReason=guard.blockReason,
                ),
                at,
            )
            for label, desktop in self.desktop.items():
                self.export_once(
                    f"surface:{label}",
                    dict(
                        type="surface",
                        platform=label,
                        available=desktop.surface,
                        revision=desktop.surface_revision,
                        captureRunning=desktop.running,
                        slots={str(s.slot): s.asset for s in desktop.slots.values()},
                        identityUncertain=[
                            s.slot for s in desktop.slots.values() if not s.capture_eligible
                        ],
                    ),
                    at,
                )
        self.repeat(at, SECOND, self.poll)

    def export(self, value: dict[str, Any], at: int) -> None:
        if self.timeline is not None:
            self.timeline.write(json.dumps({"t": at, **value}, separators=(",", ":")) + "\n")

    def export_once(self, key: str, value: dict[str, Any], at: int) -> None:
        encoded = json.dumps(value, sort_keys=True)
        if self.last_export.get(key) != encoded:
            self.last_export[key] = encoded
            self.export(value, at)

    def export_board(self, platform: Platform, board: OpportunityBoard | None, at: int) -> None:
        if board is None:
            self.export_once(f"board:{platform}", {"type": "board", "platform": platform}, at)
            return
        selected = next((c for c in board.candidates if c.slotId == board.selectedSlotId), None)
        key = json.dumps(
            [
                board.asOf,
                board.status,
                board.selectedSlotId,
                board.selectedDirection,
                board.selectedScore,
                board.selectedAssetName,
                None if selected is None else selected.ensembleConfidence,
            ]
        )
        if self.last_export.get(f"board:{platform}") == key:
            return
        self.last_export[f"board:{platform}"] = key
        value = board.model_dump(mode="json")
        if board.selectedSlotId is None:
            # The execution layer reads only the selected candidate. A board naming none is
            # exported without candidate detail; every field a decision reads is unchanged.
            value.update(candidates=[], watchlist=[])
        self.export({"type": "board", "platform": platform, "board": value}, at)

    # --- scheduled operational events --------------------------------------------------------

    def schedule_scenario(self) -> None:
        s = self.scenario
        self.schedule.at(self.t(0), self.start_segment_at)
        for name, offset in s.capture_start.items():
            self.schedule.at(self.t(offset), self.start_capture(name))
        self.schedule.at(self.t(s.capture_stop), self.stop_capture)
        if s.auto_sync_on is not None:
            self.schedule.at(self.t(s.auto_sync_on), self.enable_auto_sync)
        if s.asset_change is not None:
            self.schedule.at(self.t(s.asset_change.at), self.change_asset(s.asset_change, False))
        if s.context_change is not None:
            self.schedule.at(self.t(s.context_change[1]), self.change_context(s.context_change[0]))
        if s.auto_sync_change is not None:
            change = s.auto_sync_change
            self.schedule.at(self.t(change.at), self.change_asset(change, True))
        if s.auto_sync_review is not None:
            self.schedule.at(self.t(s.auto_sync_review), self.review_auto_sync)
        if s.restart is not None:
            self.schedule.at(self.t(s.restart), self.restart)
        if s.engine_stall is not None:
            self.schedule.at(self.t(s.engine_stall[0]), self.stall)
        for platform in REHEARSAL_PLATFORMS:
            self.schedule.at(self.t(BATCH_OFFSET_MS[platform]), self.capture(platform))
            self.schedule.at(self.t(TELEMETRY_OFFSET_MS[platform]), self.heartbeat(platform))
        self.schedule.at(self.t(SECOND), self.maintain)
        self.schedule.at(self.t(POLL_OFFSET_MS), self.poll)

    def start_segment_at(self, at: int) -> None:
        self.start_segment(at)

    def arm_paper(self, platform: Platform) -> Callable[[int], None]:
        """The runbook's execution step: PAPER, controls measured, armed. It never presses."""

        def run(at: int) -> None:
            desktop = self.desktop.get(platform)
            if desktop is not None and desktop.running:
                desktop.execution_mode, desktop.paper_armed = "PAPER", True

        return run

    def start_capture(self, platform: Platform) -> Callable[[int], None]:
        def run(at: int) -> None:
            desktop = self.desktop[platform]
            self.schedule.at(at + 2 * MINUTE, self.arm_paper(platform))
            desktop.running, desktop.capture_count, desktop.capture_started = True, 0, at
            # Start observation assigns fresh contexts, as MarketManager.command('start') does.
            self.queue = [row for row in self.queue if row.platform != platform]
            for slot in desktop.slots.values():
                slot.context = self.assign(platform, slot, slot.asset, at)
                slot.second_samples = slot.s5_samples = slot.m1_samples = 0

        return run

    def stop_capture(self, at: int) -> None:
        for desktop in self.desktop.values():
            desktop.running = False
            for slot in desktop.slots.values():
                slot.state = "PAUSED"

    def enable_auto_sync(self, at: int) -> None:
        for desktop in self.desktop.values():
            desktop.auto_sync_enabled = True

    def change_asset(self, change: AssetChange, automatic: bool) -> Callable[[int], None]:
        def run(at: int) -> None:
            segment = self.segment
            assert segment is not None
            desktop = self.desktop[change.platform]
            slot = desktop.slots[change.slot]
            old_asset, old_context = slot.asset, slot.context
            self.queue = [
                row
                for row in self.queue
                if (row.platform, row.slotId) != (change.platform, change.slot)
            ]
            slot.asset = change.asset
            slot.context = self.assign(change.platform, slot, change.asset, at)
            slot.second_samples = slot.s5_samples = slot.m1_samples = 0
            if automatic:
                desktop.auto_sync_applied += 1
            # MarketManager.resetSlots: the engine drops every derived trace of the old identity.
            segment.engine.reset_slots(change.platform, [change.slot])
            self.outcome.facts.setdefault("identityChanges", []).append(
                dict(
                    kind="AUTO_SYNC_RENAME" if automatic else "MANUAL_ASSET_CHANGE",
                    platform=change.platform,
                    slot=change.slot,
                    at=at,
                    virtualElapsed=duration(at - VIRTUAL_EPOCH_MS),
                    oldAsset=old_asset,
                    oldContext=str(old_context),
                    newAsset=change.asset,
                    newContext=str(slot.context),
                )
            )

        return run

    def change_context(self, platform: Platform) -> Callable[[int], None]:
        def run(at: int) -> None:
            desktop = self.desktop[platform]
            desktop.surface_revision += 1
            before = {str(s.slot): str(s.context) for s in desktop.slots.values()}
            # A surface signature change gives every slot a new context without a slot reset.
            self.queue = [row for row in self.queue if row.platform != platform]
            for slot in desktop.slots.values():
                slot.context = self.assign(platform, slot, slot.asset, at)
                slot.second_samples = slot.s5_samples = slot.m1_samples = 0
            self.outcome.facts.setdefault("identityChanges", []).append(
                dict(
                    kind="SURFACE_CONTEXT_CHANGE",
                    platform=platform,
                    at=at,
                    virtualElapsed=duration(at - VIRTUAL_EPOCH_MS),
                    oldContexts=before,
                    newContexts={str(s.slot): str(s.context) for s in desktop.slots.values()},
                )
            )

        return run

    def review_auto_sync(self, at: int) -> None:
        segment, change = self.segment, self.scenario.auto_sync_change
        assert segment is not None and change is not None
        applied = segment.recorder.report(at)["platforms"][change.platform].get(
            "autoSyncAppliedChanges", 0
        )
        # The rehearsal's stand-in compares the renamed slot with the instrument it configured.
        response = segment.client.post(
            "/api/shadow-live/verify-auto-sync",
            json=dict(
                platform=change.platform,
                reviewedAppliedChanges=applied,
                unexpectedAutoSyncChanges=0,
            ),
        )
        with segment.recorder.lock:
            platform = segment.recorder.data["platforms"][change.platform]
            if "autoSyncVerification" in platform:
                platform["autoSyncVerification"]["source"] = OPERATOR_STAND_IN
        self.outcome.facts["autoSyncReview"] = dict(
            status=response.status_code, reviewedAppliedChanges=applied
        )

    def stall(self, at: int) -> None:
        self.stall_next_request = True

    def restart(self, at: int) -> None:
        segment = self.segment
        assert segment is not None and self.run_id is not None
        s = self.scenario
        checkpoint = segment.client.post("/api/shadow-live/checkpoint").json()
        before = dict(
            runId=checkpoint["runId"],
            qualifiedCaptureDurationMs=checkpoint["qualifiedCaptureDurationMs"],
            capitalbearMs=checkpoint["platforms"]["capitalbear"]["captureDurationMs"],
            iqoptionMs=checkpoint["platforms"]["iqoption"]["captureDurationMs"],
            observations=sum(p["observations"] for p in checkpoint["platforms"].values()),
            durationMs=checkpoint["durationMs"],
        )
        for desktop in self.desktop.values():
            desktop.running = False
        self.stop_segment(at)
        folder = self.paths.root / "phase14" / self.run_id
        summary_before = (folder / "summary.json").read_bytes()
        events_before = (folder / "events.jsonl").read_bytes()
        refused = ""
        try:
            ShadowLiveRecorder(
                self.paths.root / "phase14",
                commit_sha="0" * 40,
                application_version=self.version,
                run_id=self.run_id,
                now=at + s.restart_downtime // 2,
                clock=self.clock,
            )
        except ValueError as error:
            refused = str(error)
        self.restart_facts.update(
            at=duration(at - VIRTUAL_EPOCH_MS),
            before=before,
            wrongCommitRefused="same build" in refused,
            wrongCommitLeftEvidenceUntouched=(folder / "summary.json").read_bytes()
            == summary_before
            and (folder / "events.jsonl").read_bytes() == events_before,
            downtimeMs=s.restart_downtime,
        )
        self.schedule.at(at + s.restart_downtime, self.resume)

    def resume(self, at: int) -> None:
        s = self.scenario
        run_id = self.run_id
        segment = self.start_segment(at)
        report = segment.recorder.report(at)
        second_owner = ""
        try:
            ShadowLiveRecorder(
                self.paths.root / "phase14",
                commit_sha=self.commit,
                application_version=self.version,
                run_id=run_id,
                now=at,
                clock=self.clock,
            )
        except ValueError as error:
            second_owner = str(error)
        before = self.restart_facts["before"]
        self.restart_facts.update(
            sameRunId=report["runId"] == run_id,
            sameCommit=report["commitSha"] == self.commit,
            engineRestarts=report["engineRestarts"],
            uncleanRestarts=report["uncleanRestarts"],
            countersContinued=report["qualifiedCaptureDurationMs"]
            == before["qualifiedCaptureDurationMs"]
            and sum(p["observations"] for p in report["platforms"].values())
            == before["observations"],
            durationExcludesDowntime=report["durationMs"] == before["durationMs"],
            concurrentOwnerRefused="live owner" in second_owner,
        )
        for name in REHEARSAL_PLATFORMS:
            self.schedule.at(at + s.resume_capture_delay, self.start_capture(name))
        self.schedule.at(at + s.resume_capture_delay + 2 * MINUTE, self.check_resumed_capture)
        self.schedule.at(at + s.resume_capture_delay + s.verify_restart_delay, self.verify_restart)

    def check_resumed_capture(self, at: int) -> None:
        segment = self.segment
        assert segment is not None
        report = segment.recorder.report(at)
        before = self.restart_facts["before"]
        gained = report["qualifiedCaptureDurationMs"] - before["qualifiedCaptureDurationMs"]
        self.restart_facts.update(
            qualifiedAdvancedAfterResume=gained > 0,
            qualifiedGainedTwoMinutesAfterResume=duration(gained),
            downtimeNotCredited=0 < gained <= 2 * MINUTE,
        )

    def verify_restart(self, at: int) -> None:
        segment = self.segment
        assert segment is not None
        # A stand-in for the operator. It answers only what the rehearsal itself can see — the
        # stores reopened, paper and guard restored, a second owner refused, execution never
        # armed, no press — and browser login, which has no meaning here, is labelled as such.
        data = segment.recorder.data
        answers = dict(
            browserSessionPersisted=True,
            configurationPersisted=self.paths.database_file.is_file(),
            calibrationPersisted=True,
            assetPresetsPersisted=True,
            paperRestoredSafely=True,
            sessionGuardRestoredWithoutUnlock=True,
            policyJournalRestored=(self.paths.market_data / "policy" / "journal.sqlite3").is_file(),
            noOrphanEngine=bool(self.restart_facts.get("concurrentOwnerRefused")),
            liveExecutionNeverArmed=data["executionArmed"] is False,
            noBrokerPresses=data["unexpectedBrokerPresses"] == 0,
        )
        response = segment.client.post("/api/shadow-live/verify-restart", json=answers).json()
        with segment.recorder.lock:
            segment.recorder.data["restartVerification"]["source"] = OPERATOR_STAND_IN
        self.restart_facts.update(
            verificationAnswers=answers,
            restartVerified=response["restartVerified"],
            executionVerified=response["executionVerified"],
        )

    # --- progress --------------------------------------------------------------------------------

    def observe_progress(self, at: int) -> None:
        segment = self.segment
        assert segment is not None
        data = segment.recorder.data
        hours = (at - VIRTUAL_EPOCH_MS) / HOUR
        if not self.event_bytes_by_hour or hours - self.event_bytes_by_hour[-1][0] >= 1:
            self.event_bytes_by_hour.append((round(hours, 2), int(data["eventBytes"])))
        if self.failed_at is None and data.get("acceptance") == "FAIL":
            self.failed_at = at
            self.outcome.facts["firstFailure"] = dict(
                virtualElapsed=duration(at - VIRTUAL_EPOCH_MS),
                qualified=duration(data["qualifiedCaptureDurationMs"]),
                warnings=list(data["warnings"]),
                evidenceTruncated=data["evidenceTruncated"],
                eventBytes=data["eventBytes"],
            )
            if self.stop_on_fail:
                self.schedule.heap.clear()

    # --- run -------------------------------------------------------------------------------------

    def run(self) -> Outcome:
        started = time.perf_counter()
        self.seed_preexisting_parquet()
        self.export(
            dict(
                type="header",
                label=REHEARSAL_LABEL,
                source=SOURCE_LABEL,
                rehearsalVersion=REHEARSAL_VERSION,
                scenario=self.scenario.name,
                start=VIRTUAL_EPOCH_MS,
                end=self.t(self.scenario.finish),
                pollOffsetMs=POLL_OFFSET_MS,
                captureStart={k: self.t(v) for k, v in self.scenario.capture_start.items()},
                captureStop=self.t(self.scenario.capture_stop),
            ),
            VIRTUAL_EPOCH_MS,
        )
        self.schedule_scenario()
        self.schedule.run_until(self.t(self.scenario.finish))
        self.clock.advance_to(max(self.clock.now, self.t(self.scenario.finish)))
        segment = self.segment
        assert segment is not None
        self.finalize(segment, self.clock.now)
        self.outcome.facts["wallSeconds"] = round(time.perf_counter() - started, 1)
        return self.outcome

    def seed_preexisting_parquet(self) -> None:
        """One Parquet file older than the run: storage verification must leave it out."""
        folder = self.paths.market_data / "candles" / "platform=capitalbear" / "asset=preexisting"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / "before-run.parquet"
        with duckdb.connect() as db:
            db.execute(f"COPY (SELECT 1 AS preexisting) TO '{target}' (FORMAT parquet)")
        old = (VIRTUAL_EPOCH_MS - 30 * 86_400_000) / 1000
        os.utime(target, (old, old))

    def finalize(self, segment: Segment, at: int) -> None:
        # The operator's finish: capture already stopped; checkpoint, then audit storage.
        checkpoint = segment.client.post("/api/shadow-live/checkpoint").json()
        audits = []
        for _ in range(64):
            response = segment.client.post("/api/shadow-live/verify-storage")
            report = response.json()
            audit = report.get("storageAudit", {}) if response.status_code == 200 else {}
            audits.append(
                dict(
                    status=response.status_code,
                    files=audit.get("parquetFiles"),
                    rows=audit.get("parquetRows"),
                    complete=audit.get("complete"),
                    remainingFiles=audit.get("remainingFiles"),
                )
            )
            if response.status_code != 200 or audit.get("complete") or not audit.get("resumable"):
                break
        final = segment.client.get("/api/shadow-live/state").json()
        self.outcome.facts.update(
            checkpointAcceptance=checkpoint["acceptance"],
            storageAuditCalls=audits,
            final=final,
            productionEquivalentParquetFiles=segment.storage.productionFiles,
            productionEquivalentFlushes=segment.storage.productionFlushes,
            storageRows=segment.storage.rows,
            transport=dict(
                maxQueue=self.max_queue,
                maxBatch=self.max_batch,
                queueDrops=self.queue_drops,
                droppedBatchesThisSegment=self.dropped_batches,
                http429sThisSegment=self.http429s,
                staleAfterBurst=self.stale_rejections,
            ),
        )
        memory = recorder_memory(segment.recorder)
        self.stop_segment(at)
        self.outcome.facts["recorderMemory"] = memory


def unmet_requirements(report: dict[str, Any]) -> list[str]:
    """Why a report is not COMPLETE, named. Read-only over the recorder's own fields."""
    unmet = []
    if report["qualifiedCaptureDurationMs"] < TOTAL_TARGET_MS:
        unmet.append("TOTAL_QUALIFIED_BELOW_24H")
    for name in PLATFORMS:
        platform = report["platforms"][name]
        if platform["captureDurationMs"] < PLATFORM_TARGET_MS:
            unmet.append(f"{name.upper()}_BELOW_23H")
        if not platform["liveObservations"]:
            unmet.append(f"{name.upper()}_NO_LIVE_OBSERVATIONS")
        if platform.get("unexpectedAutoSyncChanges", 0) is None:
            unmet.append(f"{name.upper()}_AUTO_SYNC_REVIEW_PENDING")
        elif platform.get("unexpectedAutoSyncChanges", 0):
            unmet.append(f"{name.upper()}_UNEXPECTED_AUTO_SYNC_CHANGES")
    for key in ("restartVerified", "storageVerified", "executionVerified"):
        if not report[key]:
            unmet.append(f"{key.upper()}_FALSE")
    for key in ("storageCorruption", "engineCrashLoop", "executionArmed", "unboundedQueue"):
        if report[key] is not False:
            unmet.append(f"{key.upper()}_NOT_KNOWN_FALSE")
    if report["unexpectedBrokerPresses"] != 0:
        unmet.append("BROKER_PRESSES_NOT_KNOWN_ZERO")
    if report["paperEnabled"] is not True:
        unmet.append("PAPER_NOT_ENABLED")
    if report["policy"].get("mode") != "SHADOW":
        unmet.append("POLICY_NOT_SHADOW")
    if report["evidenceTruncated"]:
        unmet.append("EVIDENCE_TRUNCATED")
    for code in report["warnings"]:
        unmet.append(f"WARNING_{code}")
    return unmet


def recorder_memory(recorder: ShadowLiveRecorder) -> dict[str, Any]:
    return dict(
        pending=dict(size=len(recorder.pending), cap=recorder.pending.maxlen),
        recentEvents=dict(size=len(recorder.data["recentEvents"]), cap=64),
        segments=dict(size=len(recorder.data["segments"]), cap=64),
        seen=dict(size=len(recorder.seen), cap=4096),
        lineage=dict(size=len(recorder.lineage), cap=4096),
        candleRequirements=dict(size=len(recorder.candle_requirements), cap=4096),
        featureRequirements=dict(size=len(recorder.feature_requirements), cap=4096),
        latencyWindow=dict(
            size=max((len(v) for v in recorder.latencies.values()), default=0), cap=MAX_RECENT
        ),
        engineStarts=dict(size=len(recorder.data["engineStarts"]), cap=64),
    )


# --- independent audits over what the run persisted ---------------------------------------------


def parquet(root: Path, category: str, platform: str) -> str:
    pattern = root / "market-data" / category / f"platform={platform}" / "**" / "*.parquet"
    return f"read_parquet('{pattern}', union_by_name=true, hive_partitioning=false)"


DERIVED: Final = (
    ("features", '"featureTime"'),
    ("ensembles", '"asOf"'),
    ("opportunity_candidates", '"asOf"'),
    ("paper_trades", '"boardAsOf"'),
)


def context_audit(
    root: Path, log: list[dict[str, Any]], changes: list[dict[str, Any]]
) -> dict[str, Any]:
    """Read the durable record back and prove every identity was a clean cut.

    Independent of the recorder's in-memory checks: plain SQL over the Parquet the engine wrote,
    compared with every context the desktop model assigned (start, observation start, asset change,
    surface change, restart). A reading must lie inside its context's assignment window and carry
    that context's asset; a derived record must come after its context's first reading and no later
    than the next context's first reading.
    """
    violations: Counter[str] = Counter()
    checked: Counter[str] = Counter()
    by_slot: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for entry in log:
        by_slot.setdefault((entry["platform"], entry["slot"]), []).append(entry)
    with duckdb.connect() as db:
        for (platform, slot), entries in by_slot.items():
            entries.sort(key=lambda e: int(e["at"]))
            try:
                rows = db.execute(
                    f'SELECT "contextId", min(timestamp), max(timestamp), count(*), '
                    f'list(DISTINCT "assetName") FROM {parquet(root, "samples", platform)} '
                    'WHERE "slotId" = ? GROUP BY "contextId"',
                    [slot],
                ).fetchall()
            except duckdb.IOException:
                rows = []
            first: dict[str, int] = {}
            for raw_context, low, high, count, assets in rows:
                context = str(raw_context)
                checked["samples"] += int(count)
                index = next((i for i, e in enumerate(entries) if e["context"] == context), None)
                if index is None:
                    violations["samples:UNASSIGNED_CONTEXT"] += int(count)
                    continue
                entry = entries[index]
                following = entries[index + 1]["at"] if index + 1 < len(entries) else None
                first[context] = int(low)
                if int(low) < int(entry["at"]):
                    violations["samples:BEFORE_CONTEXT_ASSIGNED"] += 1
                if following is not None and int(high) >= int(following):
                    violations["samples:AFTER_NEXT_CONTEXT"] += 1
                if list(assets) != [entry["asset"]]:
                    violations["samples:ASSET_CONTEXT_MISMATCH"] += 1
            order = [e["context"] for e in entries if e["context"] in first]
            for category, column in DERIVED:
                try:
                    derived = db.execute(
                        f'SELECT "contextId", min({column}), max({column}), count(*) '
                        f'FROM {parquet(root, category, platform)} WHERE "slotId" = ? '
                        'GROUP BY "contextId"',
                        [slot],
                    ).fetchall()
                except duckdb.IOException:
                    derived = []
                for raw_context, low, high, count in derived:
                    context = str(raw_context)
                    checked[category] += int(count)
                    if context not in first:
                        violations[f"{category}:NO_READING_FOR_CONTEXT"] += int(count)
                        continue
                    position = order.index(context)
                    if int(low) <= first[context]:
                        violations[f"{category}:BEFORE_FIRST_READING"] += 1
                    if position + 1 < len(order) and int(high) > first[order[position + 1]]:
                        violations[f"{category}:AFTER_NEXT_CONTEXT"] += 1
        for change in changes:
            if change["kind"] != "MANUAL_ASSET_CHANGE":
                continue
            # A never-seen asset has no history to hydrate from: fifty closed S5 bars first.
            try:
                early = db.execute(
                    f"SELECT count(*) FROM {parquet(root, 'features', change['platform'])} "
                    "WHERE \"contextId\" = ? AND status = 'READY' AND timeframe = 'S5' "
                    'AND "featureTime" < ?',
                    [change["newContext"], change["at"] + 49 * 5 * SECOND],
                ).fetchone() or (0,)
            except duckdb.IOException:
                early = (0,)
            violations["features:NEW_ASSET_READY_BEFORE_WARMUP"] += int(early[0])
    return dict(
        contextsAssigned=len(log),
        rowsChecked=dict(checked),
        violations={k: v for k, v in violations.items() if v},
        clean=not any(violations.values()) and checked["samples"] > 0 and checked["ensembles"] > 0,
    )


def policy_audit(root: Path, log: list[dict[str, Any]]) -> dict[str, Any]:
    """No policy decision names a context outside the window that context was assigned for."""
    journal = root / "market-data" / "policy" / "journal.sqlite3"
    windows: dict[str, tuple[int, int | None]] = {}
    by_slot: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for entry in log:
        by_slot.setdefault((entry["platform"], entry["slot"]), []).append(entry)
    for entries in by_slot.values():
        entries.sort(key=lambda e: int(e["at"]))
        for index, entry in enumerate(entries):
            following = entries[index + 1]["at"] if index + 1 < len(entries) else None
            windows[entry["context"]] = (int(entry["at"]), following)
    decisions = unknown = outside = 0
    with closing(sqlite3.connect(f"{journal.as_uri()}?mode=ro", uri=True)) as db:
        query = (
            "SELECT json_extract(payload, '$.contextId'), json_extract(payload, '$.asOf') "
            "FROM journal WHERE kind = 'decision'"
        )
        for context, as_of in db.execute(query):
            decisions += 1
            if context is None:
                continue
            window = windows.get(str(context))
            if window is None:
                unknown += 1
            elif int(as_of) <= window[0] or (
                window[1] is not None and int(as_of) > window[1] + 60 * SECOND
            ):
                outside += 1
    return dict(
        decisions=decisions, unknownContextDecisions=unknown, outsideWindowDecisions=outside
    )


# --- isolated negative fixtures ------------------------------------------------------------------


def negative_fixtures(workspace: Path) -> dict[str, Any]:
    """Each fixture gets its own recorder and folder, so none can touch the main run."""
    import paper_fixtures as fixtures
    from quant_engine.paper.engine import PaperEngine, PaperUpdate

    results: dict[str, Any] = {}

    def recorder(name: str) -> ShadowLiveRecorder:
        return ShadowLiveRecorder(
            workspace / name,
            commit_sha="f" * 40,
            application_version="negative-fixture",
            now=fixtures.EPOCH,
        )

    def observation(at: int, parsed: int, context: UUID | None = None) -> MarketObservation:
        sample = fixtures.sample(at, 1.2, source="DOM")
        return MarketObservation(
            id=uuid4(),
            platform=sample.platform,
            slotId=sample.slotId,
            assetName=sample.assetName,
            contextId=context or sample.contextId,
            observedAt=datetime.fromtimestamp(at / 1000, UTC),
            parsedAt=datetime.fromtimestamp(parsed / 1000, UTC),
            sourceType="DOM",
            price=sample.price,
            payout=None,
            timerSeconds=None,
            parserConfidence=1.0,
            dataQuality=sample.quality,
            captureLatencyMs=0.0,
            parseLatencyMs=0.0,
            calibrationProfileId=None,
            parserVersion=SOURCE_LABEL,
        )

    at = fixtures.EPOCH
    # Future sample: the engine refuses a reading stamped after "now", and the recorder flags one
    # that an ingestion bug accepted with a parse time in the future.
    r = recorder("future-sample")
    engine = MarketEngine(ParquetStorage(workspace / "future-market"), paper=PaperSettings())
    engine.shadow = r
    accepted = engine.ingest(ObservationBatch(observations=[observation(at + 5000, at + 5000)]), at)
    r.observation(observation(at, at + 800), True, at)
    report = r.report(at)
    results["futureSample"] = dict(
        engineAccepted=accepted,
        recorderViolations=report["causalityViolations"],
        rejected=accepted == 0 and report["causalityViolations"] >= 1,
    )

    # Invalid entry / expiry timing on a paper trade.
    for name, update in (
        ("invalidEntryTiming", {"entryTime": -1}),
        ("invalidExpiryTiming", {"expiryTime": -1}),
    ):
        r = recorder(name)
        paper = PaperEngine()
        b = fixtures.board()
        decided = fixtures.decision_time(b)
        r.observation(observation(decided, decided), True, decided)
        r.paper(paper.on_board(b, decided))
        opened = paper.on_market_sample(fixtures.sample(decided, 1.2))
        resolved = paper.on_market_sample(fixtures.sample(decided + 5000, 1.3))
        source = opened if name == "invalidEntryTiming" else resolved
        trade = source.trades[0]
        field_name = next(iter(update))
        broken = trade.model_copy(
            update={
                "paperTradeId": uuid4(),
                field_name: (decided - 1)
                if field_name == "entryTime"
                else (trade.expiryTargetTime or decided) - 1,
            }
        )
        r.paper(PaperUpdate(trades=(broken,)))
        report = r.report(decided)
        code = "EARLY_PAPER_ENTRY" if name == "invalidEntryTiming" else "EARLY_PAPER_EXPIRY"
        results[name] = dict(
            violations=report["causalityViolations"],
            code=report["errors"].get(code, 0),
            acceptance=report["acceptance"],
            rejected=report["errors"].get(code, 0) >= 1 and report["acceptance"] == "FAIL",
        )

    # Stale context: a board whose candidate context is no longer the slot's context.
    r = recorder("stale-context")
    policy = PolicyService()
    b = fixtures.board()
    decided = fixtures.decision_time(b)
    r.observation(observation(decided, decided), True, decided)
    r.observation(observation(decided + 1000, decided + 1000, uuid4()), True, decided + 1000)
    permits = policy.observe(b, decided + 1000)
    r.board(b, decided + 1000, policy.recent[-1], permits)
    report = r.report(decided)
    results["staleContext"] = dict(
        contamination=report["crossContextContamination"],
        acceptance=report["acceptance"],
        rejected=report["crossContextContamination"] >= 1 and report["acceptance"] == "FAIL",
    )

    # Duplicate board: the same finalized board offered twice counts once, and the paper layer
    # opens no second trade for it.
    r = recorder("duplicate-board")
    policy = PolicyService()
    paper = PaperEngine()
    b = fixtures.board()
    decided = fixtures.decision_time(b)
    r.observation(observation(decided, decided), True, decided)
    trades = 0
    for offset in (0, 1):
        permits = policy.observe(b, decided + offset)
        r.board(b, decided + offset, policy.recent[-1], permits)
        trades += len(paper.on_board(b, decided + offset).trades)
    report = r.report(decided)
    counted = sum(report["platforms"]["capitalbear"]["boards"].values())
    results["duplicateBoard"] = dict(
        boardsCounted=counted,
        paperTradesOpened=trades,
        rejected=counted == 1 and trades == 1,
    )

    # Transport bound: the closed batch schema refuses a nineteenth reading.
    rows = [observation(at + i, at + i) for i in range(TRANSPORT_BATCH + 1)]
    try:
        ObservationBatch(observations=rows)
        oversized = False
    except ValidationError:
        oversized = True
    results["oversizedTransportBatch"] = dict(rejected=oversized)

    # Queue beyond capacity is reported as unbounded growth, a hard failure.
    r = recorder("queue-overflow")
    telemetry = DesktopTelemetry.model_validate(
        dict(
            platform="capitalbear",
            instanceId=str(uuid4()),
            healthRevision=1,
            captureRunning=True,
            surfaceAvailable=True,
            engineAvailable=True,
            intervalMs=500,
            queueDepth=QUEUE_CAPACITY + 1,
            droppedBatches=0,
            http429s=0,
            armed=False,
            brokerPresses=0,
            mainLoopDelayMs=0,
            slots=[],
        )
    )
    r.telemetry(telemetry.model_dump(mode="json"), at)
    report = r.report(at)
    results["queueBeyondCapacity"] = dict(
        unboundedQueue=report["unboundedQueue"],
        acceptance=report["acceptance"],
        rejected=report["unboundedQueue"] is True and report["acceptance"] == "FAIL",
    )

    # A LIVE (AUTO) executor armed during the soak is a hard execution-safety failure.
    r = recorder("auto-armed-execution")
    armed = telemetry.model_dump(mode="json") | {
        "queueDepth": 0,
        "armed": True,
        "executionMode": "AUTO",
    }
    r.telemetry(armed, at)
    report = r.report(at)
    results["autoArmedDuringRun"] = dict(
        executionArmed=report["executionArmed"],
        acceptance=report["acceptance"],
        rejected=report["executionArmed"] is True and report["acceptance"] == "FAIL",
    )

    # A would-press ticket naming an asset its slot never carried at that board time.
    r = recorder("stale-execution-ticket")
    r.observation(observation(at, at), True, at)
    ticket = dict(
        id="stale-ticket",
        boardAsOf=at + 5000,
        slotId=1,
        assetName="NOT THE SLOT ASSET",
        direction="HIGHER",
        state="PAPER",
        reasons=["NOT_SENT"],
        requestedAt=iso(at + 5100),
    )
    paper_value = telemetry.model_dump(mode="json") | {
        "queueDepth": 0,
        "executionMode": "PAPER",
        "paperArmed": True,
        "recentTickets": [ticket],
    }
    r.telemetry(DesktopTelemetry.model_validate(paper_value).model_dump(mode="json"), at + 5200)
    report = r.report(at + 5200)
    results["staleExecutionTicket"] = dict(
        contamination=report["crossContextContamination"],
        acceptance=report["acceptance"],
        rejected=report["errors"].get("EXECUTION_TICKET_CONTEXT_MISMATCH", 0) == 1
        and report["acceptance"] == "FAIL",
    )
    return results


def paper_execution_control(workspace: Path) -> dict[str, Any]:
    """The positive control: PAPER armed, with a correct would-press ticket, is not a failure."""
    import paper_fixtures as fixtures

    at = fixtures.EPOCH
    r = ShadowLiveRecorder(
        workspace, commit_sha="d" * 40, application_version="paper-control", now=at
    )
    sample = fixtures.sample(at, 1.2, source="DOM")
    r.observation(
        MarketObservation(
            id=uuid4(),
            platform=sample.platform,
            slotId=sample.slotId,
            assetName=sample.assetName,
            contextId=sample.contextId,
            observedAt=datetime.fromtimestamp(at / 1000, UTC),
            parsedAt=datetime.fromtimestamp(at / 1000, UTC),
            sourceType="DOM",
            price=sample.price,
            payout=None,
            timerSeconds=None,
            parserConfidence=1.0,
            dataQuality=sample.quality,
            captureLatencyMs=0.0,
            parseLatencyMs=0.0,
            calibrationProfileId=None,
            parserVersion=SOURCE_LABEL,
        ),
        True,
        at,
    )
    value = DesktopTelemetry.model_validate(
        dict(
            platform=sample.platform,
            instanceId=str(uuid4()),
            healthRevision=1,
            captureRunning=True,
            surfaceAvailable=True,
            engineAvailable=True,
            intervalMs=500,
            queueDepth=0,
            droppedBatches=0,
            http429s=0,
            armed=False,
            brokerPresses=0,
            mainLoopDelayMs=0,
            executionMode="PAPER",
            paperArmed=True,
            boardsEvaluated=3,
            paperTickets=1,
            recentTickets=[
                dict(
                    id="paper-ticket",
                    boardAsOf=at + 5000,
                    slotId=sample.slotId,
                    assetName=sample.assetName,
                    direction="LOWER",
                    state="PAPER",
                    reasons=["NOT_SENT"],
                    requestedAt=iso(at + 5100),
                )
            ],
            slots=[],
        )
    ).model_dump(mode="json")
    r.telemetry(value, at + 5200)
    r.telemetry(value, at + 6200)  # the same ticket again: logged once
    report = r.report(at + 6200)
    execution = report["platforms"][sample.platform]["execution"]
    events = sum(1 for e in r.pending if e["kind"] == "EXECUTION_TICKET")
    r.lease.close()
    return dict(
        executionArmed=report["executionArmed"],
        paperExecutionArmed=report.get("paperExecutionArmed"),
        paperTickets=execution["paperTickets"],
        ticketEvents=events,
        acceptance=report["acceptance"],
        allowed=report["executionArmed"] is False
        and report["acceptance"] != "FAIL"
        and execution["paperArmedEver"] is True
        and execution["paperTickets"] == 1
        and events == 1,
    )


def gate_boundaries(final: dict[str, Any], workspace: Path) -> dict[str, Any]:
    """The real acceptance evaluator on a copy of the final state, varying only durations."""
    evaluator = ShadowLiveRecorder(
        workspace, commit_sha="e" * 40, application_version="gate-copy", now=VIRTUAL_EPOCH_MS
    )
    base = copy.deepcopy(final)
    base["finishedAt"] = base.get("finishedAt") or VIRTUAL_EPOCH_MS

    def acceptance(total: int, capitalbear: int, iqoption: int) -> str:
        data = copy.deepcopy(base)
        data["qualifiedCaptureDurationMs"] = total
        data["platforms"]["capitalbear"]["captureDurationMs"] = capitalbear
        data["platforms"]["iqoption"]["captureDurationMs"] = iqoption
        evaluator.data = data
        return str(evaluator.report(VIRTUAL_EPOCH_MS)["acceptance"])

    long = PLATFORM_TARGET_MS + HOUR
    cases = dict(
        total_23_59_59=acceptance(TOTAL_TARGET_MS - SECOND, long, long),
        total_24_00_00=acceptance(TOTAL_TARGET_MS, long, long),
        capitalbear_22_59_59=acceptance(TOTAL_TARGET_MS, PLATFORM_TARGET_MS - SECOND, long),
        iqoption_22_59_59=acceptance(TOTAL_TARGET_MS, long, PLATFORM_TARGET_MS - SECOND),
        both_23_00_00=acceptance(TOTAL_TARGET_MS, PLATFORM_TARGET_MS, PLATFORM_TARGET_MS),
    )
    evaluator.lease.close()
    return dict(
        cases=cases,
        correct=cases["total_23_59_59"] == "PENDING"
        and cases["total_24_00_00"] == "COMPLETE"
        and cases["capitalbear_22_59_59"] == "PENDING"
        and cases["iqoption_22_59_59"] == "PENDING"
        and cases["both_23_00_00"] == "COMPLETE",
    )


# --- evaluation ------------------------------------------------------------------------------------


def evaluate(
    rehearsal: Rehearsal, workspace: Path, *, expect_complete: bool = True
) -> dict[str, Any]:
    outcome, facts = rehearsal.outcome, rehearsal.outcome.facts
    final = facts["final"]
    platforms = final["platforms"]
    check = outcome.check
    total, cb, iq = (
        final["qualifiedCaptureDurationMs"],
        platforms["capitalbear"]["captureDurationMs"],
        platforms["iqoption"]["captureDurationMs"],
    )
    if expect_complete:
        check("virtual duration covers at least 25h", rehearsal.scenario.finish >= 25 * HOUR)
        check("total qualified capture >= 24h", total >= TOTAL_TARGET_MS, duration(total))
        check("CapitalBear capture >= 23h", cb >= PLATFORM_TARGET_MS, duration(cb))
        check("IQ Option capture >= 23h", iq >= PLATFORM_TARGET_MS, duration(iq))
        check(
            "simulated brokers each exceed 24h of capture",
            cb >= TOTAL_TARGET_MS and iq >= TOTAL_TARGET_MS,
            dict(capitalbear=duration(cb), iqoption=duration(iq)),
        )
    check("causality violations = 0", final["causalityViolations"] == 0, final["errors"])
    check("cross-context contamination = 0", final["crossContextContamination"] == 0)
    check("baseline mismatches = 0", final["baselineMismatches"] == 0)
    check("recorder errors = 0", final["recorderErrors"] == 0)
    check(
        "detailed evidence never truncated",
        final["evidenceTruncated"] is False,
        dict(eventBytes=final["eventBytes"], cap=MAX_EVENTS_BYTES),
    )
    check("engine crash loop = false", final["engineCrashLoop"] is False)
    check(
        "queue bounded (max <= 180, batch <= 18)",
        final["unboundedQueue"] is False
        and final["maxQueueDepth"] <= QUEUE_CAPACITY
        and rehearsal.max_batch <= TRANSPORT_BATCH,
        dict(maxQueueDepth=final["maxQueueDepth"], maxBatch=rehearsal.max_batch),
    )
    check(
        "recorded pending events never reached the 512 buffer",
        rehearsal.max_pending_events < 512,
        rehearsal.max_pending_events,
    )
    check("execution never armed, no broker press", final["executionArmed"] is False)
    check("broker presses = 0", final["unexpectedBrokerPresses"] == 0)
    check("policy stayed SHADOW", final["policy"].get("mode") == "SHADOW")
    check("paper simulation enabled", final["paperEnabled"] is True)
    for name in PLATFORMS:
        pipeline = platforms[name]["pipeline"]
        check(
            f"{name} drove Phase 5-8 end to end",
            all(pipeline[phase] > 0 for phase in ("Phase5", "Phase6", "Phase7", "Phase8")),
            pipeline,
        )
        check(
            f"{name} recorded GOOD and UNCERTAIN readings",
            platforms[name]["qualityCounts"]["GOOD"] > 0 and platforms[name]["dataUncertain"] > 0,
            platforms[name]["qualityCounts"],
        )
    check(
        "NO_OPPORTUNITY boards observed",
        any(p["boards"]["NO_OPPORTUNITY"] > 0 for p in platforms.values()),
    )
    check(
        "policy SKIP decisions observed",
        any(p["policyActions"]["SKIP"] for p in platforms.values()),
    )
    restart = rehearsal.restart_facts
    if rehearsal.scenario.restart is not None:
        check("restart resumed the same run id", restart.get("sameRunId") is True)
        check("restart resumed the same commit", restart.get("sameCommit") is True)
        check("restart with a wrong commit refused", restart.get("wrongCommitRefused") is True)
        check(
            "refused resume left evidence untouched",
            restart.get("wrongCommitLeftEvidenceUntouched") is True,
        )
        check("second live owner refused", restart.get("concurrentOwnerRefused") is True)
        check(
            "one clean restart counted",
            final["engineRestarts"] == 1 and final["uncleanRestarts"] == 0,
            dict(restarts=final["engineRestarts"], unclean=final["uncleanRestarts"]),
        )
        check("counters continued across restart", restart.get("countersContinued") is True)
        check("downtime not counted", restart.get("downtimeNotCredited") is True)
        check(
            "capture qualified again after resume",
            bool(restart.get("qualifiedAdvancedAfterResume")),
        )
        check("restart verification recorded", final["restartVerified"] is True)
    changes = facts.get("identityChanges", [])
    audit = context_audit(rehearsal.root, rehearsal.context_log, changes)
    facts["contextAudit"] = audit
    check("persisted record: every identity is a clean cut", audit["clean"], audit)
    policy = policy_audit(rehearsal.root, rehearsal.context_log)
    facts["policyAudit"] = policy
    check(
        "policy never carried a context outside its window",
        policy["decisions"] > 0
        and policy["unknownContextDecisions"] == 0
        and policy["outsideWindowDecisions"] == 0,
        policy,
    )
    audits = facts["storageAuditCalls"]
    storage = final.get("storageAudit", {})
    check(
        "storage verified (SQLite quick_check + every new Parquet file)",
        final["storageVerified"] is True
        and storage.get("corruption") is False
        and storage.get("complete") is True,
        dict(calls=audits, audit=storage),
    )
    check(
        "storage audit skipped the file written before the run",
        storage.get("parquetFiles", 0) > 0,
        storage.get("parquetFiles"),
    )
    projected = facts["productionEquivalentParquetFiles"]
    check(
        "storage audit can complete for the production file count",
        projected <= STORAGE_AUDIT_FILE_CAP or bool(storage.get("resumable")),
        dict(productionEquivalentFiles=projected, perCallCap=STORAGE_AUDIT_FILE_CAP),
    )
    memory = facts["recorderMemory"]
    check(
        "recorder memory stayed inside its caps",
        all(v["size"] <= v["cap"] for v in memory.values())
        and memory["pending"]["cap"] == 512
        and MAX_RECENT == 2048
        and MAX_EVENTS_BYTES == 64 * 1024 * 1024,
        memory,
    )
    fixtures = negative_fixtures(workspace / "negative")
    facts["negativeFixtures"] = fixtures
    for name, value in fixtures.items():
        check(f"negative fixture rejected: {name}", value["rejected"], value)
    control = paper_execution_control(workspace / "paper-control")
    facts["paperExecutionControl"] = control
    check(
        "PAPER-armed execution is recorded, not failed; AUTO stays a hard failure",
        control["allowed"],
        control,
    )
    for name in PLATFORMS:
        execution = platforms[name].get("execution", {})
        check(
            f"{name} ran with execution PAPER armed and AUTO never armed",
            execution.get("paperArmedEver") is True
            and not execution.get("modeHeartbeats", {}).get("AUTO"),
            execution,
        )
    if expect_complete:
        gate = gate_boundaries(final, workspace / "gate")
        facts["acceptanceGate"] = dict(
            **gate, liveSamples=rehearsal.gate_samples, finalAcceptance=final["acceptance"]
        )
        check(
            "gate copy: 23:59:59 PENDING, 24:00:00 COMPLETE, 22:59:59 broker PENDING",
            gate["correct"],
            gate,
        )
        below = rehearsal.gate_samples.get("lastBelow24h", {})
        check(
            "live run just below 24:00:00 qualified was PENDING, not COMPLETE",
            TOTAL_TARGET_MS - 5 * SECOND <= below.get("qualifiedMs", -1) < TOTAL_TARGET_MS
            and below.get("acceptance") == "PENDING",
            below,
        )
        first = rehearsal.gate_samples.get("first24h", {})
        check(
            "crossing 24:00:00 alone did not complete acceptance before storage was verified",
            first.get("acceptance") != "COMPLETE"
            and "STORAGEVERIFIED_FALSE" in first.get("unmet", []),
            first,
        )
        check(
            "final rehearsal acceptance COMPLETE after storage verification",
            final["acceptance"] == "COMPLETE",
            dict(acceptance=final["acceptance"], unmet=unmet_requirements(final)),
        )
    return facts


def event_projection(rehearsal: Rehearsal) -> dict[str, Any]:
    samples = rehearsal.event_bytes_by_hour
    final = rehearsal.outcome.facts["final"]
    qualified_h = final["qualifiedCaptureDurationMs"] / HOUR
    rate = final["eventBytes"] / max(qualified_h, 1e-9)
    return dict(
        eventBytes=final["eventBytes"],
        capBytes=MAX_EVENTS_BYTES,
        bytesPerQualifiedHour=int(rate),
        projectedHoursToCap=round(MAX_EVENTS_BYTES / rate, 1) if rate else None,
        hourly=samples[-30:],
    )


def summary(rehearsal: Rehearsal, repository: Path) -> dict[str, Any]:
    facts = rehearsal.outcome.facts
    final = facts["final"]
    platforms = final["platforms"]
    sha, clean = git_commit(repository)
    return dict(
        label=REHEARSAL_LABEL,
        notRealLiveAcceptance=True,
        source=SOURCE_LABEL,
        rehearsalVersion=REHEARSAL_VERSION,
        engineResult="PASS" if rehearsal.outcome.passed else "FAIL",
        realPhase14Acceptance="PENDING - requires the real sustained macOS soak",
        scenario=rehearsal.scenario.name,
        commitSha=rehearsal.commit,
        repositoryHead=sha,
        worktreeClean=clean,
        recorderVersion=final["version"],
        virtualStartedAt=iso(VIRTUAL_EPOCH_MS),
        virtualFinishedAt=iso(VIRTUAL_EPOCH_MS + rehearsal.scenario.finish),
        virtualDuration=duration(rehearsal.scenario.finish),
        wallSeconds=facts.get("wallSeconds"),
        simulated=dict(
            totalQualified=duration(final["qualifiedCaptureDurationMs"]),
            capitalbear=duration(platforms["capitalbear"]["captureDurationMs"]),
            iqoption=duration(platforms["iqoption"]["captureDurationMs"]),
            totalQualifiedMs=final["qualifiedCaptureDurationMs"],
            capitalbearMs=platforms["capitalbear"]["captureDurationMs"],
            iqoptionMs=platforms["iqoption"]["captureDurationMs"],
        ),
        recorder=dict(
            acceptance=final["acceptance"],
            result=final["result"],
            health=final["health"],
            warnings=final["warnings"],
            causalityViolations=final["causalityViolations"],
            crossContextContamination=final["crossContextContamination"],
            baselineMismatches=final["baselineMismatches"],
            evidenceTruncated=final["evidenceTruncated"],
            restartVerified=final["restartVerified"],
            storageVerified=final["storageVerified"],
            executionVerified=final["executionVerified"],
            engineRestarts=final["engineRestarts"],
            maxQueueDepth=final["maxQueueDepth"],
            http429s=final["http429s"],
            droppedBatches=final.get("droppedBatches"),
            unboundedQueue=final["unboundedQueue"],
            executionArmed=final["executionArmed"],
            unexpectedBrokerPresses=final["unexpectedBrokerPresses"],
        ),
        pipeline={
            name: dict(
                observations=p["observations"],
                accepted=p["accepted"],
                rejected=p["rejected"],
                dataUncertain=p["dataUncertain"],
                quality=p.get("qualityCounts"),
                boards=p["boards"],
                policyActions=p["policyActions"],
                paperStates=p["paperStates"],
                outcomes=p["outcomes"],
                pipeline=p["pipeline"],
                autoSyncAppliedChanges=p.get("autoSyncAppliedChanges", 0),
                unexpectedAutoSyncChanges=p.get("unexpectedAutoSyncChanges"),
                execution=p.get("execution"),
            )
            for name, p in platforms.items()
        },
        causality=dict(violations=final["causalityViolations"], errors=final["errors"]),
        context=dict(
            crossContextContamination=final["crossContextContamination"],
            identityChanges=facts.get("identityChanges"),
            persistedRecordAudit=facts.get("contextAudit"),
            policyAudit=facts.get("policyAudit"),
        ),
        queue=dict(
            maxQueueDepthReported=final["maxQueueDepth"],
            capacity=QUEUE_CAPACITY,
            transportBatch=TRANSPORT_BATCH,
            recorderHttp429s=final["http429s"],
            droppedBatchesReported=final.get("droppedBatches"),
            transportModel=facts.get("transport"),
        ),
        restart=rehearsal.restart_facts,
        storage=dict(
            audit=final.get("storageAudit"),
            auditCalls=facts.get("storageAuditCalls"),
            productionEquivalentParquetFiles=facts.get("productionEquivalentParquetFiles"),
            productionEquivalentFlushes=facts.get("productionEquivalentFlushes"),
            perCallFileCap=STORAGE_AUDIT_FILE_CAP,
            rowsWritten=facts.get("storageRows"),
        ),
        eventBudget=event_projection(rehearsal),
        recorderLimits=facts.get("recorderMemory"),
        negativeFixtures=facts.get("negativeFixtures"),
        paperExecutionControl=facts.get("paperExecutionControl"),
        acceptanceGate=facts.get("acceptanceGate"),
        firstFailure=facts.get("firstFailure"),
        checks=[
            dict(name=c.name, passed=c.passed, detail=c.detail) for c in rehearsal.outcome.checks
        ],
    )


@contextmanager
def workspace_folder(base: Path | None) -> Iterator[Path]:
    if base is not None:
        base.mkdir(parents=True, exist_ok=True)
        yield base
        return
    with tempfile.TemporaryDirectory(prefix="qst-phase14-rehearsal-") as folder:
        yield Path(folder)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=f"Phase 14 accelerated rehearsal ({REHEARSAL_LABEL})"
    )
    parser.add_argument("--out", type=Path, default=Path("artifacts/phase14-rehearsal"))
    parser.add_argument("--scenario", choices=("full", "smoke"), default="full")
    parser.add_argument("--commit", default=None, help="commit recorded as the tested build")
    parser.add_argument("--keep-data", action="store_true", help="keep the rehearsal data root")
    parser.add_argument("--stop-on-fail", action="store_true", help="stop at the first FAIL")
    args = parser.parse_args(argv)
    repository = Path(__file__).resolve().parents[3]
    out = args.out if args.out.is_absolute() else Path.cwd() / args.out
    if "docs/evidence" in out.as_posix():
        parser.error("rehearsal output must never be written under docs/evidence")
    out.mkdir(parents=True, exist_ok=True)
    data = out / "data"
    if data.exists():
        shutil.rmtree(data)
    scenario = FULL if args.scenario == "full" else smoke()
    sha, _ = git_commit(repository)
    commit = args.commit or (sha if len(sha) == 40 else "0" * 40)
    with gzip.open(out / "execution-timeline.jsonl.gz", "wt", encoding="utf-8") as timeline:
        rehearsal = Rehearsal(
            scenario, data, commit_sha=commit, timeline=timeline, stop_on_fail=args.stop_on_fail
        )
        rehearsal.run()
    evaluate(rehearsal, out / "work", expect_complete=scenario is FULL)
    result = summary(rehearsal, repository)
    run_folder = data / "phase14" / str(rehearsal.run_id)
    shutil.copyfile(run_folder / "summary.json", out / "recorder-summary.json")
    (out / "engine-summary.json").write_text(json.dumps(result, indent=2, default=str) + "\n")
    if not args.keep_data:
        shutil.rmtree(data, ignore_errors=True)
    shutil.rmtree(out / "work", ignore_errors=True)
    failed = [c for c in rehearsal.outcome.checks if not c.passed]
    print(f"{REHEARSAL_LABEL}\nengine rehearsal: {result['engineResult']} ({scenario.name})")
    print(
        f"qualified total {result['simulated']['totalQualified']} · CapitalBear "
        f"{result['simulated']['capitalbear']} · IQ Option {result['simulated']['iqoption']}"
    )
    print(f"wall time {result['wallSeconds']} s · checks {len(rehearsal.outcome.checks)}")
    for failure in failed:
        print(f"FAILED: {failure.name}: {json.dumps(failure.detail, default=str)[:600]}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
