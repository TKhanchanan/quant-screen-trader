import { describe, expect, it } from 'vitest'
import { FeatureEngineStateSchema, FeatureStateSchema, IPC_CHANNELS } from '@quant-screen-trader/shared-types'

const slot = {
  platform: 'capitalbear', slotId: 1, assetName: 'EUR/USD OTC', primaryTimeframe: 'S5',
  microSamples: 42, contextId: '11111111-1111-4111-8111-111111111111',
  timeframes: [{ timeframe: 'S5', barCount: 18, hydratedBars: 0, status: 'WARMING', featureTime: 1 }]
}
describe('feature diagnostics bridge', () => {
  it('accepts engine state and tolerates fields the desktop does not render', () => {
    const parsed = FeatureStateSchema.parse({ featureVersion: 'qfe-v1', available: true, slots: [slot] })
    expect(parsed.slots[0]!.timeframes[0]!.status).toBe('WARMING')
    expect(parsed.slots[0]!.microSamples).toBe(42)
    expect(IPC_CHANNELS.features).toBe('features:state')
  })
  it('rejects unreadable state rather than inventing a default', () => {
    expect(FeatureStateSchema.safeParse({ featureVersion: 'qfe-v1', available: true, slots: [
      { ...slot, timeframes: [{ ...slot.timeframes[0], status: 'GREAT' }] }] }).success).toBe(false)
    expect(FeatureStateSchema.safeParse({ featureVersion: 'qfe-v1', slots: [] }).success).toBe(false)
    expect(FeatureStateSchema.safeParse({ featureVersion: 'qfe-v1', available: false,
      slots: [], extra: 1 }).success).toBe(false)
  })
  it('accepts the counters the engine reports alongside the state it renders', () => {
    // The engine also returns `rejected`; parsing its payload with the strict desktop contract
    // made every poll look like an unreachable engine.
    const payload = { featureVersion: 'qfe-v1', rejected: 0, slots: [slot] }
    const parsed = FeatureEngineStateSchema.parse(payload)
    expect(parsed.featureVersion).toBe('qfe-v1')
    expect(parsed.slots).toHaveLength(1)
    expect(FeatureStateSchema.safeParse(payload).success).toBe(false)
    expect(FeatureEngineStateSchema.safeParse({ featureVersion: 'qfe-v1' }).success).toBe(false)
  })
  it('reports an unreachable engine without fabricating slots', () => {
    const offline = FeatureStateSchema.parse({ featureVersion: 'unknown', available: false, slots: [] })
    expect(offline.slots).toHaveLength(0)
    expect(offline.available).toBe(false)
  })
})
