"""Running one replay at a time, off the request thread (Phase 11).

A replay is heavy offline compute. Three properties keep that from becoming a problem for the
thing that actually matters, which is live capture.

**One at a time.** A second heavy run is refused rather than queued or started alongside; two
replays competing for the same cores would cost the capture loop its samples, and the whole
reason this layer is isolated is so it cannot do that.

**Off the request thread.** A run executes on its own worker so a poll for its progress answers
immediately. Progress is runtime-only and influences nothing: the replay advances on recorded
events whether or not anybody is watching.

**Nothing shared with live.** The service builds its own engine, its own storage and its own
analytics per run, reads the live market record only as input, and writes only under the run's
own namespace. Cancelling a replay stops that replay — it does not stop capture, does not touch
the live daily session, and could not reach an execution surface if it tried.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid4

from quant_engine.analytics.models import AnalyticsRow
from quant_engine.replay import report, repository, walk_forward
from quant_engine.replay.engine import ReplayEngine, ReplayResult
from quant_engine.replay.models import (
    ReplayEvidence,
    ReplayManifest,
    ReplayRun,
    ReplayRunStatus,
    ReplaySummary,
)
from quant_engine.replay.robustness import equity
from quant_engine.replay.source import ParquetObservationSource, ReplaySource

MAX_TRACKED_JOBS = 20

type SourceFactory = Callable[[ReplayManifest], ReplaySource]


class ReplayBusy(RuntimeError):
    """A heavy replay is already running. Deliberately an error rather than a queue."""


@dataclass(slots=True)
class ReplayJob:
    """One running or finished replay, as the API sees it.

    ``jobId`` identifies a *process*, not a result: it is a fresh value per request and never
    appears in a stored artefact. The result's identity is ``replayRunId``, which is
    deterministic over the history and the settings and is the only id anything durable carries.
    """

    jobId: UUID
    manifest: ReplayManifest
    status: ReplayRunStatus = "PENDING"
    phase: str = "PENDING"
    replayRunId: UUID | None = None
    totalEvents: int = 0
    processedEvents: int = 0
    currentMarketTime: int | None = None
    startedAt: int = 0
    finishedAt: int | None = None
    error: str | None = None
    run: ReplayRun | None = None
    summary: ReplaySummary | None = None
    evidence: ReplayEvidence | None = None
    cancelled: bool = field(default=False)

    @property
    def percent(self) -> float | None:
        if not self.totalEvents:
            return None
        return min(1.0, self.processedEvents / self.totalEvents)


@dataclass(slots=True)
class ReplayReport:
    """Everything one complete replay produced."""

    run: ReplayRun
    summary: ReplaySummary
    evidence: ReplayEvidence
    result: ReplayResult


def default_source(market_data: Path) -> SourceFactory:
    """Read the live durable observation record, read-only, as replay input."""

    def factory(manifest: ReplayManifest) -> ReplaySource:
        return ParquetObservationSource(
            market_data,
            platforms=manifest.platforms,
            mode=manifest.sourceMode,
            filters=manifest.sourceFilter,
            label=manifest.sourcePathLabel or "observations",
        )

    return factory


def run_replay(
    manifest: ReplayManifest,
    *,
    market_data: Path,
    factory: SourceFactory,
    persist: bool = True,
    cancelled: Callable[[], bool] | None = None,
    observe: Callable[[ReplayEngine, str], None] | None = None,
) -> ReplayReport:
    """One complete replay: baseline first, then every research study beside it.

    The baseline is always the frozen pipeline at zero delay, and it is never replaced by a
    research scenario. Each latency scenario is a *separate* replay of the same history that
    changes exactly one thing, so the board it was measured on is provably the same board.
    """
    root = repository.replay_root(market_data)
    baseline_engine = ReplayEngine(
        manifest, factory(manifest), root=root, persist=persist, cancelled=cancelled
    )
    if observe is not None:
        observe(baseline_engine, "BASELINE")
    baseline = baseline_engine.run()
    if observe is not None:
        observe(baseline_engine, "BASELINE_DONE")
    scenarios: list[tuple[int, ReplayResult]] = []
    # Cancellation is sticky for the whole job, not for the phase that happened to be running.
    # A baseline that finished before the operator pressed cancel is still a cancelled job: the
    # research studies beside it never ran, so presenting it as a complete result would be
    # presenting partial evidence as acceptance evidence.
    stopped = baseline.run.status == "CANCELLED"
    if baseline.run.status == "COMPLETED":
        for delay in sorted({value for value in manifest.latencyScenarios if value > 0}):
            if cancelled is not None and cancelled():
                stopped = True
                break
            engine = ReplayEngine(
                manifest,
                factory(manifest),
                root=root,
                persist=False,
                cancelled=cancelled,
                decision_delay_ms=delay,
            )
            if observe is not None:
                observe(engine, f"LATENCY_{delay}")
            result = engine.run()
            if result.run.status != "COMPLETED":
                # Whatever this scenario managed is not a scenario. It is dropped rather than
                # reported half-measured beside the ones that finished.
                stopped = True
                break
            scenarios.append((delay, result))
    rows: tuple[AnalyticsRow, ...] = baseline.analysis.rows if baseline.analysis is not None else ()
    folds = (
        walk_forward.analyse(
            rows,
            settings=manifest.walkForward,
            analytics=manifest.analyticsSettings,
            platforms=manifest.platforms,
            paper=manifest.paperSettings,
        )
        if manifest.walkForward.enabled
        else None
    )
    guard = (
        report.guard_report(baseline.sessions)
        if manifest.sessionGuardScenario is not None
        else None
    )
    summary = report.build_summary(
        baseline,
        latency=report.latency_report(baseline, scenarios) if scenarios else (),
        walk_forward=folds,
        guard=guard,
        partial=stopped,
    )
    run = baseline.run if not stopped else baseline.run.model_copy(update={"status": "CANCELLED"})
    evidence = report.build_evidence(baseline, summary)
    if persist:
        repository.save_manifest(market_data, run.replayRunId, manifest)
        repository.save_run(market_data, run)
        repository.save_summary(market_data, summary)
        repository.save_equity(
            market_data,
            run.replayRunId,
            [point.model_dump(mode="json") for point in equity(rows)],
        )
        repository.save_evidence(market_data, evidence)
        repository.trim(market_data)
    return ReplayReport(run=run, summary=summary, evidence=evidence, result=baseline)


class ReplayService:
    """The registry of replay jobs, and the one lock that keeps a second heavy run out."""

    def __init__(self, market_data: Path, factory: SourceFactory | None = None) -> None:
        self.market_data = market_data
        self.factory = factory if factory is not None else default_source(market_data)
        self._lock = threading.Lock()
        self._jobs: dict[UUID, ReplayJob] = {}
        self._order: deque[UUID] = deque(maxlen=MAX_TRACKED_JOBS)
        self._active: ReplayJob | None = None
        self._engine: ReplayEngine | None = None
        self._thread: threading.Thread | None = None

    # --- lifecycle ---------------------------------------------------------------------

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._active is not None

    def start(self, manifest: ReplayManifest) -> ReplayJob:
        """Accept one replay, or refuse because one is already running."""
        job = ReplayJob(jobId=uuid4(), manifest=manifest, startedAt=int(time.time() * 1000))
        with self._lock:
            if self._active is not None:
                raise ReplayBusy("A replay is already running")
            self._active = job
            self._remember(job)
        thread = threading.Thread(target=self._work, args=(job,), daemon=True, name="qst-replay")
        self._thread = thread
        thread.start()
        return job

    def cancel(self, key: UUID) -> bool:
        """Stop the offline replay, and only the offline replay."""
        with self._lock:
            job = self._resolve(key)
            if job is None or job is not self._active:
                return False
            job.cancelled = True
            return True

    def join(self, timeout: float | None = None) -> None:
        """Wait for the worker. Tests and the CLI use this; the API never blocks on it."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    # --- reads -------------------------------------------------------------------------

    def job(self, key: UUID) -> ReplayJob | None:
        with self._lock:
            job = self._resolve(key)
        if job is not None and job is self._active:
            self._refresh(job)
        return job

    def jobs(self) -> list[ReplayJob]:
        with self._lock:
            found = [self._jobs[key] for key in reversed(self._order) if key in self._jobs]
        active = self._active
        if active is not None:
            self._refresh(active)
        return found

    def runs(self, limit: int = 20) -> list[ReplayRun]:
        """Every stored result, newest first, whether or not this process produced it."""
        return repository.list_runs(self.market_data, limit)

    # --- internals ---------------------------------------------------------------------

    def _remember(self, job: ReplayJob) -> None:
        if len(self._order) == self._order.maxlen:
            self._jobs.pop(self._order[0], None)
        self._order.append(job.jobId)
        self._jobs[job.jobId] = job

    def _resolve(self, key: UUID) -> ReplayJob | None:
        job = self._jobs.get(key)
        if job is not None:
            return job
        return next((item for item in self._jobs.values() if item.replayRunId == key), None)

    def _refresh(self, job: ReplayJob) -> None:
        engine = self._engine
        if engine is None:
            return
        job.totalEvents = engine.progress.totalEvents
        job.processedEvents = engine.progress.processedEvents
        job.currentMarketTime = engine.progress.currentMarketTime

    def _work(self, job: ReplayJob) -> None:
        job.status = "RUNNING"
        job.phase = "BASELINE"

        def observe(engine: ReplayEngine, phase: str) -> None:
            self._engine = engine
            job.phase = phase

        try:
            report_ = run_replay(
                job.manifest,
                market_data=self.market_data,
                factory=self.factory,
                cancelled=lambda: job.cancelled,
                observe=observe,
            )
            job.run = report_.run
            job.summary = report_.summary
            job.evidence = report_.evidence
            job.replayRunId = report_.run.replayRunId
            job.status = report_.run.status
            job.error = report_.run.error
        except Exception as failure:
            job.status = "FAILED"
            job.error = f"{type(failure).__name__}: {failure}"[:300]
        finally:
            self._refresh(job)
            job.phase = "DONE"
            job.finishedAt = int(time.time() * 1000)
            self._engine = None
            with self._lock:
                self._active = None
