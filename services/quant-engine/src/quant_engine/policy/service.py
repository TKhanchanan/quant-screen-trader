"""Explicit rebuild/activation and durable shadow observation; one worker, no auto-tuning."""

from __future__ import annotations

import sqlite3
from collections import deque
from pathlib import Path
from threading import RLock
from uuid import UUID

from quant_engine.opportunity.models import OpportunityBoard
from quant_engine.paper.models import PaperTrade
from quant_engine.policy.drift import evaluate_watchdog
from quant_engine.policy.engine import PolicyEngine
from quant_engine.policy.evidence import build_snapshot, from_analytics, load_replay
from quant_engine.policy.models import (
    POLICY_VERSION,
    WATCHDOG_VERSION,
    AdaptivePolicyDecision,
    PolicyEvent,
    PolicyEvidence,
    PolicyMode,
    PolicySettings,
    PolicySnapshot,
    WatchdogSnapshot,
    identity,
)
from quant_engine.policy.repository import PolicyRepository
from quant_engine.storage.analytics_repository import load_snapshots

STATE_EVENTS = {
    "POLICY_ACTIVATED",
    "POLICY_DEACTIVATED",
    "POLICY_ROLLED_BACK",
    "POLICY_WITHDRAWN_DRIFT",
}


class PolicyBusy(Exception):
    pass


class PolicyService:
    def __init__(
        self, repository: PolicyRepository | None = None, *, default_mode: PolicyMode = "SHADOW"
    ) -> None:
        self.repository = repository or PolicyRepository()
        self.defaultMode = default_mode
        self.engine = PolicyEngine()
        self.lock = RLock()
        self.busy = False
        self.error: str | None = None
        self.events = self.repository.records("event", PolicyEvent)
        self.recent: deque[AdaptivePolicyDecision] = deque(
            self.repository.records("decision", AdaptivePolicyDecision, limit=100), maxlen=100
        )
        self.snapshots: dict[UUID, PolicySnapshot] = {}
        self.decisions: dict[tuple[str, int, int, UUID | None], AdaptivePolicyDecision] = {}

    def event(
        self,
        kind: str,
        snapshot: UUID | None,
        at: int,
        mode: PolicyMode,
        reasons: tuple[str, ...],
        previous: UUID | None = None,
    ) -> PolicyEvent:
        values = dict(
            eventType=kind,
            snapshotId=snapshot,
            previousPolicySnapshotId=previous,
            at=at,
            mode=mode,
            reasons=reasons,
        )
        # Sequence predecessor makes A->B->A->B distinct while keeping deterministic history.
        event = PolicyEvent.model_validate(
            {
                "eventId": identity(
                    "event", [values, self.events[-1].eventId if self.events else None]
                ),
                **values,
            }
        )
        self.repository.append("event", event.eventId, at, event)
        self.events.append(event)
        return event

    def active_at(
        self, at: int
    ) -> tuple[PolicySnapshot | None, PolicyMode, PolicyEvent | None, bool]:
        event = next(
            (e for e in reversed(self.events) if e.at <= at and e.eventType in STATE_EVENTS), None
        )
        if event is None:
            return None, self.defaultMode, None, False
        snapshot = self.snapshots.get(event.snapshotId) if event.snapshotId else None
        if snapshot is None and event.snapshotId is not None:
            snapshot = self.repository.get("snapshot", event.snapshotId, PolicySnapshot)
            if snapshot is not None:
                self.snapshots[event.snapshotId] = snapshot
        return snapshot, event.mode, event, event.eventType == "POLICY_WITHDRAWN_DRIFT"

    def rebuild(
        self, root: Path, cutoff: int, now: int, settings: PolicySettings
    ) -> PolicySnapshot:
        if not self.lock.acquire(blocking=False):
            raise PolicyBusy("Policy worker busy")
        self.busy = True
        try:
            if cutoff > now:
                raise ValueError("Future cutoff is not permitted")
            existing = self.repository.records("evidence", PolicyEvidence)
            imported = {(e.sourceId, e.sourceFingerprint) for e in existing}
            for folder in sorted((root / "replay").glob("*")):
                if not (folder / "evidence.json").is_file():
                    continue
                try:
                    UUID(folder.name)
                    records = load_replay(folder, now)
                except (OSError, ValueError, sqlite3.Error) as error:
                    raise ValueError(f"Invalid persisted replay evidence: {folder.name}") from error
                for e in records:
                    if (e.sourceId, e.sourceFingerprint) in imported:
                        continue
                    self.repository.append("evidence", e.evidenceId, e.evidenceAvailableAt, e)
            for source in load_snapshots(root, limit=200):
                e = from_analytics(source, now)
                if (e.sourceId, e.sourceFingerprint) not in imported:
                    self.repository.append("evidence", e.evidenceId, e.evidenceAvailableAt, e)
            return self.create(
                self.repository.records("evidence", PolicyEvidence), settings, cutoff, now
            )
        finally:
            self.busy = False
            self.lock.release()

    def create(
        self, evidence: list[PolicyEvidence], settings: PolicySettings, cutoff: int, now: int
    ) -> PolicySnapshot:
        snapshot = build_snapshot(evidence, settings, cutoff)
        for e in evidence:
            self.repository.append("evidence", e.evidenceId, e.evidenceAvailableAt, e)
        self.repository.append("snapshot", snapshot.snapshotId, now, snapshot)
        if not any(
            e.eventType == "POLICY_CREATED" and e.snapshotId == snapshot.snapshotId
            for e in self.events
        ):
            self.event(
                "POLICY_CREATED", snapshot.snapshotId, now, settings.mode, (snapshot.status,)
            )
        return snapshot

    def activate(
        self, identifier: UUID, mode: PolicyMode, at: int, *, rollback: bool = False
    ) -> PolicyEvent:
        with self.lock:
            if mode == "OFF":
                raise ValueError("Use deactivate to select OFF")
            snapshot = self.repository.get("snapshot", identifier, PolicySnapshot)
            if snapshot is None:
                raise ValueError("Unknown policy snapshot")
            created = next(
                (
                    e
                    for e in self.events
                    if e.eventType == "POLICY_CREATED" and e.snapshotId == identifier
                ),
                None,
            )
            if created is None or max(created.at, snapshot.generatedAt) > at:
                raise ValueError("Snapshot did not exist at activation time")
            evidence = [
                self.repository.get("evidence", e, PolicyEvidence)
                for e in snapshot.createdFromEvidenceIds
            ]
            if any(e is None for e in evidence):
                raise ValueError("Missing source evidence")
            rebuilt = build_snapshot(
                [e for e in evidence if e is not None],
                snapshot.settings,
                snapshot.evidenceCutoffTime,
            )
            if rebuilt != snapshot:
                self.event(
                    "POLICY_VERSION_REJECTED",
                    identifier,
                    at,
                    mode,
                    ("SNAPSHOT_CONTENT_OR_VERSION_MISMATCH",),
                )
                raise ValueError("Snapshot content or version mismatch")
            if mode == "PAPER_GATED" or rollback:
                if snapshot.status != "VALIDATED" or not snapshot.rules:
                    raise ValueError("Activation requires a validated snapshot")
                if any(r.expiresAt < at for r in snapshot.rules):
                    raise ValueError("Snapshot evidence has expired")
                if any(
                    e.snapshotId == identifier and e.eventType == "POLICY_WITHDRAWN_DRIFT"
                    for e in self.events
                ):
                    raise ValueError("Drifted snapshot requires explicit evidence rebuild")
            previous, _, _, _ = self.active_at(at)
            return self.event(
                "POLICY_ROLLED_BACK" if rollback else "POLICY_ACTIVATED",
                identifier,
                at,
                mode,
                ("EXPLICIT_ROLLBACK" if rollback else "EXPLICIT_ACTIVATION",),
                previous.snapshotId if previous else None,
            )

    def deactivate(self, at: int) -> PolicyEvent:
        with self.lock:
            previous, _, _, _ = self.active_at(at)
            return self.event(
                "POLICY_DEACTIVATED",
                None,
                at,
                "OFF",
                ("EXPLICIT_OFF",),
                previous.snapshotId if previous else None,
            )

    def rollback(self, at: int) -> PolicyEvent:
        with self.lock:
            _, mode, current, _ = self.active_at(at)
            previous = current.previousPolicySnapshotId if current else None
            if previous is None:
                return self.deactivate(at)
            if mode == "OFF":
                prior = next(
                    (
                        e
                        for e in reversed(self.events)
                        if e.snapshotId == previous
                        and e.eventType in ("POLICY_ACTIVATED", "POLICY_ROLLED_BACK")
                    ),
                    None,
                )
                mode = prior.mode if prior else "SHADOW"
            return self.activate(previous, mode, at, rollback=True)

    def observe(
        self,
        board: OpportunityBoard,
        at: int,
        *,
        guard_permits: bool = True,
        strategies: tuple[str, ...] = (),
    ) -> bool:
        """Return permission to offer a board to Phase 9; OFF/SHADOW always preserve that call."""
        with self.lock:
            latest = next(
                (e for e in reversed(self.events) if e.at <= at and e.eventType in STATE_EVENTS),
                None,
            )
            mode = latest.mode if latest else self.defaultMode
            try:
                snapshot, mode, activation, withdrawn = self.active_at(at)
                if mode == "OFF":
                    return True
                watchdog = self.watchdog_at(at)
                decision = self.engine.evaluate(
                    board,
                    snapshot,
                    at,
                    mode=mode,
                    watchdog=watchdog,
                    withdrawn=withdrawn,
                    strategies=strategies,
                )
                # A selection appears once while live and again when finalized. Keep its first
                # availability, never reinterpret it using evidence imported between those calls.
                key = (board.platform, board.asOf, board.selectedSlotId or 0, decision.contextId)
                previous = self.decisions.get(key)
                if previous is not None and previous.baseGatePassed:
                    decision = previous
                elif decision.baseGatePassed:
                    self.decisions[key] = decision
                    if len(self.decisions) > 4096:
                        self.decisions.pop(next(iter(self.decisions)))
                self.repository.append(
                    "decision", decision.decisionId, decision.decisionAvailableAt, decision
                )
                if not self.recent or self.recent[-1].decisionId != decision.decisionId:
                    self.recent.append(decision)
                return mode != "PAPER_GATED" or (
                    decision.policyAction == "ALLOW"
                    and decision.baseGatePassed
                    and guard_permits
                    and not withdrawn
                    and self.error is None
                )
            except (OSError, ValueError, sqlite3.Error) as error:
                self.error = str(error)
                return mode != "PAPER_GATED"

    def watchdog_at(self, at: int) -> WatchdogSnapshot | None:
        _, _, activation, _ = self.active_at(at)
        if activation is None:
            return None
        records = self.repository.records("watchdog", WatchdogSnapshot, at=at, limit=1)
        return (
            records[-1] if records and records[-1].activationEventId == activation.eventId else None
        )

    def resolved(self, trades: list[PaperTrade], at: int) -> None:
        try:
            self._resolved(trades, at)
        except (OSError, ValueError, sqlite3.Error) as error:
            self.error = str(error)

    def _resolved(self, trades: list[PaperTrade], at: int) -> None:
        with self.lock:
            snapshot, mode, activation, withdrawn = self.active_at(at)
            if snapshot is None or activation is None or withdrawn or mode == "OFF":
                return
            for trade in trades:
                decision = self.decisions.get(
                    (trade.platform, trade.boardAsOf, trade.slotId, trade.contextId)
                )
                if decision is None:
                    decision = next(
                        (
                            d
                            for d in self.repository.records(
                                "decision", AdaptivePolicyDecision, at=at
                            )
                            if d.platform == trade.platform
                            and d.asOf == trade.boardAsOf
                            and d.slotId == trade.slotId
                            and d.contextId == trade.contextId
                        ),
                        None,
                    )
                if (
                    decision is not None
                    and decision.policyAction == "ALLOW"
                    and decision.snapshotId == snapshot.snapshotId
                    and decision.decisionAvailableAt >= activation.at
                ):
                    # Resolved outcomes are journaled for watchdog restart continuity.
                    # PaperTrade is validated by Phase 9; the journal payload is immutable below.
                    record = OutcomeRecord(trade=trade.model_dump_json())
                    self.repository.append("outcome", trade.paperTradeId, at, record)
            rows = [
                PaperTrade.model_validate_json(r.trade)
                for r in self.repository.records(
                    "outcome", OutcomeRecord, at=at, limit=snapshot.settings.longWindow
                )
            ]
            watchdog = evaluate_watchdog(snapshot, activation, rows, at)
            self.repository.append("watchdog", watchdog.watchdogId, at, watchdog)
            if watchdog.state in ("WARNING", "DRIFTED"):
                self.event(
                    "WATCHDOG_DRIFTED" if watchdog.state == "DRIFTED" else "WATCHDOG_WARNING",
                    snapshot.snapshotId,
                    at,
                    mode,
                    watchdog.reasons,
                )
            if watchdog.state == "DRIFTED":
                self.event(
                    "POLICY_WITHDRAWN_DRIFT",
                    snapshot.snapshotId,
                    at,
                    mode,
                    watchdog.reasons,
                    activation.previousPolicySnapshotId,
                )

    def state(self, at: int) -> dict[str, object]:
        snapshot, mode, _, withdrawn = self.active_at(at)
        watchdog = self.watchdog_at(at)
        return dict(
            policyVersion=POLICY_VERSION,
            watchdogVersion=WATCHDOG_VERSION,
            mode=mode,
            snapshotId=str(snapshot.snapshotId) if snapshot else None,
            evidenceCutoffTime=snapshot.evidenceCutoffTime if snapshot else None,
            evidenceStatus="DRIFTED"
            if withdrawn
            else snapshot.status
            if snapshot
            else "NO_EVIDENCE",
            watchdogState="DRIFTED" if withdrawn else watchdog.state if watchdog else "WARMING",
            decisions=[d.model_dump(mode="json") for d in list(self.recent)[-20:]],
            busy=self.busy,
            error=self.error,
            analyticalOnly=True,
        )


from quant_engine.policy.models import Frozen  # noqa: E402


class OutcomeRecord(Frozen):
    trade: str
