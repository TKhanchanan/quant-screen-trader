import { Notification } from 'electron'
import { SessionNotificationSchema, type SessionNotification } from '@quant-screen-trader/shared-types'
import type { EngineConnectionConfig } from './engine-config'

/**
 * Watches the Phase 9.5 daily session for two things the engine cannot do for itself: telling
 * the operator, and closing the application.
 *
 * Python states that something notification-worthy happened and that the conditions for a close
 * are satisfied. It never reaches an operating system notification centre and never terminates
 * anything — Electron owns the application's lifetime, and a risk layer that could kill the
 * process directly would be able to take the capture pipeline down with it mid-measurement.
 *
 * A close is graceful by construction. This asks the application to quit through the same path a
 * person closing the window takes, so observation stops cleanly, state is flushed and the local
 * engine is shut down in order. Nothing is ever forced.
 */
export class SessionWatcher {
  private readonly timer: ReturnType<typeof setInterval>
  /**
   * Notifications already accounted for. Seeded on the first successful poll rather than
   * starting empty: a target reached an hour ago has already been announced, and re-announcing
   * it every time the application opens would train the operator to ignore it.
   */
  private seen = new Set<string>()
  private seeded = false
  private closing = false
  constructor(private readonly connection: EngineConnectionConfig,
    private readonly quit: () => void, intervalMs = 5000) {
    this.timer = setInterval(() => { void this.poll() }, intervalMs)
  }

  private async poll(): Promise<void> {
    if (this.closing) return
    let state: { notifications?: unknown; shutdownRequested?: unknown }
    try {
      const response = await fetch(new URL('/api/session-guard/state', this.connection.healthUrl),
        { signal: AbortSignal.timeout(2000), redirect: 'error' })
      // 429 means the engine is mid-ingest. Neither an outage nor a busy moment is a reason to
      // announce anything or to close: the next poll asks again.
      if (!response.ok) return
      state = await response.json() as { notifications?: unknown; shutdownRequested?: unknown }
    } catch { return }

    const parsed = SessionNotificationSchema.array().safeParse(state.notifications ?? [])
    const notifications: SessionNotification[] = parsed.success ? parsed.data : []
    if (!this.seeded) {
      for (const item of notifications) this.seen.add(item.eventId)
      this.seeded = true
    } else {
      for (const item of notifications) {
        if (this.seen.has(item.eventId)) continue
        this.seen.add(item.eventId)
        this.show(item)
      }
    }
    if (state.shutdownRequested === true) {
      this.closing = true
      this.quit()
    }
  }

  private show(item: SessionNotification): void {
    if (!Notification.isSupported()) return
    new Notification({ title: item.title, body: item.message }).show()
  }

  stop(): void { clearInterval(this.timer) }
}
