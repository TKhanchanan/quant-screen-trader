"""Phase 14 observer. No result from this module is consumed by an analytical decision.

Counters cover the run; detailed events stop at 64 MiB (and invalidate acceptance).
Latency percentiles describe the last 2048 measurements; count/max cover the whole run.
Only typed market records and a closed desktop telemetry schema enter this namespace.

Detail is tiered so a 24-hour run fits the event bound. Every board revision and policy decision
is counted and checked as it happens. Boards that name a selection, INVALID boards, and policy
decisions that pass the baseline gate, act, diverge or leave SHADOW are written in full; the
thousands of non-selecting revisions and their SKIP decisions become five-minute BOARD_SUMMARY
counts per platform.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import platform
import re
import sqlite3
import time
from collections import deque
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from threading import RLock
from typing import Any, cast
from uuid import UUID, uuid4

from quant_engine.features.models import FeatureBundle, FeatureSnapshot
from quant_engine.market_models import TIMEFRAMES, MarketObservation, PriceSample
from quant_engine.opportunity.models import OpportunityBoard
from quant_engine.paper.engine import PaperUpdate
from quant_engine.paper.models import PaperTrade
from quant_engine.paper.policy import PaperSettings
from quant_engine.paper.resolver import accepts_entry, accepts_expiry, same_identity
from quant_engine.policy.models import SOURCE_VERSIONS, AdaptivePolicyDecision

SHADOW_LIVE_VERSION = "qst-shadow-live-v2"
MAX_EVENTS_BYTES = 64 * 1024 * 1024
MAX_RECENT = 2048
PLATFORMS = ("capitalbear", "iqoption")
SUMMARY_WINDOW_MS = 300_000
SUMMARY_KEYS = 64
CONTEXT_HISTORY = 16


def millis() -> int:
    return time.time_ns() // 1_000_000


def observer(method: Callable[..., None]) -> Callable[..., None]:
    """Instrumentation failure must be visible, but cannot alter baseline ingestion."""

    @wraps(method)
    def wrapped(self: ShadowLiveRecorder, *args: Any, **kwargs: Any) -> None:
        with self.lock:
            try:
                method(self, *args, **kwargs)
            except Exception:
                self.data["recorderErrors"] += 1
                self.warn("RECORDER_ERROR")  # Never persist exception text or local paths.

    return wrapped


class ShadowLiveRecorder:
    def __init__(
        self,
        root: Path,
        *,
        commit_sha: str,
        application_version: str,
        run_id: str | None = None,
        now: int | None = None,
        clock: Callable[[], int] = millis,
    ) -> None:
        self.lock = RLock()
        # The live engine never passes a clock. Only the Phase 14 rehearsal injects virtual time.
        self.clock = clock
        at = clock() if now is None else now
        identifier = str(UUID(run_id)) if run_id else str(uuid4())
        self.folder = root / identifier
        self.folder.mkdir(parents=True, exist_ok=True)
        self.target = self.folder / "summary.json"
        self.events = self.folder / "events.jsonl"
        self.pending: deque[dict[str, Any]] = deque(maxlen=512)
        self.seen: dict[str, None] = {}
        self.lineage: dict[str, int] = {}
        self.candle_requirements: dict[str, int] = {}
        self.feature_requirements: dict[str, int] = {}
        self.latencies: dict[str, deque[float]] = {}
        self.last_telemetry: dict[str, tuple[int, str, bool]] = {}
        self.segment_started = at
        self.qualified_until = at
        self.last_operational_snapshot: dict[str, int] = {}
        self.last_auto_sync: dict[str, tuple[str, int, int]] = {}
        # (asset, context, first accepted sample, first sample of the next identity or reset).
        self.contexts: dict[str, deque[tuple[str, str, int, int | None]]] = {}
        self.board_windows: dict[str, dict[str, Any]] = {}
        self.data: dict[str, Any] = dict(
            runId=identifier,
            version=SHADOW_LIVE_VERSION,
            startedAt=at,
            finishedAt=None,
            commitSha=commit_sha if re.fullmatch(r"[0-9a-f]{40}", commit_sha) else "unknown",
            applicationVersion=application_version,
            platform=f"{platform.system()} {platform.machine()}",
            sourceVersions={
                **SOURCE_VERSIONS,
                "policyVersion": "qst-policy-v1",
                "watchdogVersion": "qst-watchdog-v1",
            },
            durationMs=0,
            qualifiedCaptureDurationMs=0,
            maxQueueDepth=0,
            engineRestarts=0,
            uncleanRestarts=0,
            recorderErrors=0,
            causalityViolations=0,
            crossContextContamination=0,
            baselineMismatches=0,
            storageErrors=0,
            storageCorruption=None,
            engineCrashLoop=None,
            unexpectedBrokerPresses=None,
            executionArmed=None,
            unboundedQueue=None,
            http429s=0,
            engineBusyEvents=0,
            eventBytes=0,
            evidenceTruncated=False,
            restartVerified=False,
            storageVerified=False,
            executionVerified=False,
            warnings=[],
            errors={},
            platforms={},
            slots={},
            latency={},
            policy={},
            sessionGuard=None,
            paperEnabled=None,
            recentEvents=[],
            segments=[],
            desktop={},
        )
        self.lease = sqlite3.connect(
            self.folder / "lease.sqlite3", timeout=0, check_same_thread=False
        )
        try:
            self.lease.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError as error:
            self.lease.close()
            raise ValueError("Phase 14 run already has a live owner") from error
        if self.data["commitSha"] == "unknown":
            self.warn("BUILD_IDENTITY_UNKNOWN")
        if self.target.exists():
            try:
                old = json.loads(self.target.read_text())
            except (OSError, ValueError):
                self.lease.close()
                raise
            if (old["version"], old["commitSha"], old["applicationVersion"]) != (
                SHADOW_LIVE_VERSION,
                commit_sha,
                application_version,
            ):
                self.lease.close()
                raise ValueError("Continuation requires the same build and report version")
            self.data = old
            self.data["engineRestarts"] += 1
            self.data["uncleanRestarts"] += int(old["finishedAt"] is None)
            self.data["finishedAt"] = None
            if self.data["uncleanRestarts"]:
                self.warn("UNCLEAN_RESTART_EVIDENCE_GAP")
        self.data.setdefault("qualifiedCaptureDurationMs", 0)
        self.data.setdefault("maxQueueDepth", 0)
        for name in PLATFORMS:
            self.data["platforms"].setdefault(
                name,
                dict(
                    observations=0,
                    accepted=0,
                    rejected=0,
                    dataUncertain=0,
                    liveObservations=0,
                    captureDurationMs=0,
                    continuousCaptureMs=0,
                    longestContinuousCaptureMs=0,
                    boards={
                        s: 0
                        for s in ("COLLECTING", "PARTIAL", "READY", "NO_OPPORTUNITY", "INVALID")
                    },
                    policyActions={s: 0 for s in ("ALLOW", "WATCH", "SKIP")},
                    paperStates={
                        s: 0 for s in ("PENDING_ENTRY", "OPEN", "RESOLVED", "CANCELLED", "INVALID")
                    },
                    outcomes={s: 0 for s in ("WIN", "LOSS", "DRAW", "INVALID")},
                    pipeline={s: 0 for s in ("Phase5", "Phase6", "Phase7", "Phase8")},
                ),
            )
            self.data["platforms"][name]["continuousCaptureMs"] = 0
        starts = (self.data.get("engineStarts", []) + [at])[-64:]
        self.data["engineStarts"] = starts
        self.data["engineCrashLoop"] = (
            bool(self.data["engineCrashLoop"]) or sum(t >= at - 60000 for t in starts) >= 4
        )
        self.data["eventBytes"] = self.events.stat().st_size if self.events.exists() else 0
        self.completed_duration = self.data["durationMs"]
        self.event("ENGINE_STARTED", at=at)
        self.flush(now=at)

    def warn(self, code: str) -> None:
        if code not in self.data["warnings"] and len(self.data["warnings"]) < 64:
            self.data["warnings"].append(code)

    def event(self, kind: str, **values: Any) -> None:
        if len(self.pending) == self.pending.maxlen:
            self.data["evidenceTruncated"] = True
            self.warn("EVENT_BUFFER_OVERFLOW")
        self.pending.append(dict(kind=kind, **values))

    def once(self, key: str) -> bool:
        if key in self.seen:
            return False
        self.seen[key] = None
        if len(self.seen) > 4096:
            self.seen.pop(next(iter(self.seen)))
        return True

    def violation(self, metric: str, reason: str) -> None:
        self.data[metric] += 1
        self.data["errors"][reason] = self.data["errors"].get(reason, 0) + 1
        self.event("VIOLATION", metric=metric, reason=reason)

    @observer
    def latency(self, name: str, stage: str, elapsed: float) -> None:
        if not math.isfinite(elapsed) or elapsed < 0:
            self.violation("causalityViolations", "NEGATIVE_OR_INVALID_LATENCY")
            return
        key = f"{name}:{stage}"
        values = self.latencies.setdefault(key, deque(maxlen=MAX_RECENT))
        values.append(elapsed)
        value = self.data["latency"].setdefault(key, dict(count=0, max=0.0))
        value["count"] += 1
        value["max"] = max(value["max"], elapsed)

    @observer
    def observation(self, observation: MarketObservation, accepted: bool, now: int) -> None:
        o = observation
        at, parsed = int(o.observedAt.timestamp() * 1000), int(o.parsedAt.timestamp() * 1000)
        p = self.data["platforms"][o.platform]
        p["observations"] += 1
        p["accepted" if accepted else "rejected"] += 1
        live = o.sourceType in ("DOM", "VISUAL")
        p["liveObservations"] += int(live)
        if not live:
            self.warn("NON_LIVE_INPUT")
        key = f"{o.platform}:{o.slotId}"
        s = self.data["slots"].setdefault(
            key,
            dict(
                platform=o.platform,
                slotId=o.slotId,
                assetName=o.assetName,
                contextId=None,
                observationCount=0,
                acceptedSamples=0,
                rejectedSamples=0,
                staleSamples=0,
                dataUncertain=0,
                DOM=0,
                VISUAL=0,
                duplicates=0,
                outOfOrder=0,
                sourceSwitches=0,
                contextTransitions=0,
                slotResets=0,
                missingSecondEstimate=0,
                lastSampleAt=None,
                lastObservedAt=None,
                requiredAt=0,
                source=None,
            ),
        )
        s["observationCount"] += 1
        s["acceptedSamples" if accepted else "rejectedSamples"] += 1
        stale = o.dataQuality.state == "STALE" or (live and now - at > 3000)
        uncertain = o.price is None or o.dataQuality.state in ("UNCERTAIN", "INVALID")
        quality = p.setdefault(
            "qualityCounts",
            {state: 0 for state in ("GOOD", "UNCERTAIN", "STALE", "INVALID", "DEGRADED")},
        )
        quality["STALE" if stale else o.dataQuality.state] += 1
        s["staleSamples"] += int(stale)
        s["dataUncertain"] += int(uncertain)
        p["dataUncertain"] += int(uncertain)
        if o.sourceType in ("DOM", "VISUAL"):
            s[o.sourceType] += 1
        if s["lastObservedAt"] is not None:
            s["duplicates"] += int(at == s["lastObservedAt"])
            s["outOfOrder"] += int(at < s["lastObservedAt"])
            if at > s["lastObservedAt"]:
                self.latency(o.platform, "captureInterval", at - s["lastObservedAt"])
        s["lastObservedAt"] = max(at, s["lastObservedAt"] or at)
        if not accepted:
            return
        if stale or uncertain or (live and parsed > now):
            self.violation("causalityViolations", "INVALID_LIVE_SAMPLE_ACCEPTED")
        identity = (o.assetName, str(o.contextId))
        history = self.contexts.setdefault(key, deque(maxlen=CONTEXT_HISTORY))
        if not history or history[-1][:2] != identity:
            if history and history[-1][3] is None:
                history[-1] = (*history[-1][:3], at)
            history.append((*identity, at, None))
        if (s["assetName"], s["contextId"]) != identity:
            if s["contextId"] is not None:
                s["contextTransitions"] += 1
            self.event(
                "CONTEXT",
                platform=o.platform,
                slotId=o.slotId,
                oldAsset=s["assetName"],
                oldContext=s["contextId"],
                assetName=o.assetName,
                contextId=str(o.contextId),
                at=at,
            )
            s.update(
                assetName=o.assetName, contextId=str(o.contextId), lastSampleAt=None, requiredAt=0
            )
        if s["source"] is not None and s["source"] != o.sourceType:
            s["sourceSwitches"] += 1
            self.event(
                "SOURCE",
                platform=o.platform,
                slotId=o.slotId,
                previous=s["source"],
                source=o.sourceType,
                at=at,
            )
        if s["lastSampleAt"] is not None:
            if at <= s["lastSampleAt"]:
                self.violation("causalityViolations", "CANONICAL_TIME_REGRESSION")
            s["missingSecondEstimate"] += max(0, at // 1000 - s["lastSampleAt"] // 1000 - 1)
        s.update(source=o.sourceType, lastSampleAt=at, requiredAt=max(parsed, s["requiredAt"]))
        for timeframe, seconds in TIMEFRAMES.items():
            end = (at // (seconds * 1000) + 1) * seconds * 1000
            candle = f"{o.platform}:{o.slotId}:{o.contextId}:{timeframe}:{end}"
            self.candle_requirements[candle] = max(parsed, self.candle_requirements.get(candle, 0))
            if len(self.candle_requirements) > 4096:
                self.candle_requirements.pop(next(iter(self.candle_requirements)))
        p["pipeline"]["Phase5"] += 1
        self.latency(o.platform, "captureToAccepted", max(0, self.clock() - at))

    def check_context(self, name: str, slot: int, asset: str, context: str, at: int) -> None:
        """A derived record must carry the identity the slot actually had when its data existed.

        Comparing with the slot's *current* identity would call the last bar of a context that
        closed on the next context's first reading contamination, and would miss nothing a
        timed check misses: a context that never covered ``at`` is still a violation.
        """
        history = self.contexts.get(f"{name}:{slot}", ())
        if not any(
            (entry_asset, entry_context) == (asset, context)
            and start < at
            and (end is None or at <= end)
            for entry_asset, entry_context, start, end in history
        ):
            self.violation("crossContextContamination", "DERIVED_CONTEXT_MISMATCH")

    @observer
    def feature(self, snapshot: FeatureSnapshot, available_at: int) -> None:
        key = f"{snapshot.platform}:{snapshot.slotId}:{snapshot.contextId}:{snapshot.timeframe}:{snapshot.featureTime}"
        required = self.candle_requirements.pop(key, None)
        if required is None:
            self.warn("MISSING_CANDLE_DEPENDENCY")
        self.feature_requirements[key] = max(available_at, required or snapshot.featureTime)
        if len(self.feature_requirements) > 4096:
            self.feature_requirements.pop(next(iter(self.feature_requirements)))
        self.data["platforms"][snapshot.platform]["pipeline"]["Phase6"] += 1

    @observer
    def ensemble(self, bundle: FeatureBundle, available_at: int) -> None:
        name = bundle.platform
        self.data["platforms"][name]["pipeline"]["Phase7"] += 1
        required = available_at
        self.check_context(
            name, bundle.slotId, bundle.assetName, str(bundle.contextId), bundle.asOf
        )
        for snapshot in [bundle.primary, *bundle.contexts.values()]:
            if snapshot is None:
                continue
            if (snapshot.slotId, snapshot.assetName, snapshot.contextId) != (
                bundle.slotId,
                bundle.assetName,
                bundle.contextId,
            ):
                self.violation("crossContextContamination", "MIXED_CONTEXT_BUNDLE")
            if snapshot.featureTime > bundle.asOf:
                self.violation("causalityViolations", "FUTURE_CONTEXT_CANDLE")
            key = f"{name}:{snapshot.slotId}:{snapshot.contextId}:{snapshot.timeframe}:{snapshot.featureTime}"
            required = max(required, self.feature_requirements.get(key, snapshot.featureTime))
        self.lineage[f"{name}:{bundle.slotId}:{bundle.contextId}:{bundle.asOf}"] = required
        if len(self.lineage) > 4096:
            self.lineage.pop(next(iter(self.lineage)))
        if bundle.primaryTimeframe != ("S5" if name == "capitalbear" else "M1"):
            self.violation("causalityViolations", "WRONG_PRIMARY_TIMEFRAME")

    @observer
    def board(
        self,
        board: OpportunityBoard,
        at: int,
        decision: AdaptivePolicyDecision | None,
        permits: bool,
    ) -> None:
        name = board.platform
        key = "board:" + hashlib.sha256(board.model_dump_json().encode()).hexdigest()
        if self.once(key):
            self.data["platforms"][name]["boards"][board.status] += 1
            self.data["platforms"][name]["pipeline"]["Phase8"] += 1
            if board.selectedSlotId is not None or board.status == "INVALID":
                self.event("BOARD", board=board.model_dump(mode="json"), decisionAvailableAt=at)
            else:
                self.summarize_board(board)
        required = board.asOf
        for c in board.candidates:
            self.check_context(name, c.slotId, c.assetName, str(c.contextId), c.asOf)
            dependency = self.lineage.get(f"{name}:{c.slotId}:{c.contextId}:{c.asOf}")
            if dependency is None:
                self.warn("MISSING_DECISION_LINEAGE")
            required = max(required, dependency or c.asOf)
        if at < required:
            self.violation("causalityViolations", "DECISION_PRECEDES_REQUIRED_DATA")
        if decision is None:
            self.warn("POLICY_DECISION_UNAVAILABLE")
            return
        if decision.mode != "SHADOW":
            self.warn("POLICY_NOT_SHADOW")
        if decision.mode == "SHADOW" and not permits:
            self.violation("baselineMismatches", "SHADOW_SUPPRESSED_BASELINE")
        if decision.baseGatePassed:
            selected = next((c for c in board.candidates if c.slotId == board.selectedSlotId), None)
            if selected is None or selected.contextId != decision.contextId:
                self.violation("crossContextContamination", "POLICY_CONTEXT_MISMATCH")
        if decision.decisionAvailableAt < (decision.evidenceAvailableAt or 0):
            self.violation("causalityViolations", "FUTURE_POLICY_EVIDENCE")
        if self.once(f"policy:{decision.decisionId}"):
            self.data["platforms"][name]["policyActions"][decision.policyAction] += 1
            if (
                decision.baseGatePassed
                or decision.policyAction != "SKIP"
                or decision.adaptiveAction != decision.baselineAction
                or decision.mode != "SHADOW"
                or not permits
            ):
                self.event(
                    "POLICY", decision=decision.model_dump(mode="json"), baselineOffered=permits
                )
            else:
                self.summarize_policy(board, decision)

    @observer
    def first_price(
        self,
        before: PaperTrade | None,
        sample: PriceSample,
        update: PaperUpdate,
        settings: PaperSettings,
    ) -> None:
        if before is None or not same_identity(before, sample):
            return
        eligible = (
            accepts_entry(
                before,
                sample,
                deadline=before.decisionAvailableAt + settings.max_entry_delay_ms(sample.platform),
            )
            if before.status == "PENDING_ENTRY"
            else accepts_expiry(
                before,
                sample,
                deadline=(before.expiryTargetTime or 0)
                + settings.max_resolution_lag_ms(sample.platform),
            )
        )
        if eligible and not any(t.paperTradeId == before.paperTradeId for t in update.trades):
            self.violation("causalityViolations", "FIRST_CANONICAL_PRICE_SKIPPED")

    @observer
    def paper(self, update: PaperUpdate, sample: PriceSample | None = None) -> None:
        for trade in update.trades:
            if not self.once(f"paper:{trade.paperTradeId}:{trade.status}"):
                continue
            p = self.data["platforms"][trade.platform]
            p["paperStates"][trade.status] += 1
            if trade.outcome in p["outcomes"]:
                p["outcomes"][trade.outcome] += 1
            if trade.status in ("PENDING_ENTRY", "OPEN", "RESOLVED"):
                self.check_context(
                    trade.platform,
                    trade.slotId,
                    trade.assetName,
                    str(trade.contextId),
                    trade.boardAsOf,
                )
            if trade.entryTime is not None and trade.entryTime < trade.decisionAvailableAt:
                self.violation("causalityViolations", "EARLY_PAPER_ENTRY")
            if trade.expiryTime is not None and (
                trade.expiryTargetTime is None or trade.expiryTime < trade.expiryTargetTime
            ):
                self.violation("causalityViolations", "EARLY_PAPER_EXPIRY")
            if trade.durationMs != (5000 if trade.platform == "capitalbear" else 60000):
                self.violation("causalityViolations", "WRONG_PAPER_HORIZON")
            if sample is not None and trade.status in ("OPEN", "RESOLVED"):
                stamp = trade.entryTime if trade.status == "OPEN" else trade.expiryTime
                price = trade.entryPrice if trade.status == "OPEN" else trade.expiryPrice
                if stamp != sample.timestamp or price != sample.price:
                    self.violation("causalityViolations", "NON_CANONICAL_PAPER_PRICE")
                if (sample.slotId, sample.assetName, sample.contextId) != (
                    trade.slotId,
                    trade.assetName,
                    trade.contextId,
                ):
                    self.violation("crossContextContamination", "PAPER_PRICE_CONTEXT_MISMATCH")
            self.event("PAPER", trade=trade.model_dump(mode="json"))

    def summary_window(self, name: str, as_of: int) -> dict[str, Any]:
        window = self.board_windows.get(name)
        if window is not None and as_of >= window["fromAsOf"] + SUMMARY_WINDOW_MS:
            self.emit_summary(name)
            window = None
        if window is None:
            window = dict(
                fromAsOf=as_of,
                toAsOf=as_of,
                boardRevisions={},
                boardReasons={},
                missingSlots={},
                candidateStatus={},
                exclusionReasons={},
                maxRankScore=0.0,
                maxEnsembleConfidence=0.0,
                policyActions={},
                policyReasons={},
                policyVetoes={},
                evidenceStatus={},
            )
            self.board_windows[name] = window
        window["fromAsOf"] = min(window["fromAsOf"], as_of)
        window["toAsOf"] = max(window["toAsOf"], as_of)
        return window

    @staticmethod
    def tally(counts: dict[str, int], key: object) -> None:
        name = str(key)
        if name not in counts and len(counts) >= SUMMARY_KEYS:
            name = "OTHER"
        counts[name] = counts.get(name, 0) + 1

    def summarize_board(self, board: OpportunityBoard) -> None:
        window = self.summary_window(board.platform, board.asOf)
        self.data["platforms"][board.platform]["summarizedBoardRevisions"] = (
            self.data["platforms"][board.platform].get("summarizedBoardRevisions", 0) + 1
        )
        self.tally(window["boardRevisions"], board.status)
        for code in board.reasons:
            self.tally(window["boardReasons"], code)
        for slot in board.missingSlots:
            self.tally(window["missingSlots"], slot)
        for candidate in board.candidates:
            self.tally(window["candidateStatus"], candidate.candidateStatus)
            for code in candidate.exclusionReasons:
                self.tally(window["exclusionReasons"], code)
            window["maxRankScore"] = max(window["maxRankScore"], candidate.rankScore)
            window["maxEnsembleConfidence"] = max(
                window["maxEnsembleConfidence"], candidate.ensembleConfidence
            )

    def summarize_policy(self, board: OpportunityBoard, decision: AdaptivePolicyDecision) -> None:
        window = self.summary_window(board.platform, board.asOf)
        self.data["platforms"][board.platform]["summarizedPolicyDecisions"] = (
            self.data["platforms"][board.platform].get("summarizedPolicyDecisions", 0) + 1
        )
        self.tally(window["policyActions"], decision.policyAction)
        self.tally(window["evidenceStatus"], decision.evidenceStatus)
        for code in decision.reasons:
            self.tally(window["policyReasons"], code)
        for code in decision.vetoes:
            self.tally(window["policyVetoes"], code)

    def emit_summary(self, name: str) -> None:
        window = self.board_windows.pop(name, None)
        if window is not None:
            self.event("BOARD_SUMMARY", platform=name, **window)

    @observer
    def reset(self, name: str, slots: list[int]) -> None:
        at = self.clock()
        for slot in slots:
            current = self.data["slots"].get(f"{name}:{slot}")
            if current:
                current["slotResets"] += 1
                current["contextId"] = None
            history = self.contexts.get(f"{name}:{slot}")
            if history and history[-1][3] is None:
                history[-1] = (*history[-1][:3], at)
        self.event("SLOT_RESET", platform=name, slotIds=slots, at=at)

    @observer
    def telemetry(self, value: dict[str, Any], now: int | None = None) -> None:
        value = copy.deepcopy(value)
        at = self.clock() if now is None else now
        name = value["platform"]
        prior_desktop = self.data["desktop"].get(name)
        self.data["desktop"][name] = value
        sync = self.last_auto_sync.get(name)
        runs, changes = value.get("autoSyncRuns", 0), value.get("autoSyncAppliedChanges", 0)
        previous_runs, previous_changes = (
            sync[1:] if sync and sync[0] == value["instanceId"] else (0, 0)
        )
        platform = self.data["platforms"][name]
        platform["autoSyncRuns"] = platform.get("autoSyncRuns", 0) + max(0, runs - previous_runs)
        platform["autoSyncAppliedChanges"] = platform.get("autoSyncAppliedChanges", 0) + max(
            0, changes - previous_changes
        )
        # Applied changes require operator review against the visible instruments.
        review = platform.get("autoSyncVerification", {})
        platform["unexpectedAutoSyncChanges"] = (
            review["unexpectedAutoSyncChanges"]
            if review.get("reviewedAppliedChanges") == platform["autoSyncAppliedChanges"]
            else None
            if platform["autoSyncAppliedChanges"]
            else 0
        )
        transport = self.data.setdefault("desktopTransport", {})
        if value["instanceId"] not in transport and len(transport) >= 64:
            self.data["evidenceTruncated"] = True
            self.warn("DESKTOP_SEGMENT_CAP_REACHED")
        else:
            prior = transport.setdefault(value["instanceId"], {"droppedBatches": 0, "http429s": 0})
            for metric in ("droppedBatches", "http429s"):
                prior[metric] = max(prior[metric], value.get(metric, 0))
            self.data["droppedBatches"] = sum(v["droppedBatches"] for v in transport.values())
            self.data["desktopHttp429s"] = sum(v["http429s"] for v in transport.values())
        self.last_auto_sync[name] = (value["instanceId"], runs, changes)
        totals = platform.setdefault("slotCaptureTotals", {})
        previous_slots = (
            {s["slotId"]: s for s in prior_desktop["slots"]}
            if prior_desktop and prior_desktop["instanceId"] == value["instanceId"]
            else {}
        )
        for slot in value["slots"]:
            total = totals.setdefault(str(slot["slotId"]), {})
            old = previous_slots.get(slot["slotId"], {})
            for metric in ("attemptCount", "parsedCount", "goodCount", "uncertainCount"):
                current = slot.get(metric, 0)
                previous_count = old.get(metric, 0)
                total[metric] = total.get(metric, 0) + (
                    current - previous_count if current >= previous_count else current
                )
            for metric in (
                "lastAttemptAt",
                "lastParsedPriceAt",
                "lastGoodPriceAt",
                "secondSamples",
                "s5Samples",
                "m1Samples",
            ):
                total[metric] = slot.get(metric)
        enabled = [s for s in value["slots"] if s["enabled"]]
        recent = all(
            s.get("captureEligible", False)
            and s.get("lastCaptureAttemptAt") is not None
            and 0 <= at - s["lastCaptureAttemptAt"] <= max(10000, value["intervalMs"] * 2)
            for s in enabled
        )
        live = (
            bool(enabled)
            and recent
            and value["captureRunning"]
            and value["surfaceAvailable"]
            and value["engineAvailable"]
        )
        previous = self.last_telemetry.get(name)
        p = self.data["platforms"][name]
        if previous and previous[1] == value["instanceId"] and previous[2] and live:
            delta = at - previous[0]
            if 0 <= delta <= 5000:
                p["captureDurationMs"] += delta
                # Union of actually credited platform intervals, without double-counting
                # simultaneous brokers or bridging downtime between process segments.
                self.data["qualifiedCaptureDurationMs"] += max(
                    0, at - max(previous[0], self.qualified_until)
                )
                self.qualified_until = max(self.qualified_until, at)
                p["continuousCaptureMs"] += delta
                p["longestContinuousCaptureMs"] = max(
                    p["longestContinuousCaptureMs"], p["continuousCaptureMs"]
                )
            else:
                p["continuousCaptureMs"] = 0
                self.warn("DESKTOP_TELEMETRY_GAP")
        else:
            p["continuousCaptureMs"] = 0
        self.last_telemetry[name] = (at, value["instanceId"], live)
        if self.once(f"health:{name}:{value['instanceId']}:{value['healthRevision']}"):
            self.event("DESKTOP_HEALTH", at=at, **value)
        self.data["executionArmed"] = bool(self.data["executionArmed"]) or value["armed"]
        self.data["unexpectedBrokerPresses"] = max(
            self.data["unexpectedBrokerPresses"] or 0, value["brokerPresses"]
        )
        self.data["maxQueueDepth"] = max(self.data["maxQueueDepth"], value["queueDepth"])
        self.data["unboundedQueue"] = bool(self.data["unboundedQueue"]) or value["queueDepth"] > 180
        if at - self.last_operational_snapshot.get(name, 0) >= 300000:
            self.event("OPERATIONAL_SNAPSHOT", at=at, **value)
            self.last_operational_snapshot[name] = at
        if value["armed"] or value["brokerPresses"]:
            self.warn("EXECUTION_SAFETY_FAILURE")
        self.latency(name, "mainLoopDelay", value["mainLoopDelayMs"])

    @observer
    def health(
        self, policy: Callable[[], dict[str, object]], session: Any, paper_enabled: bool
    ) -> None:
        self.data["paperEnabled"] = paper_enabled
        if not paper_enabled:
            self.warn("PAPER_DISABLED")
        state = policy()
        state["error"] = bool(state.get("error"))
        self.data["policy"] = state
        self.data["sessionGuard"] = session.model_dump(mode="json") if session else None
        if state.get("mode") != "SHADOW":
            self.warn("POLICY_NOT_SHADOW")
        if state["error"]:
            self.warn("POLICY_SERVICE_ERROR")

    @observer
    def error(self, code: str) -> None:
        self.data["errors"][code] = self.data["errors"].get(code, 0) + 1
        if code == "STORAGE_ERROR":
            self.data["storageErrors"] += 1
        if code == "HTTP_429":
            self.data["http429s"] += 1
            self.data["engineBusyEvents"] += 1
        self.event("ERROR", code=code, at=self.clock())

    def report(self, now: int | None = None) -> dict[str, Any]:
        with self.lock:
            at = self.clock() if now is None else now
            if self.data["finishedAt"] is None:
                self.data["durationMs"] = self.completed_duration + max(
                    0, at - self.segment_started
                )
            for key, values in self.latencies.items():
                ordered = sorted(values)
                self.data["latency"][key].update(
                    p50=ordered[math.ceil(len(ordered) * 0.50) - 1],
                    p95=ordered[math.ceil(len(ordered) * 0.95) - 1],
                    percentileWindowCount=len(ordered),
                    percentileScope="LAST_2048_THIS_PROCESS",
                )
            for metric in ("observations", "accepted", "rejected", "dataUncertain"):
                self.data[metric] = sum(p[metric] for p in self.data["platforms"].values())
            failed = (
                any(
                    self.data[key]
                    for key in (
                        "causalityViolations",
                        "crossContextContamination",
                        "baselineMismatches",
                        "storageCorruption",
                        "engineCrashLoop",
                        "executionArmed",
                        "unexpectedBrokerPresses",
                        "unboundedQueue",
                        "recorderErrors",
                        "evidenceTruncated",
                    )
                )
                or self.data["storageErrors"] >= 3
                or any(p.get("unexpectedAutoSyncChanges") for p in self.data["platforms"].values())
            )
            capture_uncertain = any(
                s["dataUncertain"]
                for desktop in self.data["desktop"].values()
                for s in desktop["slots"]
            )
            degraded = (
                capture_uncertain
                or bool(self.data["warnings"] or self.data["storageErrors"])
                or any(p["dataUncertain"] or p["rejected"] for p in self.data["platforms"].values())
            )
            self.data["health"] = "FAILED" if failed else "DEGRADED" if degraded else "HEALTHY"
            duration_ok = self.data["qualifiedCaptureDurationMs"] >= 86_400_000 and all(
                p["captureDurationMs"] >= 82_800_000 and p["liveObservations"] > 0
                for p in self.data["platforms"].values()
            )
            verified = all(
                self.data[key]
                for key in ("restartVerified", "storageVerified", "executionVerified")
            )
            hard_known = (
                all(
                    self.data[key] is False
                    for key in (
                        "storageCorruption",
                        "engineCrashLoop",
                        "executionArmed",
                        "unboundedQueue",
                    )
                )
                and self.data["unexpectedBrokerPresses"] == 0
            )
            complete = (
                duration_ok
                and verified
                and hard_known
                and all(
                    p.get("unexpectedAutoSyncChanges", 0) == 0
                    for p in self.data["platforms"].values()
                )
                and self.data["paperEnabled"] is True
                and self.data["policy"].get("mode") == "SHADOW"
                and not self.data["evidenceTruncated"]
                and not any(
                    code in self.data["warnings"]
                    for code in (
                        "NON_LIVE_INPUT",
                        "POLICY_NOT_SHADOW",
                        "MISSING_DECISION_LINEAGE",
                        "MISSING_CANDLE_DEPENDENCY",
                        "POLICY_DECISION_UNAVAILABLE",
                        "UNCLEAN_RESTART_EVIDENCE_GAP",
                        "BUILD_IDENTITY_UNKNOWN",
                        "PAPER_DISABLED",
                        "POLICY_SERVICE_ERROR",
                    )
                )
            )
            self.data["acceptance"] = "FAIL" if failed else "COMPLETE" if complete else "PENDING"
            self.data["result"] = (
                "FAIL" if failed else "PASS" if complete and not degraded else "DEGRADED"
            )
            return cast(
                dict[str, Any], json.loads(json.dumps(self.data))
            )  # Detached read; no mutable trading references.

    @observer
    def flush(self, *, now: int | None = None, finish: bool = False) -> None:
        at = self.clock() if now is None else now
        if finish:
            for name in list(self.board_windows):
                self.emit_summary(name)
        payload = "".join(json.dumps(e, separators=(",", ":")) + "\n" for e in self.pending)
        if self.data["eventBytes"] + len(payload.encode()) <= MAX_EVENTS_BYTES:
            with self.events.open("a") as output:
                output.write(payload)
            self.data["eventBytes"] += len(payload.encode())
        else:
            self.data["evidenceTruncated"] = True
            self.warn("EVENT_CAP_REACHED")
        self.data["recentEvents"] = (self.data["recentEvents"] + list(self.pending))[-64:]
        self.pending.clear()
        self.report(at)
        if finish:
            self.data["finishedAt"] = at
            self.data["segments"] = (
                self.data["segments"] + [dict(startedAt=self.segment_started, finishedAt=at)]
            )[-64:]
        temporary = self.target.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.report(at), indent=2) + "\n")
        temporary.replace(self.target)
        if finish:
            self.lease.close()
