import { defaultCalibration, type ConfigurationResult, type Platform } from '@quant-screen-trader/shared-types'
import type { ExecutionManager } from '../../electron/main/execution-manager'
import type { MarketManager } from '../../electron/main/market-manager'
import type { PlatformBrowserManager } from '../../electron/main/platform-browser'
import { ShadowLiveTelemetry } from '../../electron/main/shadow-live'

/**
 * Phase 14 queue rehearsal. REHEARSAL ONLY — NOT REAL LIVE ACCEPTANCE.
 *
 * The real MarketManager transport and the real Phase 14 telemetry observer, under a clock the
 * caller controls: normal load, a burst larger than one transport batch but inside capacity, and
 * a stall long enough to push past capacity, where the bound must drop rather than grow.
 */

export const QUEUE_CAPACITY = 180
export const TRANSPORT_BATCH = 18
const PLATFORMS: readonly Platform[] = ['capitalbear', 'iqoption']
const PROFILE = '4f3e2d1c-0b9a-4876-9543-210fedcba987'

export interface QueueHarness {
  /** Constructs the real MarketManager, with OCR stubbed by the calling test file. */
  create: (browsers: PlatformBrowserManager) => MarketManager
  /** Advances fake timers, flushing the promises they release. */
  advance: (ms: number) => Promise<void>
}

export interface PhaseResult { maxQueue: number; maxBatch: number; batches: number; dropped: number }
export interface QueueResult {
  normal: PhaseResult; burst: PhaseResult; overflow: PhaseResult
  telemetry: { bodies: number; maxQueueDepth: number; armedEver: boolean; maxBrokerPresses: number; sample: unknown }
}

function configuration(platform: Platform): ConfigurationResult {
  const stamp = '2026-03-02T00:00:00.000Z'
  return {
    configuration: { platform, slots: Array.from({ length: 9 }, (_, index) => ({ id: index + 1, enabled: true,
      platform, assetName: `ASSET ${index + 1}` })) },
    calibrations: [{ id: PROFILE, platform, name: 'Rehearsal grid', createdAt: stamp, updatedAt: stamp,
      referenceBrowserWidth: 1440, referenceBrowserHeight: 760, zoomFactor: .7, slots: defaultCalibration(platform) }],
    presets: [], activeCalibrationId: PROFILE
  }
}

export async function queueRehearsal(harness: QueueHarness): Promise<QueueResult> {
  const surface = { available: true, paused: false, revision: 1, zoomFactor: .7, gridReady: true,
    bounds: { x: 0, y: 0, width: 1440, height: 760 } }
  const browsers = {
    observationSurface: () => surface,
    chartSlot: (_platform: Platform, slotId: number) => slotId,
    command: () => ({ grid: { confidence: 1 } }),
    captureSlots: (contexts: { slotId: number }[]) => Promise.resolve({ observedAt: Date.now(), images: new Map(contexts.map(c =>
      [c.slotId, { width: 1, height: 1, grayscale: new Uint8Array([c.slotId]) }])) })
  } as unknown as PlatformBrowserManager
  let stallUntil = 0, batches: number[] = []
  const bodies: { queueDepth: number; armed: boolean; brokerPresses: number }[] = []
  let sample: unknown = null
  const original = globalThis.fetch
  globalThis.fetch = (async (input: URL | string, init?: RequestInit): Promise<Response> => {
    const path = new URL(String(input)).pathname
    const body = JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>
    if (path === '/api/shadow-live/telemetry') {
      bodies.push(body as unknown as { queueDepth: number; armed: boolean; brokerPresses: number })
      sample ??= body
      return Response.json({ recorded: true })
    }
    if (path !== '/api/market/observations') return Response.json({ reset: 1 })
    const observations = body.observations as { platform: Platform; slotId: number; contextId: string }[]
    batches.push(observations.length)
    // A slow engine holds the request open; the desktop sends nothing else meanwhile.
    const wait = Math.max(0, stallUntil - Date.now())
    if (wait) await new Promise(resolve => setTimeout(resolve, wait))
    return Response.json({ accepted: observations.length, rejected: 0, queueDepth: 0,
      slots: observations.map(o => ({ platform: o.platform, slotId: o.slotId, contextId: o.contextId,
        secondSamples: 5, m1Samples: 6, m1State: 'FORMING' })) })
  }) as typeof fetch
  const market = harness.create(browsers)
  const telemetry = new ShadowLiveTelemetry(market, { state: () => ({ armed: false, tickets: [], lastBoardAsOf: null,
    settings: { mode: 'OFF' } }) } as unknown as ExecutionManager,
    { host: '127.0.0.1', port: 8765, healthUrl: 'http://127.0.0.1:8765/health' })
  const dropped = () => PLATFORMS.reduce((total, platform) => total + market.command({ platform, operation: 'state' }).dropped, 0)
  const phase = async (duration: number): Promise<PhaseResult> => {
    batches = []
    const droppedBefore = dropped()
    let maxQueue = 0
    for (let elapsed = 0; elapsed < duration; elapsed += 50) {
      await harness.advance(50)
      maxQueue = Math.max(maxQueue, market.operationalState()[0]?.queueDepth ?? 0)
    }
    return { maxQueue, maxBatch: Math.max(0, ...batches), batches: batches.length, dropped: dropped() - droppedBefore }
  }
  try {
    for (const platform of PLATFORMS) {
      market.configure(configuration(platform))
      market.command({ platform, operation: 'start', intervalMs: 500 })
    }
    const normal = await phase(10_000)
    // One slow request up to the desktop's two-second request timeout: two or three capture
    // rounds of nine slots per platform queue behind it.
    stallUntil = Date.now() + 2_000
    const burst = await phase(6_000)
    stallUntil = Date.now() + 12_000
    const overflow = await phase(16_000)
    return { normal, burst, overflow, telemetry: { bodies: bodies.length,
      maxQueueDepth: Math.max(0, ...bodies.map(b => b.queueDepth)), armedEver: bodies.some(b => b.armed),
      maxBrokerPresses: Math.max(0, ...bodies.map(b => b.brokerPresses)), sample } }
  } finally {
    telemetry.stop()
    market.stop()
    globalThis.fetch = original
  }
}

export function queueChecks(result: QueueResult): { name: string; passed: boolean; detail: Record<string, unknown> }[] {
  const { normal, burst, overflow, telemetry } = result
  return [
    { name: 'normal load: batches <= 18, queue within one batch', passed: normal.batches > 0 &&
      normal.maxBatch <= TRANSPORT_BATCH && normal.maxQueue <= TRANSPORT_BATCH && normal.dropped === 0, detail: { ...normal } },
    { name: 'burst: queue exceeded one batch, stayed inside 180, nothing dropped', passed: burst.maxQueue > TRANSPORT_BATCH &&
      burst.maxQueue <= QUEUE_CAPACITY && burst.maxBatch <= TRANSPORT_BATCH && burst.dropped === 0, detail: { ...burst } },
    { name: 'beyond capacity: explicit bounded drops, never above 180', passed: overflow.maxQueue === QUEUE_CAPACITY &&
      overflow.dropped > 0 && overflow.maxBatch <= TRANSPORT_BATCH, detail: { ...overflow } },
    { name: 'telemetry reported the bounded queue, never armed, no presses', passed: telemetry.bodies > 0 &&
      telemetry.maxQueueDepth <= QUEUE_CAPACITY && !telemetry.armedEver && telemetry.maxBrokerPresses === 0,
    detail: { bodies: telemetry.bodies, maxQueueDepth: telemetry.maxQueueDepth } }
  ]
}
