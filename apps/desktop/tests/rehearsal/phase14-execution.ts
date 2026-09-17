import { defaultCalibration, defaultExecutionSettings, directionForVote,
  type ConfigurationResult, type ControlMap, type ExecutionSettings, type OpportunityBoard,
  type OrderTicket, type Platform } from '@quant-screen-trader/shared-types'
import { ExecutionManager } from '../../electron/main/execution-manager'
import type { OrderExecutor, PressResult } from '../../electron/main/order-executor'
import type { PlatformBrowserManager } from '../../electron/main/platform-browser'

/**
 * Phase 14 execution rehearsal. REHEARSAL ONLY — NOT REAL LIVE ACCEPTANCE.
 *
 * Drives the real ExecutionManager — the accepted, byte-for-byte protected decision code — over
 * what the engine would have served it, one poll per virtual second. Nothing here can reach a
 * broker: the browsers facade has no input path at all, and the executor stub refuses every
 * press and counts the attempt, so a PAPER run that ever tried to press is a failed run.
 *
 * Board-level refusals (BOARD_STATUS, NO_SELECTION, BELOW_LIMITS, SLOT_NOT_ALLOWED) leave no
 * trace inside the manager, so they are classified by an independent oracle written from the
 * documented gate order, and every board the manager evaluated is cross-checked against it.
 */

export const REHEARSAL_LABEL = 'REHEARSAL ONLY - NOT REAL LIVE ACCEPTANCE'
export const SOURCE_LABEL = 'SYNTHETIC_REHEARSAL'
const PLATFORMS: readonly Platform[] = ['capitalbear', 'iqoption']
const PROFILE_IDS: Record<Platform, string> = {
  capitalbear: '6d0c9f0e-4a0b-4f7e-9d61-0c3a1f7f5a11', iqoption: '8b1e2d3c-5f6a-4b7c-8d9e-0f1a2b3c4d22'
}
const ZOOM = .7
const SECOND = 1_000
const MINUTE = 60 * SECOND

export type TimelineRecord =
  | { t: number; type: 'header'; start: number; end: number; pollOffsetMs: number; scenario: string;
      label: string; source: string; captureStart: Record<Platform, number>; captureStop: number }
  | { t: number; type: 'process'; event: 'start' | 'stop'; segment: number }
  | { t: number; type: 'board'; platform: Platform; board?: OpportunityBoard }
  | { t: number; type: 'guard'; canOpenNewEntry: boolean; blockReason: string | null }
  | { t: number; type: 'surface'; platform: Platform; available: boolean; revision: number;
      captureRunning: boolean; slots: Record<string, string>; identityUncertain: number[] }
  | { t: number; type: 'engineBusy'; until: number }

interface Surface {
  available: boolean; revision: number; captureRunning: boolean
  slots: Record<string, string>; identityUncertain: number[]; assignedAt: Record<string, number>
}

/** Everything the manager can see: engine responses, the surface, tab identity and the clock. */
export class RehearsalWorld {
  now = 0
  busyUntil = -1
  guard: { canOpenNewEntry: boolean; blockReason: string | null } | null = null
  readonly boards = new Map<Platform, OpportunityBoard>()
  readonly surfaces = new Map<Platform, Surface>()
  readonly served = new Map<Platform, Set<number>>()
  readonly presses: { platform: Platform; canvasSlotId: number; direction: string; at: number }[] = []

  surface(platform: Platform): Surface {
    let value = this.surfaces.get(platform)
    if (!value) {
      value = { available: true, revision: 1, captureRunning: false, slots: {}, identityUncertain: [], assignedAt: {} }
      this.surfaces.set(platform, value)
    }
    return value
  }

  apply(record: TimelineRecord): void {
    switch (record.type) {
      case 'board':
        if (record.board) this.boards.set(record.platform, record.board)
        else this.boards.delete(record.platform)
        break
      case 'guard': this.guard = { canOpenNewEntry: record.canOpenNewEntry, blockReason: record.blockReason }; break
      case 'engineBusy': this.busyUntil = record.until; break
      case 'process':
        // A stopped engine serves nothing; the next process starts from its own first board.
        if (record.event === 'stop') { this.boards.clear(); this.guard = null; this.busyUntil = -1 }
        break
      case 'surface': {
        const current = this.surface(record.platform)
        for (const [slot, asset] of Object.entries(record.slots))
          if (current.slots[slot] !== asset) current.assignedAt[slot] = record.t
        Object.assign(current, { available: record.available, revision: record.revision,
          captureRunning: record.captureRunning, slots: record.slots, identityUncertain: record.identityUncertain })
        break
      }
      default: break
    }
  }

  /** The engine's local API, as the manager calls it. A held busy gate answers 429. */
  readonly fetch = async (input: URL | string): Promise<Response> => {
    const url = new URL(String(input))
    if (this.now < this.busyUntil) return new Response('busy', { status: 429 })
    if (url.pathname === '/api/session-guard/state') {
      return this.guard ? Response.json({ canOpenNewEntry: this.guard.canOpenNewEntry, blockReason: this.guard.blockReason })
        : new Response('unavailable', { status: 503 })
    }
    const match = /^\/api\/opportunities\/(capitalbear|iqoption)$/.exec(url.pathname)
    if (match) {
      const platform = match[1] as Platform, board = this.boards.get(platform)
      if (!board) return new Response('none', { status: 404 })
      let served = this.served.get(platform)
      if (!served) { served = new Set(); this.served.set(platform, served) }
      served.add(board.asOf)
      return Response.json({ featureVersion: board.featureVersion, regimeVersion: board.regimeVersion,
        strategyVersion: board.strategyVersion, rankingVersion: board.rankingVersion, board })
    }
    return new Response('not found', { status: 404 })
  }

  /** Read-only surface facts and tab identity. There is no press, input or navigation method. */
  readonly browsers = {
    observationSurface: (platform: Platform) => {
      const surface = this.surface(platform)
      return { available: surface.available, paused: false, gridReady: surface.available, revision: surface.revision,
        zoomFactor: ZOOM, bounds: { x: 0, y: 0, width: 1440, height: 760 } }
    },
    chartSlot: (platform: Platform, slotId: number, assetName: string): number => {
      const surface = this.surface(platform)
      if (surface.identityUncertain.includes(slotId) || surface.slots[String(slotId)] !== assetName)
        throw new Error('TAB: identity is uncertain; run Sync Assets')
      return slotId
    }
  }

  /** Measures a map on the current surface. Its press path refuses and records the attempt. */
  executor(confidence = .95, cells: number[] = [1, 2, 3, 4, 5, 6, 7, 8, 9], wouldPress = false): OrderExecutor {
    return {
      calibrate: (platform: Platform): Promise<ControlMap> => Promise.resolve({ platform, zoomFactor: ZOOM,
        surfaceRevision: this.surface(platform).revision, directionForGreen: 'HIGHER',
        measuredAt: new Date(this.now).toISOString(), reasons: [],
        slots: cells.map(slotId => ({ slotId, confidence, higher: { x: .3, y: .2 }, lower: { x: .3, y: .25 },
          panelBounds: { x: .3, y: .1, width: .05, height: .2 } })) }),
      press: (platform: Platform, _map: ControlMap, canvasSlotId: number, direction: string): Promise<PressResult> => {
        this.presses.push({ platform, canvasSlotId, direction, at: this.now })
        if (!wouldPress) return Promise.reject(new Error('REHEARSAL_PRESS_REFUSED: nothing may reach a broker'))
        // WOULD_PRESS: an inert stand-in that only reports when a press would have happened.
        return Promise.resolve({ pressedAt: this.now, latencyMs: 0, verified: true, reasons: ['WOULD_PRESS'] })
      }
    } as unknown as OrderExecutor
  }
}

export function configuration(platform: Platform, assets: Record<string, string> = {}): ConfigurationResult {
  const stamp = '2026-03-02T00:00:00.000Z'
  return {
    configuration: { platform, slots: Array.from({ length: 9 }, (_, index) => ({ id: index + 1, enabled: true,
      platform, assetName: assets[String(index + 1)] ?? `ASSET ${index + 1}` })) },
    calibrations: [{ id: PROFILE_IDS[platform], platform, name: 'Rehearsal grid', createdAt: stamp, updatedAt: stamp,
      referenceBrowserWidth: 1440, referenceBrowserHeight: 760, zoomFactor: ZOOM, slots: defaultCalibration(platform) }],
    presets: [], activeCalibrationId: PROFILE_IDS[platform]
  }
}

export function manager(world: RehearsalWorld, executor: OrderExecutor): ExecutionManager {
  const value = new ExecutionManager(world.browsers as unknown as PlatformBrowserManager, executor,
    { host: '127.0.0.1', port: 8765, healthUrl: 'http://127.0.0.1:8765/health' })
  // The rehearsal owns time. The manager's own one-second timer is stopped before anything is
  // configured, and every decision cycle is driven explicitly below.
  value.stop()
  return value
}

export async function tick(value: ExecutionManager): Promise<void> {
  await (value as unknown as { tick: () => Promise<void> }).tick()
}

/** The documented gate order, restated independently so each evaluated board can be checked. */
export function expectedRefusals(board: OpportunityBoard, settings: ExecutionSettings): string[] {
  const reasons = new Set<string>(), limits = settings.limits
  if (!limits.acceptBoardStatus.includes(board.status)) reasons.add('BOARD_STATUS')
  const direction = board.selectedDirection ? directionForVote(board.selectedDirection) : null
  if (board.selectedSlotId === null || !direction) reasons.add('NO_SELECTION')
  const candidate = board.candidates.find(entry => entry.slotId === board.selectedSlotId)
  if ((board.selectedScore ?? 0) < limits.minRankScore || (candidate?.ensembleConfidence ?? 0) < limits.minEnsembleConfidence)
    reasons.add('BELOW_LIMITS')
  if (board.selectedSlotId !== null && !settings.allowedSlots.includes(board.selectedSlotId)) reasons.add('SLOT_NOT_ALLOWED')
  return [...reasons]
}

const bump = (counts: Record<string, number>, key: string): void => { counts[key] = (counts[key] ?? 0) + 1 }

export interface PlatformTally {
  ticks: number; armedTicks: number; boardsSeen: number; boardsEvaluated: number
  boardRefusals: Record<string, number>; stateBlockedTicks: Record<string, number>
  tickets: Record<string, number>; paperTickets: number; paperNotSent: number
  directionMismatch: number; slotMismatch: number; assetMismatch: number; staleContextTickets: number
  duplicateTickets: number; oracleMismatches: number; controlMapRemeasures: number
}

function tally(): PlatformTally {
  return { ticks: 0, armedTicks: 0, boardsSeen: 0, boardsEvaluated: 0, boardRefusals: {}, stateBlockedTicks: {},
    tickets: {}, paperTickets: 0, paperNotSent: 0, directionMismatch: 0, slotMismatch: 0, assetMismatch: 0,
    staleContextTickets: 0, duplicateTickets: 0, oracleMismatches: 0, controlMapRemeasures: 0 }
}

export interface TimelineResult {
  label: string; source: string; scenario: string; start: number; end: number; segments: number
  platforms: Record<Platform, PlatformTally>; pressCalls: number; realBrokerPresses: number
}

/**
 * The main PAPER track: the operator sets PAPER, measures controls and arms once capture has
 * run for two minutes, re-measures a map a surface change made stale, and starts over after a
 * process restart — exactly the manual steps, driven on virtual time.
 */
export async function runTimeline(records: Iterable<TimelineRecord>, options: { setupDelayMs?: number } = {}): Promise<TimelineResult> {
  const setupDelay = options.setupDelayMs ?? 2 * MINUTE
  const iterator = records[Symbol.iterator]()
  const first = iterator.next()
  if (first.done || first.value.type !== 'header') throw new Error('Execution timeline must start with its header')
  const header = first.value
  const world = new RehearsalWorld()
  const executor = world.executor()
  const results: TimelineResult = { label: REHEARSAL_LABEL, source: header.source, scenario: header.scenario,
    start: header.start, end: header.end, segments: 0, platforms: { capitalbear: tally(), iqoption: tally() },
    pressCalls: 0, realBrokerPresses: 0 }
  const ticketed = new Set<string>()
  const staleSince = new Map<Platform, number>()
  let current: ExecutionManager | null = null
  let setupAt = new Map<Platform, number>()
  let pending = iterator.next()
  const original = globalThis.fetch
  globalThis.fetch = world.fetch as typeof fetch
  try {
    for (let now = header.start + header.pollOffsetMs; now <= header.end; now += SECOND) {
      world.now = now
      while (!pending.done && pending.value.t <= now) {
        const record = pending.value
        world.apply(record)
        if (record.type === 'process' && record.event === 'start') {
          current = manager(world, executor)
          results.segments++
          setupAt = new Map()
          for (const platform of PLATFORMS) current.configure(configuration(platform))
        } else if (record.type === 'process' && record.event === 'stop') {
          current?.stop()
          current = null
        } else if (record.type === 'surface' && current) {
          current.configure(configuration(record.platform, record.slots))
          if (record.captureRunning && !setupAt.has(record.platform)) setupAt.set(record.platform, record.t + setupDelay)
        }
        pending = iterator.next()
      }
      if (!current) continue
      const active: ExecutionManager = current
      for (const platform of PLATFORMS) {
        const due = setupAt.get(platform)
        if (due !== undefined && now >= due) {
          await active.command({ operation: 'settings', platform, settings: { ...defaultExecutionSettings(), mode: 'PAPER' } })
          await active.command({ operation: 'calibrateControls', platform })
          await active.command({ operation: 'arm', platform }).catch(() => undefined)
          // An operator whose Arm was refused tries again a minute later.
          setupAt.set(platform, active.state(platform).armed ? Number.POSITIVE_INFINITY : now + MINUTE)
        }
        const state = active.state(platform)
        if (state.armed && state.blocked.includes('CONTROL_MAP_STALE')) {
          const since = staleSince.get(platform) ?? now
          staleSince.set(platform, since)
          if (now - since >= MINUTE) {
            await active.command({ operation: 'calibrateControls', platform })
            results.platforms[platform].controlMapRemeasures++
            staleSince.delete(platform)
          }
        } else staleSince.delete(platform)
      }
      const before = new Map(PLATFORMS.map(platform => [platform, active.state(platform)]))
      const boards = new Map(PLATFORMS.map(platform => [platform, world.boards.get(platform)]))
      await tick(active)
      for (const platform of PLATFORMS) {
        const tallied = results.platforms[platform], previous = before.get(platform)!, after = active.state(platform)
        tallied.ticks++
        if (previous.armed) tallied.armedTicks++
        for (const code of previous.armed ? previous.blocked : []) bump(tallied.stateBlockedTicks, code)
        const board = boards.get(platform)
        if (after.lastBoardAsOf === previous.lastBoardAsOf || !board || board.asOf !== after.lastBoardAsOf) continue
        tallied.boardsEvaluated++
        const refusals = expectedRefusals(board, previous.settings)
        for (const code of refusals) bump(tallied.boardRefusals, code)
        const fresh = after.tickets.filter(ticket => !previous.tickets.some(old => old.id === ticket.id))
        if (refusals.length ? fresh.length !== 0 : fresh.length !== 1) tallied.oracleMismatches++
        for (const ticket of fresh) inspect(ticket, board, world, tallied, ticketed)
      }
    }
  } finally {
    globalThis.fetch = original
    current?.stop()
  }
  for (const platform of PLATFORMS) results.platforms[platform].boardsSeen = world.served.get(platform)?.size ?? 0
  results.pressCalls = world.presses.length
  return results
}

function inspect(ticket: OrderTicket, board: OpportunityBoard, world: RehearsalWorld, tallied: PlatformTally,
  ticketed: Set<string>): void {
  bump(tallied.tickets, `${ticket.state}:${ticket.reasons.join('+')}`)
  if (ticket.state === 'PAPER') {
    tallied.paperTickets++
    if (ticket.reasons.length === 1 && ticket.reasons[0] === 'NOT_SENT') tallied.paperNotSent++
  }
  const key = `${ticket.platform}:${ticket.boardAsOf}`
  if (ticketed.has(key)) tallied.duplicateTickets++
  ticketed.add(key)
  if (board.selectedDirection === null || ticket.direction !== directionForVote(board.selectedDirection)) tallied.directionMismatch++
  if (ticket.slotId !== board.selectedSlotId) tallied.slotMismatch++
  if (ticket.assetName !== board.selectedAssetName) tallied.assetMismatch++
  // A ticket may only name the asset the slot carried when the board's data existed.
  const surface = world.surface(ticket.platform)
  const assignedAt = surface.assignedAt[String(ticket.slotId)] ?? Number.POSITIVE_INFINITY
  if (ticket.state === 'PAPER' && (surface.slots[String(ticket.slotId)] !== ticket.assetName || board.asOf <= assignedAt))
    tallied.staleContextTickets++
}

/** A READY board naming one slot, for gate scenarios only. Never mixed into the timeline run. */
export function gateBoard(platform: Platform, asOf: number, changes: Partial<OpportunityBoard> = {},
  candidate: { slotId?: number; assetName?: string; confidence?: number; score?: number } = {}): OpportunityBoard {
  const slotId = candidate.slotId ?? 3, assetName = candidate.assetName ?? `ASSET ${slotId}`
  const score = candidate.score ?? .82
  return { platform, asOf, primaryTimeframe: platform === 'capitalbear' ? 'S5' : 'M1', featureVersion: 'qfe-v2',
    regimeVersion: 'qst-regime-v1', strategyVersion: 'qst-strategy-v1', rankingVersion: 'qst-ranking-v1',
    status: 'READY', expectedSlots: 9, receivedSlots: 9, rankedSlots: 9, excludedSlots: 0, missingSlots: [],
    candidates: [{ platform, slotId, assetName, asOf, direction: 'DOWN', primaryRegime: 'TREND_DOWN',
      ensembleConfidence: candidate.confidence ?? .8, rankScore: score, candidateStatus: 'ACTIONABLE', rank: 1,
      rankReasonCodes: ['SOLE_CANDIDATE'], exclusionReasons: [] }],
    selectedSlotId: slotId, selectedAssetName: assetName, selectedDirection: 'DOWN', selectedScore: score,
    runnerUpSlotId: null, leadMargin: null, watchlist: [], reasons: [], ...changes }
}

export interface ScenarioResult { name: string; passed: boolean; detail: Record<string, unknown> }

/**
 * Every refusal code the manager can produce, each isolated around the production defaults.
 * Cooldown and the hourly cap only exist after a press, and PAPER never presses, so those two use
 * the inert WOULD_PRESS stand-in; `setTime` moves the process clock those two limits read.
 */
export async function gateScenarios(setTime: (ms: number) => void): Promise<ScenarioResult[]> {
  const results: ScenarioResult[] = []
  const platform: Platform = 'capitalbear'
  const original = globalThis.fetch
  const start = Date.UTC(2026, 2, 2, 12)
  const setup = async (world: RehearsalWorld, executor: OrderExecutor, settings: Partial<ExecutionSettings> = {}) => {
    world.now = start
    setTime(start)
    world.guard = { canOpenNewEntry: true, blockReason: 'GUARD_DISABLED' }
    const surface = world.surface(platform)
    surface.slots = Object.fromEntries(Array.from({ length: 9 }, (_, i) => [String(i + 1), `ASSET ${i + 1}`]))
    const value = manager(world, executor)
    value.configure(configuration(platform, surface.slots))
    await value.command({ operation: 'settings', platform, settings: { ...defaultExecutionSettings(), mode: 'PAPER', ...settings } })
    await value.command({ operation: 'calibrateControls', platform })
    await value.command({ operation: 'arm', platform })
    return value
  }
  const record = (name: string, passed: boolean, detail: Record<string, unknown>) => { results.push({ name, passed, detail }) }
  try {
    {
      const world = new RehearsalWorld(); globalThis.fetch = world.fetch as typeof fetch
      const value = await setup(world, world.executor())
      world.boards.set(platform, gateBoard(platform, start - 5_000))
      await tick(value); await tick(value)
      world.boards.set(platform, gateBoard(platform, start - 10_000))
      await tick(value)
      const tickets = value.state(platform).tickets
      record('all gates pass: one PAPER ticket, NOT_SENT, no press', tickets.length === 1 && tickets[0]!.state === 'PAPER' &&
        tickets[0]!.reasons.join() === 'NOT_SENT' && tickets[0]!.direction === 'LOWER' && tickets[0]!.slotId === 3 &&
        world.presses.length === 0, { tickets: tickets.map(t => [t.state, t.reasons, t.direction, t.slotId]), presses: world.presses.length })
      record('lastBoardAsOf deduplication: same and older boards add nothing', tickets.length === 1,
        { lastBoardAsOf: value.state(platform).lastBoardAsOf })
    }
    const refusal = async (name: string, board: OpportunityBoard, settings: Partial<ExecutionSettings> = {}) => {
      const world = new RehearsalWorld(); globalThis.fetch = world.fetch as typeof fetch
      const value = await setup(world, world.executor(), settings)
      world.boards.set(platform, board)
      await tick(value)
      const state = value.state(platform)
      record(name, state.lastBoardAsOf === board.asOf && state.tickets.length === 0 && world.presses.length === 0 &&
        expectedRefusals(board, state.settings).length > 0,
      { refusals: expectedRefusals(board, state.settings), tickets: state.tickets.length })
    }
    await refusal('BOARD_STATUS: PARTIAL refused under the READY-only default', gateBoard(platform, start, { status: 'PARTIAL' }))
    await refusal('NO_SELECTION: a board naming no leader', gateBoard(platform, start, { status: 'NO_OPPORTUNITY',
      selectedSlotId: null, selectedAssetName: null, selectedDirection: null, selectedScore: null, candidates: [] }))
    await refusal('BELOW_LIMITS: score under the default 0.6', gateBoard(platform, start, {}, { score: .31 }))
    await refusal('BELOW_LIMITS: confidence under the default 0.55', gateBoard(platform, start, {}, { confidence: .2 }))
    await refusal('SLOT_NOT_ALLOWED: selected slot excluded', gateBoard(platform, start), { allowedSlots: [1, 2, 4, 5, 6, 7, 8, 9] })
    const blockedTicket = async (name: string, reason: string, executor: (world: RehearsalWorld) => OrderExecutor,
      prepare: (world: RehearsalWorld) => void = () => undefined) => {
      const world = new RehearsalWorld(); globalThis.fetch = world.fetch as typeof fetch
      const value = await setup(world, executor(world))
      prepare(world)
      world.boards.set(platform, gateBoard(platform, start))
      await tick(value)
      const tickets = value.state(platform).tickets
      record(name, tickets.length === 1 && tickets[0]!.state === 'BLOCKED' && tickets[0]!.reasons.join() === reason &&
        world.presses.length === 0, { tickets: tickets.map(t => [t.state, t.reasons]) })
    }
    await blockedTicket('TAB_IDENTITY_UNCERTAIN: unreadable tab blocks the ticket', 'TAB_IDENTITY_UNCERTAIN',
      world => world.executor(), world => { world.surface(platform).identityUncertain = [3] })
    await blockedTicket('TAB_IDENTITY_UNCERTAIN: slot now shows another asset', 'TAB_IDENTITY_UNCERTAIN',
      world => world.executor(), world => { world.surface(platform).slots['3'] = 'OTHER ASSET' })
    await blockedTicket('CONTROL_CONFIDENCE_LOW: map below the default 0.8', 'CONTROL_CONFIDENCE_LOW', world => world.executor(.5))
    await blockedTicket('CELL_NOT_MEASURED: map without the selected cell', 'CELL_NOT_MEASURED',
      world => world.executor(.95, [1, 2, 4, 5, 6, 7, 8, 9]))
    const stateBlock = async (name: string, code: string, prepare: (world: RehearsalWorld) => void) => {
      const world = new RehearsalWorld(); globalThis.fetch = world.fetch as typeof fetch
      const value = await setup(world, world.executor())
      prepare(world)
      world.boards.set(platform, gateBoard(platform, start))
      await tick(value)
      const state = value.state(platform)
      record(name, state.blocked.includes(code) && state.lastBoardAsOf === null && state.tickets.length === 0 &&
        !world.served.get(platform)?.size, { blocked: state.blocked })
      return { world, value }
    }
    await stateBlock('CONTROL_MAP_STALE: surface revision moved', 'CONTROL_MAP_STALE', world => { world.surface(platform).revision++ })
    await stateBlock('SURFACE_UNAVAILABLE: broker grid hidden', 'SURFACE_UNAVAILABLE', world => { world.surface(platform).available = false })
    {
      const { world, value } = await stateBlock('SESSION_MANUAL_STOP: the day was ended', 'SESSION_MANUAL_STOP',
        world => { world.guard = { canOpenNewEntry: false, blockReason: 'MANUAL_STOP' } })
      world.guard = null
      await tick(value)
      const sticky = value.state(platform).blocked.includes('SESSION_MANUAL_STOP')
      world.guard = { canOpenNewEntry: true, blockReason: 'GUARD_DISABLED' }
      await tick(value)
      record('SESSION_* is sticky while the guard is unreachable, lifts only on a positive answer',
        sticky && !value.state(platform).blocked.some(code => code.startsWith('SESSION_')), { sticky })
    }
    {
      const world = new RehearsalWorld(); globalThis.fetch = world.fetch as typeof fetch
      const value = await setup(world, world.executor(.95, undefined, true))
      await value.command({ operation: 'settings', platform, settings: { ...defaultExecutionSettings(), mode: 'AUTO' } })
      await value.command({ operation: 'arm', platform })
      const at = (ms: number) => { world.now = start + ms; setTime(start + ms) }
      const offer = async (ms: number) => { at(ms); world.boards.set(platform, gateBoard(platform, start + ms)); await tick(value) }
      await offer(0)
      await offer(30 * SECOND)
      const cooling = value.state(platform).blocked.includes('COOLDOWN')
      const afterCooldownRefusal = world.presses.length
      await offer(61 * SECOND)
      record('COOLDOWN: default 60 s between would-presses', cooling && afterCooldownRefusal === 1 && world.presses.length === 2,
        { wouldPresses: world.presses.length })
      for (let i = 2; i < 12; i++) await offer((61 + (i - 1) * 61) * SECOND)
      const pressedBeforeCap = world.presses.length
      await offer(13 * 61 * SECOND)
      const capped = value.state(platform).blocked.includes('HOURLY_CAP')
      at(3_600_000 + 62 * SECOND)
      const released = !value.state(platform).blocked.includes('HOURLY_CAP')
      record('HOURLY_CAP: default 12 would-presses per rolling hour', pressedBeforeCap === 12 && capped &&
        world.presses.length === 12 && released, { wouldPresses: world.presses.length, capped, released })
      value.stop()
    }
  } finally {
    globalThis.fetch = original
  }
  return results
}

export function timelineChecks(result: TimelineResult): ScenarioResult[] {
  const totals = PLATFORMS.map(platform => result.platforms[platform])
  const sum = (pick: (tally: PlatformTally) => number) => totals.reduce((total, tallied) => total + pick(tallied), 0)
  const checks: ScenarioResult[] = []
  const check = (name: string, passed: boolean, detail: Record<string, unknown> = {}) => { checks.push({ name, passed, detail }) }
  check('PAPER track never invoked the press path', result.pressCalls === 0, { pressCalls: result.pressCalls })
  check('boards evaluated on both platforms', totals.every(tallied => tallied.boardsEvaluated > 0),
    Object.fromEntries(PLATFORMS.map(platform => [platform, result.platforms[platform].boardsEvaluated])))
  check('every evaluated board matched the independent gate oracle', sum(t => t.oracleMismatches) === 0,
    { oracleMismatches: sum(t => t.oracleMismatches) })
  check('direction mismatch = 0', sum(t => t.directionMismatch) === 0)
  check('slot mismatch = 0', sum(t => t.slotMismatch) === 0)
  check('asset mismatch = 0', sum(t => t.assetMismatch) === 0)
  check('stale-context tickets = 0', sum(t => t.staleContextTickets) === 0)
  check('duplicate execution tickets = 0 (one boardAsOf, at most one ticket)', sum(t => t.duplicateTickets) === 0)
  check('every PAPER ticket is NOT_SENT', sum(t => t.paperTickets) === sum(t => t.paperNotSent))
  check('process restart re-created the manager', result.segments >= 2, { segments: result.segments })
  return checks
}
