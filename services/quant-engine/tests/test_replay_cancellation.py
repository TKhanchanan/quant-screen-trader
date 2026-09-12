"""T-EZ: cancellation is a property of the job, not of whatever phase happened to be running.

A replay runs a baseline first and then a research scenario per configured latency delay. The
failure this file exists to prevent is quiet and plausible: the baseline finishes, the operator
cancels, the scenarios never run — and the job reports COMPLETED on the strength of a baseline
that was only half of what was asked for. Partial evidence presented as acceptance evidence is
worse than no evidence, because nobody can tell by looking.

Every test here is driven by explicit hooks rather than by sleeping and hoping. ``run_replay``
reports each phase as it begins, so a test can raise the cancel flag at an exact point and then
assert precisely which phases ran and which did not.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import replay_fixtures as fixtures
from quant_engine.paper.policy import PaperSettings
from quant_engine.replay import (
    InMemoryObservationSource,
    ReplayEngine,
    ReplayManifest,
    ReplayReport,
    run_replay,
)

ACCOUNTING = PaperSettings(paperCurrency="THB", paperStake=50, paperPayoutRate=0.82)
DELAYS = [100, 250, 500]


def manifest(**overrides: Any) -> ReplayManifest:
    values: dict[str, Any] = {
        "warmupDurationMs": 0,
        "sourceMode": "SYNTHETIC",
        "includeIqOption": False,
        "paperSettings": ACCOUNTING,
        "latencyScenarios": DELAYS,
    }
    values.update(overrides)
    return ReplayManifest(**values)


class Run:
    """One ``run_replay`` driven by a cancel flag a test raises at a named phase."""

    def __init__(self, cancel_at: str | None, root: Path, **overrides: Any) -> None:
        self.cancelAt = cancel_at
        self.phases: list[str] = []
        self.cancelled = False
        self.root = root
        self.manifest = manifest(**overrides)

    def _observe(self, engine: ReplayEngine, phase: str) -> None:
        self.phases.append(phase)
        if phase == self.cancelAt:
            self.cancelled = True

    def go(self) -> ReplayReport:
        return run_replay(
            self.manifest,
            market_data=self.root,
            factory=lambda spec: InMemoryObservationSource(
                fixtures.small_history(), mode="SYNTHETIC", platforms=spec.platforms
            ),
            persist=False,
            cancelled=lambda: self.cancelled,
            observe=self._observe,
        )

    @property
    def latencyPhases(self) -> list[str]:
        return [phase for phase in self.phases if phase.startswith("LATENCY_")]


# --- the phases a complete job runs ----------------------------------------------------


def test_an_uncancelled_job_runs_the_baseline_and_every_scenario(tmp_path: Path) -> None:
    run = Run(None, tmp_path)
    report = run.go()
    assert report.run.status == "COMPLETED"
    assert run.latencyPhases == [f"LATENCY_{delay}" for delay in DELAYS]
    assert [row.delayMs for row in report.summary.latency] == [0, *DELAYS]
    assert report.summary.partial is False
    assert "CANCELLED_PARTIAL_RESULT" not in report.summary.warnings


# --- T-EZ1 cancel during the baseline --------------------------------------------------


def test_cancelling_during_the_baseline_ends_the_whole_job(tmp_path: Path) -> None:
    run = Run("BASELINE", tmp_path)
    report = run.go()
    assert report.run.status == "CANCELLED"
    assert run.latencyPhases == []
    assert report.summary.partial is True
    assert "CANCELLED_PARTIAL_RESULT" in report.summary.warnings


# --- T-EZ2 cancel between the baseline and the first scenario --------------------------


def test_cancelling_after_the_baseline_starts_no_scenario_and_still_ends_cancelled(
    tmp_path: Path,
) -> None:
    # The case that used to report COMPLETED: the baseline really did finish, and the job was
    # nonetheless stopped before any of the research beside it ran.
    run = Run("BASELINE_DONE", tmp_path)
    report = run.go()
    assert "BASELINE_DONE" in run.phases
    assert run.latencyPhases == []
    assert report.run.status == "CANCELLED"
    assert report.summary.partial is True
    assert report.summary.latency == []


# --- T-EZ3 cancel while a scenario is running ------------------------------------------


def test_cancelling_inside_a_scenario_stops_it_and_starts_no_later_one(tmp_path: Path) -> None:
    run = Run(f"LATENCY_{DELAYS[0]}", tmp_path)
    report = run.go()
    # The first scenario started and was cut short; the second and third never began.
    assert run.latencyPhases == [f"LATENCY_{DELAYS[0]}"]
    assert report.run.status == "CANCELLED"
    assert report.summary.partial is True
    # A scenario that did not finish is dropped rather than reported half-measured beside the
    # ones that did.
    assert report.summary.latency == []


def test_a_later_scenario_never_begins_once_the_job_is_cancelled(tmp_path: Path) -> None:
    run = Run(f"LATENCY_{DELAYS[1]}", tmp_path)
    report = run.go()
    assert run.latencyPhases == [f"LATENCY_{DELAYS[0]}", f"LATENCY_{DELAYS[1]}"]
    assert f"LATENCY_{DELAYS[2]}" not in run.phases
    assert report.run.status == "CANCELLED"


def test_cancellation_is_sticky_and_never_lifts_itself(tmp_path: Path) -> None:
    # Once raised, the flag is read again at every boundary. Nothing in the job resets it, so a
    # job cannot cancel one scenario and quietly carry on into the next.
    run = Run("BASELINE_DONE", tmp_path)
    run.go()
    assert run.cancelled is True
    assert run.latencyPhases == []


# --- a cancelled job never claims to be complete ---------------------------------------


def test_a_cancelled_job_is_never_presented_as_acceptance_evidence(tmp_path: Path) -> None:
    complete = Run(None, tmp_path / "complete").go()
    stopped = Run("BASELINE_DONE", tmp_path / "stopped").go()
    assert complete.run.status == "COMPLETED" and complete.summary.partial is False
    assert stopped.run.status == "CANCELLED" and stopped.summary.partial is True
    # The baseline work is kept — throwing away what an operator paid for helps nobody — but it
    # is labelled on its own record, in the status and in the warnings.
    assert stopped.summary.overall.resolved > 0
    assert "CANCELLED_PARTIAL_RESULT" in stopped.summary.warnings
    assert stopped.evidence.warnings.count("CANCELLED_PARTIAL_RESULT") == 1
