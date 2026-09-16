import { randomUUID } from 'node:crypto'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { defaultCalibration, type ConfigurationResult, type Platform } from '@quant-screen-trader/shared-types'
import type { PlatformBrowserManager } from '../electron/main/platform-browser'
import { observation, VisualMarketDataProvider, type ObservationContext } from '../electron/main/market-providers'
import { createPlaceholderSlots } from '../src/renderer/src/features/slots/createPlaceholderSlots'
vi.mock('../electron/main/market-ocr', () => ({ TesseractOCRProvider: class { stop = vi.fn(async () => {}) } }))
const { MarketManager } = await import('../electron/main/market-manager')
function config(platform: Platform, count = 9): ConfigurationResult {
  const stamp = new Date().toISOString(), id = randomUUID()
  return { configuration: { platform, slots: createPlaceholderSlots(platform).map(s => ({ ...s, enabled: s.id <= count, assetName: `ASSET ${s.id}` })) },
    calibrations: [{ id, name: 'Fixture', platform, createdAt: stamp, updatedAt: stamp, slots: defaultCalibration(), zoomFactor: .7,
      referenceBrowserWidth: 900, referenceBrowserHeight: 600 }], activeCalibrationId: id, presets: [] }
}
let manager: InstanceType<typeof MarketManager>
const surface = { available: true, paused: false, revision: 0, zoomFactor: .7, gridReady: true, bounds: { x: 0, y: 0, width: 900, height: 600 } }
const capture = vi.fn(async (contexts: ObservationContext[]) => ({ observedAt: Date.now(), images: new Map(contexts.map(c =>
  [c.slotId, { width: 1, height: 1, grayscale: new Uint8Array([c.slotId]) }])) }))
const deliver = (c: ObservationContext) => observation(c, 'VISUAL', { asset: c.assetName, price: String(c.slotId), confidence: c.slotId === 2 ? .6 : .94 }, Date.now())
beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2026-09-07T12:30:00Z')); surface.available = true; surface.paused = false; surface.revision = 0
  capture.mockClear()
  vi.spyOn(VisualMarketDataProvider.prototype, 'observeImage').mockImplementation(async c => deliver(c))
  vi.stubGlobal('fetch', vi.fn(async (url: URL | string, init?: RequestInit) => {
    if (String(url).endsWith('/api/market/slots/reset')) return new Response(JSON.stringify({ reset: 1 }))
    const { observations } = JSON.parse(String(init?.body)) as { observations: { platform: Platform; slotId: number; contextId: string }[] }
    return new Response(JSON.stringify({ accepted: observations.length, rejected: 0, queueDepth: 0,
      slots: observations.map(o => ({ platform: o.platform, slotId: o.slotId, contextId: o.contextId, secondSamples: 5, m1Samples: 6, m1State: 'FORMING' })) }))
  }))
  manager = new MarketManager({ observationSurface: () => surface, chartSlot: (_p: Platform, id: number) => id,
    command: () => ({ grid: { confidence: 1 } }), captureSlots: capture } as unknown as PlatformBrowserManager,
  { host: '127.0.0.1', port: 8765, healthUrl: 'http://127.0.0.1:8765/health' })
})
afterEach(() => { manager.stop(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals() })
it.each(['capitalbear', 'iqoption'] as const)('probes all nine %s slots from one batch without starting observation', async platform => {
  manager.configure(config(platform))
  const snapshot = await manager.probe(platform)
  expect(snapshot.running).toBe(false); expect(capture).toHaveBeenCalledTimes(1)
  expect(capture.mock.calls[0]![0]).toHaveLength(9)
  expect(snapshot.slots.every(s => s.observation?.price === s.slotId && s.attemptCount === 1)).toBe(true)
  expect(snapshot.slots[1]?.observation?.dataQuality.state).toBe('UNCERTAIN')
  await vi.advanceTimersByTimeAsync(500)
  const bodies = vi.mocked(fetch).mock.calls.map(([, init]) => JSON.parse(String(init?.body)) as { observations?: { slotId: number }[] })
  expect(bodies.flatMap(b => b.observations ?? []).map(o => o.slotId).sort()).toEqual([1,2,3,4,5,6,7,8,9])
  expect(capture).toHaveBeenCalledTimes(1)
})
it('captures all crops first and lets other slots publish while one worker is slow', async () => {
  let release!: () => void
  vi.mocked(VisualMarketDataProvider.prototype.observeImage).mockImplementation(async c => {
    if (c.slotId === 1) await new Promise<void>(r => { release = r })
    if (c.slotId === 3) throw new Error('Unreadable callout')
    return deliver(c)
  })
  manager.configure(config('iqoption'))
  const pending = manager.probe('iqoption')
  await vi.advanceTimersByTimeAsync(100)
  const state = manager.command({ platform: 'iqoption', operation: 'state' })
  expect(state.slots.every(s => s.attemptCount === 1)).toBe(true)
  expect(state.slots[8]?.observation?.price).toBe(9)
  expect(state.slots[2]?.diagnostics?.message).toBe('Unreadable callout')
  release(); await pending
  expect(state.slots[0]?.observation?.price).toBe(1)
})
it('gives every enabled slot repeated opportunities, updates engine samples, and respects pause/disable', async () => {
  for (const platform of ['iqoption', 'capitalbear'] as const) {
    manager.configure(config(platform)); manager.command({ platform, operation: 'start', intervalMs: 250 })
  }
  await vi.advanceTimersByTimeAsync(1200)
  for (const platform of ['iqoption', 'capitalbear'] as const) {
    const s = manager.command({ platform, operation: 'state' })
    expect(s.slots.every(v => (v.attemptCount ?? 0) >= 3 && v.secondSamples === 5)).toBe(true)
    expect(s.captureRate).toBeGreaterThan(0); expect(s.engineAvailable).toBe(true)
  }
  surface.paused = true; capture.mockClear(); await vi.advanceTimersByTimeAsync(1000)
  expect(capture).not.toHaveBeenCalled()
  surface.paused = false
  manager.configure(config('capitalbear', 0)); capture.mockClear(); await vi.advanceTimersByTimeAsync(1000)
  expect(capture.mock.calls.every(([contexts]) => contexts.every(c => c.platform === 'iqoption'))).toBe(true)
  expect(manager.command({ platform: 'iqoption', operation: 'stop' }).slots.every(s => s.secondSamples === 0)).toBe(true)
})
it('discards an in-flight frame across navigation and configuration changes', async () => {
  let release!: () => void
  vi.mocked(VisualMarketDataProvider.prototype.observeImage).mockImplementation(async c => {
    await new Promise<void>(r => { release = r }); return deliver(c)
  })
  manager.configure(config('capitalbear', 1)); const pending = manager.probe('capitalbear')
  await vi.advanceTimersByTimeAsync(1)
  manager.configure(config('capitalbear', 1)); surface.revision++
  release(); await pending
  expect(manager.command({ platform: 'capitalbear', operation: 'state' }).slots[0]?.observation).toBeNull()
})
it('returns an explicit failure per enabled slot when the frame cannot be captured', async () => {
  capture.mockRejectedValueOnce(new Error('Surface unavailable'))
  manager.configure(config('capitalbear'))
  const s = await manager.probe('capitalbear')
  expect(s.slots.every(v => v.observation === null && v.diagnostics?.message === 'Surface unavailable' && v.attemptCount === 1)).toBe(true)
  expect(manager.operationalState()[0]!.slots.every(v => !v.captureEligible && v.lastCaptureAttemptAt === null)).toBe(true)
})
