"""Durable analytics snapshots, beside the market record and never inside it.

A snapshot is a research artefact rather than a market observation, so it is not filed as one:
the Parquet categories hold rows the engine measured, and mixing a derived analysis into them
would let a later reload treat an opinion about the data as more data.

Written as JSON, one file per snapshot, named by the sample it covers and the deterministic
snapshot id. The same history analysed twice writes the same file with the same content, so
recomputation is idempotent and cannot accumulate near-duplicates. Nothing here ever opens a
``PaperTrade`` file for writing: this module only creates files under its own directory.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from quant_engine.analytics.models import AnalyticsSnapshot

FOLDER = "analytics_snapshots"
DEFAULT_LIMIT = 20
MAX_RETAINED = 200
"""Bounded on purpose. Snapshots are cheap to regenerate from the durable record, so keeping an
unbounded pile of them would spend disk on something that is never the source of truth."""


def snapshot_dir(root: Path) -> Path:
    return root / FOLDER


def snapshot_name(snapshot: AnalyticsSnapshot) -> str:
    """Sortable by name alone: the sample's end time, then the deterministic id.

    Deliberately not ordered by modification time. A file's mtime changes when nothing about
    the analysis did and stays put when a snapshot is rewritten from corrected history.
    """
    stamp = snapshot.sampleEnd if snapshot.sampleEnd is not None else 0
    return f"{max(stamp, 0):013d}-{snapshot.snapshotId}.json"


def save_snapshot(root: Path, snapshot: AnalyticsSnapshot) -> Path:
    """Write one snapshot atomically, then trim the oldest beyond the retention bound."""
    folder = snapshot_dir(root)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / snapshot_name(snapshot)
    temporary = folder / f"{uuid4()}.tmp"
    temporary.write_text(json.dumps(snapshot.model_dump(mode="json"), sort_keys=True))
    temporary.replace(target)
    _trim(folder)
    return target


def _trim(folder: Path) -> None:
    files = sorted(folder.glob("*.json"))
    for path in files[:-MAX_RETAINED]:
        path.unlink(missing_ok=True)


def load_snapshots(root: Path, limit: int = DEFAULT_LIMIT) -> list[AnalyticsSnapshot]:
    """Stored snapshots, newest last. A file that cannot be read is skipped, never guessed at."""
    folder = snapshot_dir(root)
    if not folder.is_dir():
        return []
    snapshots: list[AnalyticsSnapshot] = []
    for path in sorted(folder.glob("*.json"))[-limit:]:
        try:
            snapshots.append(AnalyticsSnapshot.model_validate(json.loads(path.read_text())))
        except (OSError, ValueError):
            continue
    return snapshots
