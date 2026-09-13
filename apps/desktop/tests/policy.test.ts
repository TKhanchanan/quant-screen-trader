import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { PolicyModeSchema, PolicyStateSchema, emptyPolicyState } from '@quant-screen-trader/shared-types'
import { PolicyPanel, PolicyResult } from '../src/renderer/src/components/PolicyPanel'

describe('analytical policy diagnostics', () => {
  it('defaults to SHADOW and rejects a real mode', () => {
    expect(PolicyStateSchema.parse(emptyPolicyState()).mode).toBe('SHADOW')
    expect(PolicyModeSchema.safeParse('LIVE_REAL').success).toBe(false)
    expect(renderToStaticMarkup(createElement(PolicyPanel))).toContain('Adaptive Policy')
  })
  it('shows the baseline alongside a veto and its reasons', () => {
    const state = PolicyStateSchema.parse({ ...emptyPolicyState(), decisions: [{
      decisionId: '00000000-0000-5000-8000-000000000001', platform: 'capitalbear',
      slotId: 1, assetName: 'EUR/USD', asOf: 1000, decisionAvailableAt: 1200,
      baselineAction: 'ALLOW', policyAction: 'SKIP', originalDirection: 'UP',
      mode: 'SHADOW', evidenceStatus: 'VALIDATED', reasons: ['MATERIALLY_ADVERSE_OOS'],
      analyticalOnly: true, appliedToLiveExecution: false
    }] })
    const html = renderToStaticMarkup(createElement(PolicyResult, { state }))
    expect(html).toContain('Phase 8: <strong>ALLOW</strong>')
    expect(html).toContain('Policy: <strong>SKIP</strong>')
    expect(html).toContain('MATERIALLY_ADVERSE_OOS')
    expect(html).not.toContain('<button')
  })
})
