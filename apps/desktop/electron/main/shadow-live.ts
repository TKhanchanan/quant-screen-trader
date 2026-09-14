import { randomUUID } from 'node:crypto'
import type { Platform } from '@quant-screen-trader/shared-types'
import type { EngineConnectionConfig } from './engine-config'
import type { ExecutionManager } from './execution-manager'
import type { MarketManager } from './market-manager'

/** Read-only observer: never issues market, policy, browser or execution commands. */
export class ShadowLiveTelemetry {
  private readonly instanceId = randomUUID()
  private readonly timer: ReturnType<typeof setInterval>
  private readonly states = new Map<Platform, { signature: string; revision: number; presses: number; lastPress: string }>()
  private busy = false
  private previous = performance.now()
  constructor(private readonly market: MarketManager, private readonly execution: ExecutionManager,
    private readonly connection: EngineConnectionConfig) {
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
        const state = this.states.get(value.platform) ?? { signature: '', revision: 0, presses: 0, lastPress: '' }
        for (const ticket of [...execution.tickets].sort((a, b) => (a.pressedAt ?? '').localeCompare(b.pressedAt ?? ''))) {
          if (ticket.pressedAt && ticket.pressedAt > state.lastPress) {
            state.presses++; state.lastPress = ticket.pressedAt
          }
        }
        const signature = JSON.stringify([value.captureRunning, value.surfaceAvailable,
          value.engineAvailable, execution.armed, value.slots.map(s => [s.slotId, s.enabled, s.assetName, s.contextId, s.state])])
        if (signature !== state.signature) { state.signature = signature; state.revision++ }
        this.states.set(value.platform, state)
        const response = await fetch(new URL('/api/shadow-live/telemetry', this.connection.healthUrl), {
          method: 'POST', headers: { 'content-type': 'application/json' }, redirect: 'error',
          signal: AbortSignal.timeout(1000), body: JSON.stringify({ ...value, instanceId: this.instanceId,
            healthRevision: state.revision, armed: execution.armed, brokerPresses: state.presses,
            mainLoopDelayMs: delay })
        })
        if (!response.ok) console.warn('[phase14] Telemetry unavailable')
      }
    } catch { /* Missing telemetry is an evidence gap, never a trading input. */ }
    finally { this.busy = false }
  }
  stop(): void { clearInterval(this.timer) }
}
