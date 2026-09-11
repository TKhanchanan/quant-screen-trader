import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defaultCalibration, defaultExecutionSettings, directionForVote,
  type ConfigurationResult, type ControlMap, type ExecutionSettings, type OpportunityBoard,
  type Platform } from '@quant-screen-trader/shared-types'
import { ExecutionManager } from '../electron/main/execution-manager'
import type { OrderExecutor, PressResult } from '../electron/main/order-executor'
import type { PlatformBrowserManager } from '../electron/main/platform-browser'

const PLATFORM: Platform = 'capitalbear'
const PROFILE_ID = '11111111-1111-4111-8111-111111111111'

function configuration(): ConfigurationResult {
  const now = new Date().toISOString()
  return {
    configuration: { platform: PLATFORM, slots: Array.from({ length: 9 }, (_, index) => ({
      id: index + 1, enabled: true, assetName: `PAIR${index + 1}`, platform: PLATFORM })) },
    calibrations: [{ id: PROFILE_ID, platform: PLATFORM, name: 'Grid', createdAt: now, updatedAt: now,
      referenceBrowserWidth: 1440, referenceBrowserHeight: 760, zoomFactor: .7, slots: defaultCalibration(PLATFORM) }],
    presets: [], activeCalibrationId: PROFILE_ID
  }
}

function controlMap(confidence = .95): ControlMap {
  return { platform: PLATFORM, zoomFactor: .7, surfaceRevision: 4, directionForGreen: 'HIGHER',
    measuredAt: new Date().toISOString(), reasons: [],
    slots: Array.from({ length: 9 }, (_, index) => ({ slotId: index + 1, confidence,
      higher: { x: .3, y: .2 + index * .01 }, lower: { x: .3, y: .25 + index * .01 },
      panelBounds: { x: .3, y: .1, width: .05, height: .2 } })) }
}

function board(overrides: Partial<OpportunityBoard> = {}): OpportunityBoard {
  return {
    platform: PLATFORM, asOf: 1_700_000_000_000, primaryTimeframe: 'S5',
    featureVersion: 'qfe-v2', regimeVersion: 'r1', strategyVersion: 's1', rankingVersion: 'qst-ranking-v1',
    status: 'READY', expectedSlots: 9, receivedSlots: 9, rankedSlots: 9, excludedSlots: 0, missingSlots: [],
    candidates: [{ platform: PLATFORM, slotId: 3, assetName: 'PAIR3', asOf: 1_700_000_000_000, direction: 'UP',
      primaryRegime: 'TREND_UP', ensembleConfidence: .8, rankScore: .82, candidateStatus: 'ACTIONABLE',
      rank: 1, rankReasonCodes: [], exclusionReasons: [] }],
    selectedSlotId: 3, selectedAssetName: 'PAIR3', selectedDirection: 'UP', selectedScore: .82,
    runnerUpSlotId: null, leadMargin: .2, watchlist: [], reasons: [], ...overrides
  }
}

interface Harness {
  manager: ExecutionManager
  presses: { canvasSlotId: number; direction: string }[]
  setBoard: (value: OpportunityBoard | null) => void
  setPress: (value: PressResult) => void
  surface: { available: boolean; paused: boolean; gridReady: boolean; revision: number; zoomFactor: number }
}

function harness(): Harness {
  const surface = { available: true, paused: false, gridReady: true, revision: 4, zoomFactor: .7,
    bounds: { x: 0, y: 0, width: 1440, height: 760 } }
  let current: OpportunityBoard | null = board()
  let press: PressResult = { pressedAt: Date.now(), latencyMs: 12, verified: true, reasons: [] }
  const presses: { canvasSlotId: number; direction: string }[] = []
  const browsers = {
    observationSurface: () => surface,
    // Identity mapping: the tab order is stable in these tests, so a configured slot is its cell.
    chartSlot: (_platform: Platform, slotId: number) => slotId
  } as unknown as PlatformBrowserManager
  const executor = {
    calibrate: () => Promise.resolve(controlMap()),
    press: (_platform: Platform, _map: ControlMap, canvasSlotId: number, direction: string) => {
      presses.push({ canvasSlotId, direction })
      return Promise.resolve({ ...press, pressedAt: Date.now() })
    }
  } as unknown as OrderExecutor
  vi.stubGlobal('fetch', () => Promise.resolve({ ok: !!current, json: () => Promise.resolve({
    featureVersion: 'qfe-v2', regimeVersion: 'r1', strategyVersion: 's1', rankingVersion: 'qst-ranking-v1',
    board: current }) } as Response))
  const manager = new ExecutionManager(browsers, executor, { healthUrl: 'http://127.0.0.1:8000' } as never)
  manager.configure(configuration())
  return { manager, presses, surface,
    setBoard: value => { current = value }, setPress: value => { press = value } }
}

/** Drive one decision cycle without waiting on the manager's own timer. */
async function cycle(manager: ExecutionManager): Promise<void> {
  await (manager as unknown as { tick: () => Promise<void> }).tick()
}

async function armed(settings: Partial<ExecutionSettings> = {}): Promise<Harness> {
  const h = harness()
  await h.manager.command({ operation: 'calibrateControls', platform: PLATFORM })
  await h.manager.command({ operation: 'settings', platform: PLATFORM,
    settings: { ...defaultExecutionSettings(), mode: 'AUTO', ...settings } })
  await h.manager.command({ operation: 'arm', platform: PLATFORM })
  return h
}

let live: ExecutionManager[] = []
beforeEach(() => { live = [] })
afterEach(() => { for (const manager of live) manager.stop(); vi.unstubAllGlobals() })
function track(h: Harness): Harness { live.push(h.manager); return h }

describe('direction mapping', () => {
  it('turns only a directional vote into a control', () => {
    expect(directionForVote('UP')).toBe('HIGHER')
    expect(directionForVote('DOWN')).toBe('LOWER')
    expect(directionForVote('NEUTRAL')).toBeNull()
    expect(directionForVote('SKIP')).toBeNull()
  })
})

describe('execution gates', () => {
  it('starts off, disarmed and uncalibrated', () => {
    const h = track(harness())
    const state = h.manager.state(PLATFORM)
    expect(state.settings.mode).toBe('OFF')
    expect(state.armed).toBe(false)
    expect(state.blocked).toContain('MODE_OFF')
    expect(state.blocked).toContain('NO_CONTROL_MAP')
  })
  it('refuses to arm while anything is blocking', async () => {
    const h = track(harness())
    await h.manager.command({ operation: 'settings', platform: PLATFORM,
      settings: { ...defaultExecutionSettings(), mode: 'AUTO' } })
    await expect(h.manager.command({ operation: 'arm', platform: PLATFORM })).rejects.toThrow('NO_CONTROL_MAP')
  })
  it('presses the control the board named, once', async () => {
    const h = track(await armed())
    await cycle(h.manager)
    await cycle(h.manager)
    expect(h.presses).toEqual([{ canvasSlotId: 3, direction: 'HIGHER' }])
    const state = h.manager.state(PLATFORM)
    expect(state.tickets[0]!.state).toBe('CONFIRMED')
    expect(state.tickets[0]!.slotId).toBe(3)
    expect(state.tickets).toHaveLength(1)
  })
  it('acts again only on a newer board', async () => {
    const h = track(await armed({ limits: { ...defaultExecutionSettings().limits, cooldownMs: 0 } }))
    await cycle(h.manager)
    h.setBoard(board({ asOf: 1_700_000_005_000, selectedSlotId: 5, selectedAssetName: 'PAIR5',
      selectedDirection: 'DOWN', candidates: [{ platform: PLATFORM, slotId: 5, assetName: 'PAIR5',
        asOf: 1_700_000_005_000, direction: 'DOWN', primaryRegime: 'TREND_UP', ensembleConfidence: .8,
        rankScore: .82, candidateStatus: 'ACTIONABLE', rank: 1, rankReasonCodes: [], exclusionReasons: [] }] }))
    await cycle(h.manager)
    expect(h.presses).toEqual([{ canvasSlotId: 3, direction: 'HIGHER' },
      { canvasSlotId: 5, direction: 'LOWER' }])
  })
  it('does not press a board that named no leader, or one below the operator limits', async () => {
    const h = track(await armed({ limits: { ...defaultExecutionSettings().limits, cooldownMs: 0 } }))
    h.setBoard(board({ asOf: 1, selectedSlotId: null, selectedDirection: null, selectedScore: null }))
    await cycle(h.manager)
    h.setBoard(board({ asOf: 2, selectedScore: .1 }))
    await cycle(h.manager)
    h.setBoard(board({ asOf: 3, status: 'PARTIAL' }))
    await cycle(h.manager)
    expect(h.presses).toEqual([])
    expect(h.manager.state(PLATFORM).tickets).toEqual([])
  })
  it('accepts a PARTIAL board once the operator opts into one', async () => {
    const h = track(await armed({ limits: { ...defaultExecutionSettings().limits,
      acceptBoardStatus: ['READY', 'PARTIAL'] } }))
    h.setBoard(board({ status: 'PARTIAL' }))
    await cycle(h.manager)
    expect(h.presses).toHaveLength(1)
  })
  it('holds the cooldown and the hourly cap', async () => {
    const h = track(await armed({ limits: { ...defaultExecutionSettings().limits, cooldownMs: 600_000 } }))
    await cycle(h.manager)
    h.setBoard(board({ asOf: 1_700_000_005_000 }))
    await cycle(h.manager)
    expect(h.presses).toHaveLength(1)
    expect(h.manager.state(PLATFORM).blocked).toContain('COOLDOWN')

    const capped = track(await armed({ limits: { ...defaultExecutionSettings().limits,
      cooldownMs: 0, maxOrdersPerHour: 1 } }))
    await cycle(capped.manager)
    capped.setBoard(board({ asOf: 1_700_000_005_000 }))
    await cycle(capped.manager)
    expect(capped.presses).toHaveLength(1)
    expect(capped.manager.state(PLATFORM).blocked).toContain('HOURLY_CAP')
  })
  it('records a paper ticket without pressing', async () => {
    const h = track(await armed())
    await h.manager.command({ operation: 'settings', platform: PLATFORM,
      settings: { ...defaultExecutionSettings(), mode: 'PAPER' } })
    // Leaving AUTO disarms, so PAPER has to be armed on its own before it decides anything.
    expect(h.manager.state(PLATFORM).armed).toBe(false)
    await h.manager.command({ operation: 'arm', platform: PLATFORM })
    await cycle(h.manager)
    expect(h.presses).toEqual([])
    expect(h.manager.state(PLATFORM).tickets[0]!.state).toBe('PAPER')
  })
  it('disarms itself after a run of presses the broker never answered', async () => {
    const h = track(await armed({ limits: { ...defaultExecutionSettings().limits,
      cooldownMs: 0, maxUnverifiedInARow: 2 } }))
    h.setPress({ pressedAt: Date.now(), latencyMs: 9, verified: false, reasons: ['PANEL_UNCHANGED_0.4'] })
    await cycle(h.manager)
    h.setBoard(board({ asOf: 1_700_000_005_000 }))
    await cycle(h.manager)
    const state = h.manager.state(PLATFORM)
    expect(h.presses).toHaveLength(2)
    expect(state.armed).toBe(false)
    expect(state.tickets[0]!.state).toBe('UNVERIFIED')
    expect(state.blocked).toContain('UNVERIFIED_BREAKER')
  })
  it('stops on request whatever else is true', async () => {
    const h = track(await armed())
    h.surface.available = false
    const state = await h.manager.command({ operation: 'disarm', platform: PLATFORM })
    expect(state.armed).toBe(false)
    await cycle(h.manager)
    expect(h.presses).toEqual([])
  })
  it('blocks a hidden, calibrating or ungridded surface', async () => {
    const h = track(await armed())
    h.surface.gridReady = false
    expect(h.manager.state(PLATFORM).blocked).toContain('SURFACE_UNAVAILABLE')
    await cycle(h.manager)
    expect(h.presses).toEqual([])
  })
  it('invalidates the control map when the surface or the calibration moves', async () => {
    const h = track(await armed())
    h.surface.revision = 5
    expect(h.manager.state(PLATFORM).controlsValid).toBe(false)
    expect(h.manager.state(PLATFORM).blocked).toContain('CONTROL_MAP_STALE')
    h.surface.revision = 4
    const next = configuration()
    next.calibrations[0]!.updatedAt = new Date(Date.now() + 1000).toISOString()
    h.manager.configure(next)
    expect(h.manager.state(PLATFORM).controls).toBeNull()
  })
  it('swaps the mapped points when the operator corrects the color meaning', async () => {
    const h = track(await armed())
    const before = h.manager.state(PLATFORM).controls!.slots[2]!
    const after = await h.manager.command({ operation: 'directionForGreen', platform: PLATFORM, direction: 'LOWER' })
    expect(after.controls!.directionForGreen).toBe('LOWER')
    expect(after.controls!.slots[2]!.higher).toEqual(before.lower)
    expect(after.controls!.slots[2]!.lower).toEqual(before.higher)
  })
})

describe('operator control test', () => {
  it('presses every measured control once in each direction', async () => {
    const h = track(harness())
    await h.manager.command({ operation: 'calibrateControls', platform: PLATFORM })
    vi.useFakeTimers()
    try {
      await h.manager.command({ operation: 'testControls', platform: PLATFORM, confirm: 'PRESS ALL CONTROLS' })
      await vi.advanceTimersByTimeAsync(30_000)
    } finally { vi.useRealTimers() }
    expect(h.presses).toHaveLength(18)
    expect(h.presses.slice(0, 4)).toEqual([
      { canvasSlotId: 1, direction: 'HIGHER' }, { canvasSlotId: 1, direction: 'LOWER' },
      { canvasSlotId: 2, direction: 'HIGHER' }, { canvasSlotId: 2, direction: 'LOWER' }])
    const state = h.manager.state(PLATFORM)
    // Fifty tickets are kept; eighteen fit, and every one is labelled as a test rather than a signal.
    expect(state.tickets).toHaveLength(18)
    expect(state.tickets.every(ticket => ticket.reasons.includes('CONTROL_TEST'))).toBe(true)
    expect(state.ordersLastHour).toBe(18)
  })
  it('runs without arming, and does not need a board', async () => {
    const h = track(harness())
    await h.manager.command({ operation: 'calibrateControls', platform: PLATFORM })
    h.setBoard(null)
    expect(h.manager.state(PLATFORM).armed).toBe(false)
    vi.useFakeTimers()
    try {
      await h.manager.command({ operation: 'testControls', platform: PLATFORM, confirm: 'PRESS ALL CONTROLS' })
      await vi.advanceTimersByTimeAsync(30_000)
    } finally { vi.useRealTimers() }
    expect(h.presses).toHaveLength(18)
  })
  it('refuses an uncalibrated, stale or hidden surface', async () => {
    const h = track(harness())
    await expect(h.manager.command({ operation: 'testControls', platform: PLATFORM, confirm: 'PRESS ALL CONTROLS' }))
      .rejects.toThrow('CONTROLS_UNCALIBRATED')
    await h.manager.command({ operation: 'calibrateControls', platform: PLATFORM })
    h.surface.revision = 5
    await expect(h.manager.command({ operation: 'testControls', platform: PLATFORM, confirm: 'PRESS ALL CONTROLS' }))
      .rejects.toThrow('CONTROL_MAP_STALE')
    h.surface.revision = 4
    h.surface.gridReady = false
    await expect(h.manager.command({ operation: 'testControls', platform: PLATFORM, confirm: 'PRESS ALL CONTROLS' }))
      .rejects.toThrow('PRESS_UNAVAILABLE')
    expect(h.presses).toEqual([])
  })
  it('stops mid-run when the operator hits stop', async () => {
    const h = track(harness())
    await h.manager.command({ operation: 'calibrateControls', platform: PLATFORM })
    vi.useFakeTimers()
    try {
      await h.manager.command({ operation: 'testControls', platform: PLATFORM, confirm: 'PRESS ALL CONTROLS' })
      await vi.advanceTimersByTimeAsync(2_000)
      const pressedBeforeStop = h.presses.length
      await h.manager.command({ operation: 'disarm', platform: PLATFORM })
      await vi.advanceTimersByTimeAsync(30_000)
      expect(h.presses.length).toBeLessThan(18)
      expect(h.presses.length).toBeLessThanOrEqual(pressedBeforeStop + 1)
    } finally { vi.useRealTimers() }
  })
})
