"""Pure analytical evaluation. The Phase 8 selection is the only possible opportunity."""

from __future__ import annotations

from quant_engine.opportunity.models import OpportunityBoard
from quant_engine.policy.models import (
    POLICY_VERSION,
    SOURCE_VERSIONS,
    WATCHDOG_VERSION,
    AdaptivePolicyDecision,
    EvidenceStatus,
    PolicyAction,
    PolicyMode,
    PolicySnapshot,
    WatchdogSnapshot,
    identity,
)
from quant_engine.policy.rules import SPECIFICITY, matches


class PolicyEngine:
    def evaluate(
        self,
        board: OpportunityBoard,
        snapshot: PolicySnapshot | None,
        at: int,
        *,
        mode: PolicyMode = "SHADOW",
        watchdog: WatchdogSnapshot | None = None,
        withdrawn: bool = False,
        strategies: tuple[str, ...] = (),
    ) -> AdaptivePolicyDecision:
        selected = next((c for c in board.candidates if c.slotId == board.selectedSlotId), None)
        base = (
            board.status == "READY"
            and selected is not None
            and selected.candidateStatus == "ACTIONABLE"
            and selected.direction in ("UP", "DOWN")
            and board.selectedDirection == selected.direction
            and board.selectedAssetName == selected.assetName
            and board.selectedScore == selected.rankScore
            and selected.platform == board.platform
            and selected.asOf == board.asOf
            and not selected.exclusionReasons
        )
        action: PolicyAction = "WATCH"
        status: EvidenceStatus = "NO_EVIDENCE"
        reasons: list[str] = []
        vetoes: list[str] = []
        warnings: list[str] = []
        rule = None
        if not base:
            action = "SKIP"
            vetoes.append("PHASE8_NOT_ELIGIBLE")
        if at < board.asOf or any(c.asOf > at for c in board.candidates):
            base = False
            vetoes.append("CHRONOLOGY_INVALID")
        if any(
            getattr(item, k) != SOURCE_VERSIONS[k]
            for item in (board, *board.candidates)
            for k in ("featureVersion", "regimeVersion", "strategyVersion", "rankingVersion")
        ):
            status = "VERSION_MISMATCH"
            vetoes.append("SOURCE_VERSION_MISMATCH")
        if selected is not None and (
            selected.primaryRegime == "UNCERTAIN" or selected.analysisStatus == "INVALID"
        ):
            vetoes.append("DATA_UNCERTAIN")
        # Even a supplied current snapshot is invisible before its own cutoff/generation.
        visible = (
            snapshot is not None and max(snapshot.generatedAt, snapshot.evidenceCutoffTime) <= at
        )
        if visible and snapshot is not None:
            status = snapshot.status
            if (
                snapshot.policyVersion != POLICY_VERSION
                or snapshot.watchdogVersion != WATCHDOG_VERSION
                or dict(snapshot.sourceVersions) != SOURCE_VERSIONS
            ):
                status = "VERSION_MISMATCH"
                vetoes.append("POLICY_VERSION_MISMATCH")
            else:
                eligible = [
                    r
                    for r in snapshot.rules
                    if r.validFrom <= at
                    and r.evidenceAvailableAt <= at
                    and r.expiresAt >= at
                    and selected is not None
                    and matches(r.scope, selected, strategies)
                ]
                # A broad veto cannot be bypassed by a positive, more specific slice.
                eligible.sort(
                    key=lambda r: (
                        r.action == "SKIP",
                        SPECIFICITY[r.scope.scopeType],
                        r.evidence.source == "REPLAY_OOS",
                        r.evidenceAvailableAt,
                        str(r.ruleId),
                    ),
                    reverse=True,
                )
                if eligible:
                    rule = eligible[0]
                    action = rule.action
                    status = "VALIDATED"
                    reasons.extend(rule.reasons)
                elif snapshot.rules:
                    status = (
                        "STALE" if all(r.expiresAt < at for r in snapshot.rules) else "NO_EVIDENCE"
                    )
        if watchdog is not None and watchdog.asOf <= at:
            if snapshot is None or watchdog.snapshotId != snapshot.snapshotId:
                vetoes.append("WATCHDOG_SNAPSHOT_MISMATCH")
            elif watchdog.state == "DRIFTED":
                withdrawn = True
            elif watchdog.state in ("VERSION_MISMATCH", "ACCOUNTING_UNAVAILABLE"):
                action = "WATCH"
                reasons.append(watchdog.state)
        if withdrawn:
            action, status = "WATCH", "DRIFTED"
            reasons.append("POLICY_WITHDRAWN_DRIFT")
        if vetoes:
            action = "SKIP"
            if any("VERSION_MISMATCH" in reason for reason in vetoes):
                status = "VERSION_MISMATCH"
            reasons.extend(vetoes)
        if not reasons:
            reasons.append("POLICY_OFF" if mode == "OFF" else status)
        if mode == "OFF":
            action = "ALLOW" if base and not vetoes else "SKIP"
        if mode == "SHADOW":
            warnings.append("SHADOW_BASELINE_UNCHANGED")
        values = dict(
            platform=board.platform,
            slotId=selected.slotId if selected else None,
            assetName=selected.assetName if selected else None,
            contextId=selected.contextId if selected else None,
            asOf=board.asOf,
            decisionAvailableAt=at,
            originalPhase8Action=board.status,
            originalDirection=board.selectedDirection,
            rankScore=selected.rankScore if selected else None,
            ensembleConfidence=selected.ensembleConfidence if selected else None,
            agreement=selected.agreement if selected else None,
            regime=selected.primaryRegime if selected else None,
            regimeConfidence=selected.regimeConfidence if selected else None,
            policyAction=action,
            baselineAction="ALLOW" if base else "SKIP",
            adaptiveAction=action,
            baseGatePassed=base,
            paperEligible=base and (mode != "PAPER_GATED" or action == "ALLOW"),
            mode=mode,
            evidenceStatus=status,
            evidenceId=rule.evidence.evidenceId if rule else None,
            evidenceAvailableAt=rule.evidenceAvailableAt if rule else None,
            snapshotId=snapshot.snapshotId if visible and snapshot is not None else None,
            matchedRules=(rule.ruleId,) if rule else (),
            vetoes=tuple(vetoes),
            warnings=tuple(warnings),
            reasons=tuple(reasons),
        )
        return AdaptivePolicyDecision.model_validate(
            {"decisionId": identity("decision", values), **values}
        )
