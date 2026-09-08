import { randomUUID } from 'node:crypto'
import { MarketBatchResultSchema, normalizedToPixel, type ConfigurationResult, type MarketCommand, type MarketObservation, type MarketSnapshot, type Platform } from '@quant-screen-trader/shared-types'
import { DOMMarketDataProvider, VisualMarketDataProvider, type ObservationContext } from './market-providers'
import { TesseractOCRProvider } from './market-ocr'
import { CaptureScheduler } from './market-scheduler'
import type { PlatformBrowserManager } from './platform-browser'
import type { EngineConnectionConfig } from './engine-config'

interface WorkspaceData {
  snapshot: MarketSnapshot; config: ConfigurationResult; contextId: string; scheduler: CaptureScheduler;
  dom: DOMMarketDataProvider; visual: VisualMarketDataProvider; ocr: TesseractOCRProvider;
  signature: string; cursor: number; busy: boolean; count: number; started: number
}
export class MarketManager {
  private readonly workspaces = new Map<Platform, WorkspaceData>()
  private readonly queue = new Map<string, MarketObservation>()
  private sending = false
  private readonly timer: ReturnType<typeof setInterval>
  private readonly flushTimer: ReturnType<typeof setInterval>
  constructor(private readonly browsers: PlatformBrowserManager, private readonly connection: EngineConnectionConfig) {
    this.timer = setInterval(() => this.tick(), 50)
    this.flushTimer = setInterval(() => { void this.flush() }, 250)
  }
  configure(config: ConfigurationResult): void {
    const platform = config.configuration.platform, previous = this.workspaces.get(platform)
    if (previous && JSON.stringify(previous.config) === JSON.stringify(config)) return
    if (previous) { previous.scheduler.invalidate(); previous.dom.stop(); previous.visual.stop(); void previous.ocr.stop().catch(() => {}) }
    for (const key of this.queue.keys()) if (key.startsWith(platform + ':')) this.queue.delete(key)
    const ocr = new TesseractOCRProvider()
    const dom = new DOMMarketDataProvider(c => this.browsers.readSlotDOM(c))
    const visual = new VisualMarketDataProvider(c => this.browsers.captureSlot(c), ocr)
    dom.start(); visual.start()
    const next: WorkspaceData = { config, ocr, dom, visual, contextId: randomUUID(), scheduler: previous?.scheduler ?? new CaptureScheduler(),
      signature: '', cursor: 0, count: 0, started: Date.now(), busy: previous?.busy ?? false, snapshot: { running: previous?.snapshot.running ?? false,
        intervalMs: previous?.snapshot.intervalMs ?? (platform === 'capitalbear' ? 500 : 1000),
        slots: config.configuration.slots.map(s => ({ slotId: s.id, state: s.enabled ? 'WAITING' : 'DISABLED', secondSamples: 0, m1Samples: 0, m1State: null, observation: null, dropped: 0, pixelBounds: null })),
        dropped: 0, queueDepth: 0, queueLagMs: 0, captureRate: 0, engineAvailable: false } }
    if (previous) Object.assign(previous, next)
    this.workspaces.set(platform, previous ?? next)
  }
  command(command: MarketCommand): MarketSnapshot {
    const workspace = this.workspaces.get(command.platform)
    if (!workspace) throw new Error('Load configuration first')
    if (command.intervalMs !== undefined) workspace.snapshot.intervalMs = command.intervalMs
    if (command.operation !== 'state') {
      workspace.snapshot.running = command.operation === 'start'
      workspace.count = 0; workspace.started = Date.now()
      workspace.snapshot.engineAvailable = false; workspace.snapshot.queueLagMs = 0
      workspace.scheduler.invalidate(); workspace.contextId = randomUUID()
      for (const key of this.queue.keys()) if (key.startsWith(command.platform + ':')) this.queue.delete(key)
      for (const slot of workspace.snapshot.slots) { slot.observation = null; slot.secondSamples = 0; slot.m1Samples = 0; slot.m1State = null; if (slot.state !== 'DISABLED') slot.state = workspace.snapshot.running ? 'WAITING' : 'PAUSED' }
    }
    workspace.snapshot.queueDepth = this.queue.size
    workspace.snapshot.captureRate = workspace.snapshot.running ? workspace.count / Math.max(1, (Date.now() - workspace.started) / 1000) : 0
    return workspace.snapshot
  }
  private tick(): void {
    for (const [platform, w] of this.workspaces) {
      const surface = this.browsers.observationSurface(platform)
      const signature = JSON.stringify(surface)
      if (w.signature !== signature) {
        w.count = 0; w.started = Date.now()
        w.signature = signature; w.contextId = randomUUID(); w.scheduler.invalidate()
        for (const key of this.queue.keys()) if (key.startsWith(platform + ':')) this.queue.delete(key)
        for (const slot of w.snapshot.slots) { slot.observation = null; slot.secondSamples = 0; slot.m1Samples = 0; slot.m1State = null }
      }
      const profile = w.config.calibrations.find(p => p.id === w.config.activeCalibrationId)
      for (const slot of w.snapshot.slots) {
        const configured = w.config.configuration.slots.find(s => s.id === slot.slotId)!
        if (!configured.enabled) { slot.state = 'DISABLED'; continue }
        if (!w.snapshot.running || surface.paused) { slot.state = 'PAUSED'; continue }
        if (!surface.available || !profile) { slot.state = 'WAITING'; continue }
        if (slot.observation && Date.now() - Date.parse(slot.observation.observedAt) > 3000) slot.state = 'STALE'
      }
      if (!w.snapshot.running || !surface.available || surface.paused || !profile || w.busy) continue
      const enabled = w.config.configuration.slots.filter(s => s.enabled)
      if (!enabled.length) continue
      const configured = enabled[w.cursor++ % enabled.length]!
      const bounds = profile.slots.find(s => s.id === configured.id)!.bounds
      const slot = w.snapshot.slots.find(s => s.slotId === configured.id)!
      slot.pixelBounds = normalizedToPixel(bounds, surface.bounds.width, surface.bounds.height)
      const context: ObservationContext = { platform, slotId: configured.id, assetName: configured.assetName,
        contextId: w.contextId, calibrationProfileId: profile.id, bounds }
      const key = `${platform}:${configured.id}`
      w.busy = true
      void w.scheduler.run(key, true, w.snapshot.intervalMs, async () => {
        slot.state = 'CAPTURING'
        const dom = await w.dom.observe(context)
        if (dom.dataQuality.state === 'GOOD') return dom
        slot.state = 'PARSING'
        return w.visual.observe(context)
      }, value => {
        if (JSON.stringify(this.browsers.observationSurface(platform)) !== signature) return
        slot.observation = value
        slot.state = value.dataQuality.state === 'GOOD' ? 'READY' : value.dataQuality.state === 'STALE' ? 'STALE' : 'DATA_UNCERTAIN'
        w.count++
        if (this.queue.has(key)) { w.snapshot.dropped++; slot.dropped++ }
        this.queue.set(key, value)
      }, () => { slot.state = 'ERROR' }).finally(() => { w.busy = false })
    }
  }
  private async flush(): Promise<void> {
    if (this.sending || !this.queue.size) return
    this.sending = true
    const batch = [...this.queue.values()]
    this.queue.clear()
    const lag = Math.max(...batch.map(o => Date.now() - Date.parse(o.observedAt)))
    try {
      const response = await fetch(new URL('/api/market/observations', this.connection.healthUrl), {
        method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ observations: batch }),
        signal: AbortSignal.timeout(2000), redirect: 'error' })
      if (!response.ok) throw new Error('Engine unavailable')
      const result = MarketBatchResultSchema.parse(await response.json())
      for (const status of result.slots) {
        const w = this.workspaces.get(status.platform)
        const slot = w?.snapshot.slots.find(s => s.slotId === status.slotId)
        if (slot && w?.contextId === status.contextId) {
          slot.secondSamples = status.secondSamples; slot.m1Samples = status.m1Samples; slot.m1State = status.m1State
        }
      }
      for (const w of this.workspaces.values()) { w.snapshot.engineAvailable = true; w.snapshot.queueLagMs = lag }
    } catch {
      for (const o of batch) {
        const w = this.workspaces.get(o.platform)
        if (w) { w.snapshot.engineAvailable = false; w.snapshot.dropped++; w.snapshot.queueLagMs = lag }
      }
    } finally { this.sending = false }
  }
  stop(): void {
    clearInterval(this.timer); clearInterval(this.flushTimer)
    for (const w of this.workspaces.values()) { w.scheduler.invalidate(); w.dom.stop(); w.visual.stop(); void w.ocr.stop().catch(() => {}) }
    this.queue.clear()
  }
}
