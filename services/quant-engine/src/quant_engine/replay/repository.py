"""Durable replay artefacts, under the run's own namespace and nowhere else (Phase 11).

The one rule this module exists to enforce: **a replay never writes into the live record.**
Every file it creates lives under ``<market-data>/replay/<replayRunId>/``, which no live reader
looks at — Phase 9's paper history, Phase 9.5's daily sessions and Phase 10's analytics
snapshots are read from their own directories, and nothing here can reach them. A backtest that
could contaminate the evidence it is supposed to be tested against would be worse than no
backtest.

Files are written atomically and named by the deterministic run id, so replaying the same
history under the same settings rewrites the same files with the same content instead of
accumulating near-duplicates.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel

from quant_engine.replay.models import (
    ReplayEvidence,
    ReplayManifest,
    ReplayRun,
    ReplaySummary,
)

FOLDER = "replay"
MANIFEST = "manifest.json"
RUN = "run.json"
SUMMARY = "summary.json"
WALK_FORWARD = "walk-forward.json"
EQUITY = "equity.json"
EVIDENCE = "evidence.json"
MARKET = "market"

MAX_RETAINED = 50
"""Bounded on purpose. A replay is reproducible from its source and its manifest — that is what
the deterministic run id is for — so keeping an unbounded pile of results would spend disk on
something that is never the source of truth."""

MAX_EQUITY_POINTS = 20_000


def replay_root(market_data: Path) -> Path:
    return market_data / FOLDER


def run_dir(market_data: Path, run_id: UUID) -> Path:
    return replay_root(market_data) / str(run_id)


def write(path: Path, payload: Any) -> Path:
    """One file, atomically. A half-written result must never be readable as a whole one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f"{uuid4()}.tmp"
    temporary.write_text(json.dumps(payload, sort_keys=True))
    temporary.replace(path)
    return path


def _dump(model: BaseModel) -> Any:
    return model.model_dump(mode="json")


def save_manifest(market_data: Path, run_id: UUID, manifest: ReplayManifest) -> Path:
    return write(run_dir(market_data, run_id) / MANIFEST, _dump(manifest))


def save_run(market_data: Path, run: ReplayRun) -> Path:
    return write(run_dir(market_data, run.replayRunId) / RUN, _dump(run))


def save_summary(market_data: Path, summary: ReplaySummary) -> Path:
    folder = run_dir(market_data, summary.replayRunId)
    if summary.walkForward is not None:
        write(folder / WALK_FORWARD, _dump(summary.walkForward))
    return write(folder / SUMMARY, _dump(summary))


def save_equity(market_data: Path, run_id: UUID, points: list[Any]) -> Path:
    return write(run_dir(market_data, run_id) / EQUITY, points[:MAX_EQUITY_POINTS])


def save_evidence(market_data: Path, evidence: ReplayEvidence) -> Path:
    """Persist the Phase 12 artefact. Persisting it is the whole action; nothing reads it."""
    return write(run_dir(market_data, evidence.replayRunId) / EVIDENCE, _dump(evidence))


def _read(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def load_run(market_data: Path, run_id: UUID) -> ReplayRun | None:
    payload = _read(run_dir(market_data, run_id) / RUN)
    if payload is None:
        return None
    try:
        return ReplayRun.model_validate(payload)
    except ValueError:
        return None


def load_summary(market_data: Path, run_id: UUID) -> ReplaySummary | None:
    payload = _read(run_dir(market_data, run_id) / SUMMARY)
    if payload is None:
        return None
    try:
        return ReplaySummary.model_validate(payload)
    except ValueError:
        return None


def load_equity(market_data: Path, run_id: UUID) -> list[Any]:
    payload = _read(run_dir(market_data, run_id) / EQUITY)
    return payload if isinstance(payload, list) else []


def load_walk_forward(market_data: Path, run_id: UUID) -> Any | None:
    return _read(run_dir(market_data, run_id) / WALK_FORWARD)


def list_runs(market_data: Path, limit: int = 20) -> list[ReplayRun]:
    """Stored runs, newest first. A directory that cannot be read is skipped, never guessed at."""
    root = replay_root(market_data)
    if not root.is_dir():
        return []
    runs: list[ReplayRun] = []
    for folder in sorted(root.iterdir()):
        if not folder.is_dir():
            continue
        payload = _read(folder / RUN)
        if payload is None:
            continue
        try:
            runs.append(ReplayRun.model_validate(payload))
        except ValueError:
            continue
    runs.sort(key=lambda item: item.createdAt, reverse=True)
    return runs[:limit]


def trim(market_data: Path, keep: int = MAX_RETAINED) -> int:
    """Drop the oldest results beyond the retention bound. Never touches the live record."""
    root = replay_root(market_data)
    if not root.is_dir():
        return 0
    folders = [folder for folder in sorted(root.iterdir()) if folder.is_dir()]
    stamped: list[tuple[int, Path]] = []
    for folder in folders:
        payload = _read(folder / RUN)
        created = int(payload.get("createdAt", 0)) if isinstance(payload, dict) else 0
        stamped.append((created, folder))
    stamped.sort()
    removed = 0
    for _, folder in stamped[: max(0, len(stamped) - keep)]:
        _remove(folder)
        removed += 1
    return removed


def _remove(folder: Path) -> None:
    for path in sorted(folder.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            path.rmdir()
    folder.rmdir()
