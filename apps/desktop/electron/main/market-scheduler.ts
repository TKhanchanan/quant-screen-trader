import type { MarketObservation } from '@quant-screen-trader/shared-types'

/** One active job per key, no pending job queue. Invalidating never releases the busy guard. */
export class CaptureScheduler {
  private readonly busy = new Set<string>()
  private readonly next = new Map<string, number>()
  private generation = 0
  dropped = 0
  invalidate(): void { this.generation++; this.next.clear() }
  async run(key: string, enabled: boolean, interval: number, job: () => Promise<MarketObservation>,
    accept: (value: MarketObservation) => void, fail: () => void, now = Date.now()): Promise<void> {
    if (!enabled || now < (this.next.get(key) ?? 0)) return
    if (this.busy.has(key)) { this.dropped++; return }
    this.busy.add(key); this.next.set(key, now + interval)
    const generation = this.generation
    try { const value = await job(); if (generation === this.generation) accept(value) }
    catch { if (generation === this.generation) fail() }
    finally { this.busy.delete(key) }
  }
}
