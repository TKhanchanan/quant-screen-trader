import { describe, expect, it } from 'vitest'
import {
  IPC_CHANNELS, STRATEGY_LABELS, StrategyEngineStateSchema, StrategyStateSchema, percent, voteLabel
} from '@quant-screen-trader/shared-types'

const slot = {
  platform: 'capitalbear', slotId: 1, assetName: 'EUR/USD OTC', asOf: 1_788_873_600_000,
  primaryRegime: 'TREND_UP', regimeConfidence: 0.72, direction: 'UP', confidence: 0.68,
  eligibleStrategies: 4, activeStrategies: 3, votes: [
    { strategyId: 'trend_follow_v1', direction: 'UP', confidence: 0.71 },
    { strategyId: 'breakout_v1', direction: 'SKIP', confidence: 0 }
  ], vetoes: []
}
const versions = { featureVersion: 'qfe-v2', regimeVersion: 'qst-regime-v1', strategyVersion: 'qst-strategy-v1' }

describe('strategy diagnostics bridge', () => {
  it('accepts engine state and tolerates counters the desktop does not render', () => {
    const parsed = StrategyStateSchema.parse({ ...versions, available: true, slots: [slot] })
    expect(parsed.slots[0]!.primaryRegime).toBe('TREND_UP')
    expect(parsed.slots[0]!.votes).toHaveLength(2)
    expect(IPC_CHANNELS.strategy).toBe('strategy:state')
    // The engine also reports `evaluated` and `duplicates`; parsing its payload with the
    // strict desktop contract would make every poll look like an unreachable engine.
    const payload = { ...versions, evaluated: 12, duplicates: 0, slots: [slot] }
    expect(StrategyEngineStateSchema.parse(payload).slots).toHaveLength(1)
    expect(StrategyStateSchema.safeParse(payload).success).toBe(false)
  })

  it('rejects an unreadable reading rather than inventing a default', () => {
    expect(StrategyStateSchema.safeParse({ ...versions, available: true,
      slots: [{ ...slot, direction: 'BUY' }] }).success).toBe(false)
    expect(StrategyStateSchema.safeParse({ ...versions, available: true,
      slots: [{ ...slot, primaryRegime: 'BULLISH' }] }).success).toBe(false)
    expect(StrategyStateSchema.safeParse({ ...versions, available: true,
      slots: [{ ...slot, confidence: 1.4 }] }).success).toBe(false)
    expect(StrategyStateSchema.safeParse({ ...versions, slots: [] }).success).toBe(false)
  })

  it('reports an unreachable engine without fabricating an opinion', () => {
    const offline = StrategyStateSchema.parse({
      featureVersion: 'unknown', regimeVersion: 'unknown', strategyVersion: 'unknown',
      available: false, slots: []
    })
    expect(offline.slots).toHaveLength(0)
    expect(offline.available).toBe(false)
  })

  it('renders whatever versions the engine reports rather than pinning its own', () => {
    const parsed = StrategyStateSchema.parse({
      featureVersion: 'qfe-v3', regimeVersion: 'qst-regime-v2', strategyVersion: 'qst-strategy-v9',
      available: true, slots: []
    })
    expect(parsed.strategyVersion).toBe('qst-strategy-v9')
  })

  it('formats a compact diagnostics line without implying a probability', () => {
    expect(percent(0.684)).toBe('68%')
    expect(voteLabel(slot.votes[0]! as never)).toBe('Trend UP 0.71')
    // An abstention has no confidence to show, so none is shown.
    expect(voteLabel(slot.votes[1]! as never)).toBe('Breakout SKIP')
    expect(Object.keys(STRATEGY_LABELS)).toHaveLength(6)
  })
})
