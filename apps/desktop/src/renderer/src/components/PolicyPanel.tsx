import { useEffect, useState, type JSX } from 'react'
import { emptyPolicyState, type PolicyState } from '@quant-screen-trader/shared-types'

export function PolicyResult({ state }: { state: PolicyState }): JSX.Element {
  return <>
    <p>Mode: <strong>{state.mode}</strong> · {state.policyVersion} · {state.watchdogVersion}</p>
    <p>Snapshot: {state.snapshotId ?? '—'}</p>
    <p>Evidence cutoff: {state.evidenceCutoffTime === null ? '—'
      : new Date(state.evidenceCutoffTime).toISOString()} · Evidence: {state.evidenceStatus}</p>
    <p>Watchdog: {state.watchdogState}</p>
    {state.error && <p role="alert">{state.error}</p>}
    {state.decisions.length === 0 && <p>ยังไม่มีการประเมินนโยบาย</p>}
    <ol className="analytics-bins">{state.decisions.slice(-5).reverse().map(decision =>
      <li key={decision.decisionId}>
        {decision.platform} · {decision.assetName ?? '—'} · {decision.originalDirection ?? '—'}
        <br />Phase 8: <strong>{decision.baselineAction}</strong>
        {' · '}Policy: <strong>{decision.policyAction}</strong>
        <br /><small>{decision.reasons.join(' · ')}</small>
      </li>)}</ol>
    <small>SHADOW บันทึกผลนโยบาย โดยผลฐานยังทำงานตามเดิม · PAPER_GATED กรองเฉพาะ paper
      {' '}· ไม่ควบคุมคำสั่งซื้อขายกับโบรกเกอร์</small>
  </>
}

export function PolicyPanel(): JSX.Element {
  const [state, setState] = useState<PolicyState>(() => emptyPolicyState())
  const [open, setOpen] = useState(false)
  useEffect(() => {
    if (!open) return
    let disposed = false
    let pending = false
    const poll = (): void => {
      if (pending) return
      pending = true
      void window.quantScreenTrader.policy().then(next => {
        if (!disposed) setState(next)
      }).catch(() => {
        if (!disposed) setState(emptyPolicyState('ต่อเอ็นจิ้นไม่ได้'))
      }).finally(() => { pending = false })
    }
    poll()
    const timer = window.setInterval(poll, 2000)
    return () => { disposed = true; window.clearInterval(timer) }
  }, [open])
  return <section className="analytics-panel"><details open={open}
    onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>Adaptive Policy</summary><PolicyResult state={state} />
  </details></section>
}
