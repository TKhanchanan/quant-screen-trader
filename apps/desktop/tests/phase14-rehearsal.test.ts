import { afterEach, describe, expect, it, vi } from 'vitest'
import type { OpportunityBoard, Platform } from '@quant-screen-trader/shared-types'
import { observation, VisualMarketDataProvider } from '../electron/main/market-providers'
import { gateBoard, gateScenarios, runTimeline, SOURCE_LABEL, timelineChecks, type TimelineRecord } from './rehearsal/phase14-execution'
import { queueChecks, queueRehearsal } from './rehearsal/phase14-queue'
vi.mock('../electron/main/market-ocr', () => ({ TesseractOCRProvider: class { stop = vi.fn(async () => {}) } }))
const { MarketManager } = await import('../electron/main/market-manager')

// SYNTHETIC_REHEARSAL fixtures for the rehearsal harness itself. The full run replays the engine's
// own board timeline (npm run rehearsal:phase14); these boards exist only to exercise the gates.
const START = Date.UTC(2026, 2, 2, 9)
const SECOND = 1_000, MINUTE = 60 * SECOND

function timeline(options: { reuseBoardAfterRestart?: boolean } = {}): TimelineRecord[] {
  const records: TimelineRecord[] = [{ t: START, type: 'header', start: START, end: START + 20 * MINUTE, pollOffsetMs: 500,
    scenario: 'ci-synthetic', label: 'REHEARSAL ONLY - NOT REAL LIVE ACCEPTANCE', source: SOURCE_LABEL,
    captureStart: { capitalbear: START, iqoption: START }, captureStop: START + 20 * MINUTE }]
  const assets = (suffix: Record<number, string> = {}) =>
    Object.fromEntries(Array.from({ length: 9 }, (_, i) => [String(i + 1), suffix[i + 1] ?? `ASSET ${i + 1}`]))
  const surface = (t: number, platform: Platform, captureRunning: boolean, revision = 1, slots = assets()): TimelineRecord =>
    ({ t, type: 'surface', platform, available: true, revision, captureRunning, slots, identityUncertain: [] })
  records.push({ t: START, type: 'process', event: 'start', segment: 1 },
    { t: START, type: 'guard', canOpenNewEntry: true, blockReason: 'GUARD_DISABLED' },
    surface(START, 'capitalbear', true), surface(START, 'iqoption', true))
  let readyBeforeRestart: OpportunityBoard | null = null
  for (let t = START + 5 * SECOND; t < START + 20 * MINUTE; t += 5 * SECOND) {
    // Boards for the renamed slot start after the rename; none are served while the app is down.
    const renamed = t > START + 6 * MINUTE
    const down = t >= START + 12 * MINUTE && t < START + 13 * MINUTE + 35 * SECOND
    const asset = renamed ? 'ASSET 3B' : 'ASSET 3'
    const kind = Math.floor((t - START) / 5_000) % 4
    const board = kind === 0 ? gateBoard('capitalbear', t, {}, { assetName: asset })
      : kind === 1 ? gateBoard('capitalbear', t, { status: 'NO_OPPORTUNITY', selectedSlotId: null, selectedAssetName: null,
        selectedDirection: null, selectedScore: null, candidates: [] })
        : kind === 2 ? gateBoard('capitalbear', t, { status: 'PARTIAL', missingSlots: [4] }, { assetName: asset })
          : gateBoard('capitalbear', t, {}, { slotId: 9, score: .4 })
    if (kind === 0 && t < START + 12 * MINUTE) readyBeforeRestart = board
    if (!down) records.push({ t, type: 'board', platform: 'capitalbear', board })
    if (!down && (t - START) % MINUTE === 0) records.push({ t, type: 'board', platform: 'iqoption', board: gateBoard('iqoption', t) })
    if (t === START + 6 * MINUTE) records.push(surface(t, 'capitalbear', true, 1, assets({ 3: 'ASSET 3B' })))
    if (t === START + 8 * MINUTE) records.push(surface(t, 'iqoption', true, 2))
    if (t === START + 10 * MINUTE) records.push({ t, type: 'engineBusy', until: t + 5 * SECOND })
    if (t === START + 12 * MINUTE) records.push({ t, type: 'process', event: 'stop', segment: 1 })
    if (t === START + 13 * MINUTE) {
      records.push({ t, type: 'process', event: 'start', segment: 2 }, surface(t, 'capitalbear', false, 1, assets({ 3: 'ASSET 3B' })),
        surface(t, 'iqoption', false, 1))
    }
    if (t === START + 13 * MINUTE + 30 * SECOND) {
      records.push(surface(t, 'capitalbear', true, 1, assets({ 3: 'ASSET 3B' })), surface(t, 'iqoption', true, 1))
      // The negative case: the restarted manager is offered a board it already acted on.
      if (options.reuseBoardAfterRestart && readyBeforeRestart)
        records.push({ t, type: 'board', platform: 'capitalbear', board: readyBeforeRestart })
    }
  }
  return records.sort((a, b) => a.t - b.t)
}

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks() })

describe('Phase 14 execution rehearsal (PAPER, SYNTHETIC_REHEARSAL)', () => {
  it('produces every refusal code around the production defaults and never presses in PAPER', async () => {
    vi.useFakeTimers()
    const results = await gateScenarios(ms => vi.setSystemTime(ms))
    expect(results.filter(result => !result.passed)).toEqual([])
    expect(results.length).toBeGreaterThanOrEqual(15)
  })

  it('evaluates the timeline through the real manager with no mismatch, duplicate or press', async () => {
    const result = await runTimeline(timeline(), { setupDelayMs: 0 })
    expect(timelineChecks(result).filter(check => !check.passed)).toEqual([])
    const capitalbear = result.platforms.capitalbear
    expect(capitalbear.paperTickets).toBeGreaterThan(0)
    expect(capitalbear.boardRefusals.BOARD_STATUS).toBeGreaterThan(0)
    expect(capitalbear.boardRefusals.NO_SELECTION).toBeGreaterThan(0)
    expect(capitalbear.boardRefusals.BELOW_LIMITS).toBeGreaterThan(0)
    expect(result.platforms.iqoption.stateBlockedTicks.CONTROL_MAP_STALE).toBeGreaterThan(0)
    expect(result.platforms.iqoption.controlMapRemeasures).toBe(1)
    expect(result.pressCalls).toBe(0)
  })

  it('detects a duplicate execution ticket when a restarted manager is offered an old board', async () => {
    const result = await runTimeline(timeline({ reuseBoardAfterRestart: true }), { setupDelayMs: 0 })
    const duplicate = timelineChecks(result).find(check => check.name.startsWith('duplicate execution tickets'))
    expect(result.platforms.capitalbear.duplicateTickets).toBeGreaterThan(0)
    expect(duplicate?.passed).toBe(false)
  })

  it('keeps the real transport queue bounded: bursts inside 180, explicit drops beyond it', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(START)
    vi.spyOn(VisualMarketDataProvider.prototype, 'observeImage').mockImplementation(async context =>
      observation(context, 'VISUAL', { asset: context.assetName, price: String(context.slotId), confidence: .94 }, Date.now()))
    const result = await queueRehearsal({ create: browsers => new MarketManager(browsers,
      { host: '127.0.0.1', port: 8765, healthUrl: 'http://127.0.0.1:8765/health' }),
    advance: ms => vi.advanceTimersByTimeAsync(ms).then(() => undefined) })
    expect(queueChecks(result).filter(check => !check.passed)).toEqual([])
  })
})
