import { randomUUID } from 'node:crypto'
import type { Platform } from '@quant-screen-trader/shared-types'
import type { EngineConnectionConfig } from './engine-config'
import type { ExecutionManager } from './execution-manager'
import type { AssetSyncManager } from './asset-sync'
import type { MarketManager } from './market-manager'

interface ObserverState {
  signature: string; revision: number; presses: number; lastPress: string
  lastBoardAsOf: number | null; boardsEvaluated: number; ticketIds: Set<string>; paperTickets: number; blockedTickets: number
}

const blankObserver = (): ObserverState => ({ signature: '', revision: 0, presses: 0, lastPress: '', lastBoardAsOf: null,
  boardsEvaluated: 0, ticketIds: new Set(), paperTickets: 0, blockedTickets: 0 })

/**
 * Read-only observer: never issues market, policy, browser or execution commands.
 *
 * `armed` means a LIVE (AUTO) executor is armed — the state that can press a broker control.
 * An executor armed in PAPER runs every gate and records would-press tickets without pressing,
 * so it is reported separately (`paperArmed`, `paperTickets`, `recentTickets`) and never as armed.
 */
export class ShadowLiveTelemetry {
  private readonly instanceId = randomUUID()
  private readonly timer: ReturnType<typeof setInterval>
  private readonly states = new Map<Platform, ObserverState>()
  private busy = false
  private previous = performance.now()
  constructor(private readonly market: MarketManager, private readonly execution: ExecutionManager,
    private readonly connection: EngineConnectionConfig,
    private readonly assetSync?: () => AssetSyncManager | null) {
    this.timer = setInterval(() => { void this.tick() }, 1000)
  }
  private async tick(): Promise<void> {
    const now = performance.now(), delay = Math.max(0, now - this.previous - 1000)
    this.previous = now
    if (this.busy) return // No telemetry backlog or retries of stale state.
    this.busy = true
    try {
      for (const value of this.market.operationalState()) {
        const execution = this.execution.state(value.platform)
        const state = this.states.get(value.platform) ?? blankObserver()
        for (const ticket of [...execution.tickets].sort((a, b) => (a.pressedAt ?? '').localeCompare(b.pressedAt ?? ''))) {
          if (ticket.pressedAt && ticket.pressedAt > state.lastPress) {
            state.presses++; state.lastPress = ticket.pressedAt
          }
        }
        const mode = execution.settings.mode
        if (execution.lastBoardAsOf !== null && execution.lastBoardAsOf !== state.lastBoardAsOf) {
          state.lastBoardAsOf = execution.lastBoardAsOf; state.boardsEvaluated++ // sampled once a second
        }
        const recentTickets = []
        for (const ticket of execution.tickets) { // newest first, at most fifty
          if (ticket.state !== 'PAPER' && ticket.state !== 'BLOCKED') continue
          if (!state.ticketIds.has(ticket.id)) {
            state.ticketIds.add(ticket.id)
            if (ticket.state === 'PAPER') state.paperTickets++
            else state.blockedTickets++
          }
          if (recentTickets.length < 5) recentTickets.push({ id: ticket.id, boardAsOf: ticket.boardAsOf,
            slotId: ticket.slotId, assetName: ticket.assetName, direction: ticket.direction, state: ticket.state,
            reasons: ticket.reasons.map(reason => reason.slice(0, 64)), requestedAt: ticket.requestedAt })
        }
        const listed = new Set(execution.tickets.map(ticket => ticket.id))
        for (const id of state.ticketIds) if (!listed.has(id)) state.ticketIds.delete(id)
        const signature = JSON.stringify([value.captureRunning, value.surfaceAvailable,
          value.engineAvailable, execution.armed, mode, value.slots.map(s => [s.slotId, s.enabled, s.assetName, s.contextId, s.captureEligible])])
        if (signature !== state.signature) { state.signature = signature; state.revision++ }
        this.states.set(value.platform, state)
        const response = await fetch(new URL('/api/shadow-live/telemetry', this.connection.healthUrl), {
          method: 'POST', headers: { 'content-type': 'application/json' }, redirect: 'error',
          signal: AbortSignal.timeout(1000), body: JSON.stringify({ ...value, instanceId: this.instanceId,
            ...this.assetSync?.()?.operationalState(value.platform), mainRssBytes: process.memoryUsage().rss,
            healthRevision: state.revision, armed: execution.armed && mode === 'AUTO', brokerPresses: state.presses,
            executionMode: mode, paperArmed: execution.armed && mode === 'PAPER', boardsEvaluated: state.boardsEvaluated,
            paperTickets: state.paperTickets, blockedTickets: state.blockedTickets, recentTickets,
            mainLoopDelayMs: delay })
        })
        if (!response.ok) console.warn('[phase14] Telemetry unavailable')
      }
    } catch { /* Missing telemetry is an evidence gap, never a trading input. */ }
    finally { this.busy = false }
  }
  stop(): void { clearInterval(this.timer) }
}
