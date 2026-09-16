import { randomUUID } from 'node:crypto'
import { calibrationToChartGrid, calibrationZoomMatches, MarketBatchResultSchema, normalizedToPixel, type ConfigurationResult, type MarketCommand, type MarketObservation, type MarketSnapshot, type Platform } from '@quant-screen-trader/shared-types'
import { canvasPriceGeometry } from './chart-grid'
import { VisualMarketDataProvider, type ObservationContext } from './market-providers'
import { TesseractOCRProvider } from './market-ocr'
import { CaptureScheduler } from './market-scheduler'
import type { PlatformBrowserManager } from './platform-browser'
import type { EngineConnectionConfig } from './engine-config'

interface WorkspaceData {
  snapshot: MarketSnapshot; config: ConfigurationResult; contextIds: Map<number, string>; scheduler: CaptureScheduler;
  readers: { visual: VisualMarketDataProvider; ocr: TesseractOCRProvider }[];
  signature: string; activeBatch: Promise<void> | null; nextBatchAt: number; count: number; started: number;
  resetting: Set<number>; resetRevision: number
}
export class MarketManager {
  private readonly workspaces = new Map<Platform, WorkspaceData>()
  private readonly queue = new Map<string, MarketObservation>()
  private sending = false
  private readonly operational = new Map<string, { observations: number; dataUncertain: number; lastCaptureAttemptAt: number | null }>()
  private readonly transport = { droppedBatches: 0, http429s: 0 }
  operationalState() {
    return [...this.workspaces].map(([platform, w]) => ({
      platform, captureRunning: w.snapshot.running,
      surfaceAvailable: this.browsers.observationSurface(platform).available && !this.browsers.observationSurface(platform).paused,
      captureRate: w.snapshot.running ? w.count / Math.max(1, (Date.now() - w.started) / 1000) : 0,
      engineAvailable: w.snapshot.engineAvailable, intervalMs: w.snapshot.intervalMs,
      queueDepth: this.queue.size, ...this.transport,
      slots: w.config.configuration.slots.map(s => ({ slotId: s.id, enabled: s.enabled,
        assetName: s.assetName, contextId: w.contextIds.get(s.id)!,
        state: w.snapshot.slots.find(slot => slot.slotId === s.id)!.state,
        captureEligible: !['TAB', 'MAPPING', 'CANVAS BOUNDS'].includes(w.snapshot.slots.find(slot => slot.slotId === s.id)!.diagnostics?.stage ?? 'TAB'),
        ...(this.operational.get(`${platform}:${s.id}`) ?? { observations: 0, dataUncertain: 0, lastCaptureAttemptAt: null }),
        ...Object.fromEntries(['lastAttemptAt', 'lastParsedPriceAt', 'lastGoodPriceAt', 'attemptCount', 'parsedCount', 'goodCount', 'uncertainCount']
          .map(key => [key, w.snapshot.slots.find(slot => slot.slotId === s.id)![key as keyof MarketSnapshot['slots'][number]] ?? (key.endsWith('Count') ? 0 : null)])),
        secondSamples: w.snapshot.slots.find(slot => slot.slotId === s.id)!.secondSamples,
        s5Samples: w.snapshot.slots.find(slot => slot.slotId === s.id)!.s5Samples ?? 0,
        m1Samples: w.snapshot.slots.find(slot => slot.slotId === s.id)!.m1Samples,
        dropped: w.snapshot.slots.find(slot => slot.slotId === s.id)!.dropped }))
    }))
  }
  private captureMetric(platform: Platform, slotId: number, uncertain: boolean, observation: boolean): void {
    const key = `${platform}:${slotId}`
    const counts = this.operational.get(key) ?? { observations: 0, dataUncertain: 0, lastCaptureAttemptAt: null }
    counts.observations += Number(observation); counts.dataUncertain += Number(uncertain); counts.lastCaptureAttemptAt = Date.now()
    this.operational.set(key, counts)
  }
  private readonly timer: ReturnType<typeof setInterval>
  private readonly flushTimer: ReturnType<typeof setInterval>
  constructor(private readonly browsers: PlatformBrowserManager, private readonly connection: EngineConnectionConfig) {
    this.timer = setInterval(() => this.tick(), 50)
    this.flushTimer = setInterval(() => { void this.flush() }, 250)
  }
  configure(config: ConfigurationResult): void {
    const platform = config.configuration.platform, previous = this.workspaces.get(platform)
    if (previous && JSON.stringify(previous.config) === JSON.stringify(config)) return
    if (previous) { previous.scheduler.invalidate(); for (const reader of previous.readers) { reader.visual.stop(); void reader.ocr.stop().catch(() => {}) } }
    const profile = (value: ConfigurationResult): string => JSON.stringify(value.calibrations.find(p => p.id === value.activeCalibrationId) ?? null)
    const resetAll = !previous || profile(previous.config) !== profile(config)
    const changed = new Set(config.configuration.slots.filter(s => {
      const old = previous?.config.configuration.slots.find(o => o.id === s.id)
      return resetAll || !old || old.assetName !== s.assetName || old.enabled !== s.enabled
    }).map(s => s.id))
    const reset = previous ? new Set(config.configuration.slots.filter(slot => {
      const old = previous.config.configuration.slots.find(candidate => candidate.id === slot.id)
      return resetAll || old?.assetName !== slot.assetName
    }).map(slot => slot.id)) : new Set<number>()
    for (const [key, value] of this.queue) if (value.platform === platform && changed.has(value.slotId)) this.queue.delete(key)
    const readers = Array.from({ length: 2 }, () => {
      const ocr = new TesseractOCRProvider(), visual = new VisualMarketDataProvider(c => this.browsers.captureSlot(c), ocr)
      visual.start()
      return { visual, ocr }
    })
    const next: WorkspaceData = { config, readers, contextIds: new Map(config.configuration.slots.map(s => [s.id, changed.has(s.id) ? randomUUID() : previous!.contextIds.get(s.id)!])), scheduler: previous?.scheduler ?? new CaptureScheduler(),
      signature: previous?.signature ?? '', count: 0, started: Date.now(), activeBatch: previous?.activeBatch ?? null, nextBatchAt: 0,
      resetting: reset, resetRevision: (previous?.resetRevision ?? 0) + 1, snapshot: { running: previous?.snapshot.running ?? false,
        intervalMs: previous?.snapshot.intervalMs ?? (platform === 'capitalbear' ? 500 : 1000),
        slots: config.configuration.slots.map(s => !changed.has(s.id) && previous ? previous.snapshot.slots.find(old => old.slotId === s.id)! : ({ slotId: s.id, state: s.enabled ? 'WAITING' : 'DISABLED', secondSamples: 0, m1Samples: 0, m1State: null, observation: null, dropped: 0, pixelBounds: null,
          ...Object.fromEntries(['lastAttemptAt', 'lastParsedPriceAt', 'lastGoodPriceAt', 'attemptCount', 'parsedCount', 'goodCount', 'uncertainCount']
            .map(key => [key, previous?.snapshot.slots.find(old => old.slotId === s.id)?.[key as keyof MarketSnapshot['slots'][number]] ?? (key.endsWith('Count') ? 0 : null)])) })),
        dropped: 0, queueDepth: 0, queueLagMs: 0, captureRate: 0, engineAvailable: false } }
    if (previous) Object.assign(previous, next)
    const workspace = previous ?? next
    this.workspaces.set(platform, workspace)
    if (reset.size) void this.resetSlots(platform, [...reset], workspace.resetRevision)
  }
  command(command: MarketCommand): MarketSnapshot {
    const workspace = this.workspaces.get(command.platform)
    if (!workspace) throw new Error('Load configuration first')
    if (command.intervalMs !== undefined) workspace.snapshot.intervalMs = command.intervalMs
    if (command.operation === 'probe') throw new Error('Use asynchronous probe')
    if (command.operation !== 'state') {
      workspace.snapshot.running = command.operation === 'start'
      workspace.count = 0; workspace.started = Date.now(); workspace.nextBatchAt = 0
      workspace.snapshot.engineAvailable = false; workspace.snapshot.queueLagMs = 0
      workspace.scheduler.invalidate(); workspace.contextIds = new Map(workspace.snapshot.slots.map(s => [s.slotId, randomUUID()]))
      for (const [key, value] of this.queue) if (value.platform === command.platform) this.queue.delete(key)
      for (const slot of workspace.snapshot.slots) { slot.observation = null; slot.s5Samples = 0; slot.s5State = null; slot.secondSamples = 0; slot.m1Samples = 0; slot.m1State = null; if (slot.state !== 'DISABLED') slot.state = workspace.snapshot.running ? 'WAITING' : 'PAUSED' }
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
        w.signature = signature; w.contextIds = new Map(w.snapshot.slots.map(s => [s.slotId, randomUUID()])); w.scheduler.invalidate()
        for (const [key, value] of this.queue) if (value.platform === platform) this.queue.delete(key)
        for (const slot of w.snapshot.slots) { slot.observation = null; slot.s5Samples = 0; slot.s5State = null; slot.secondSamples = 0; slot.m1Samples = 0; slot.m1State = null }
      }
      const profile = w.config.calibrations.find(p => p.id === w.config.activeCalibrationId)
      const geometryReady = profile && surface.gridReady && calibrationZoomMatches(profile, surface)
      for (const slot of w.snapshot.slots) {
        const configured = w.config.configuration.slots.find(s => s.id === slot.slotId)!
        if (!configured.enabled) { slot.state = 'DISABLED'; continue }
        if (w.resetting.has(configured.id)) { slot.state = 'WAITING'; continue }
        if ((!w.snapshot.running && !w.activeBatch) || surface.paused) { slot.state = 'PAUSED'; continue }
        if (!surface.available || !geometryReady) {
          slot.state = 'WAITING'; slot.diagnostics = { stage: 'CANVAS BOUNDS', message: 'Verified grid and matching calibration zoom required. Use Sync Assets or Calibrate Chart Area.' }; continue
        }
        if (slot.observation && Date.now() - Date.parse(slot.observation.observedAt) > 3000) slot.state = 'STALE'
      }
      if (!w.snapshot.running || !surface.available || surface.paused || !geometryReady || w.activeBatch || Date.now() < w.nextBatchAt) continue
      this.startBatch(platform, w)
    }
  }
  async probe(platform: Platform): Promise<MarketSnapshot> {
    this.tick()
    const w = this.workspaces.get(platform)
    if (!w) throw new Error('Load configuration first')
    while (w.activeBatch) await w.activeBatch
    await this.startBatch(platform, w, true)
    return this.command({ platform, operation: 'state' })
  }
  private startBatch(platform: Platform, w: WorkspaceData, probe = false): Promise<void> {
    const active = this.captureBatch(platform, w, probe)
    w.activeBatch = active
    return active.finally(() => {
      if (w.activeBatch === active) { w.activeBatch = null; w.nextBatchAt = Date.now() + w.snapshot.intervalMs }
    })
  }
  private async captureBatch(platform: Platform, w: WorkspaceData, probe: boolean): Promise<void> {
    const surface = this.browsers.observationSurface(platform), signature = JSON.stringify(surface)
    const profile = w.config.calibrations.find(p => p.id === w.config.activeCalibrationId)
    const contexts: ObservationContext[] = []
    const fail = (slot: MarketSnapshot['slots'][number], error: unknown, captured = false): void => {
      if (captured) this.captureMetric(platform, slot.slotId, true, false)
      slot.state = 'DATA_UNCERTAIN'; slot.observation = null
      slot.diagnostics = { ...slot.diagnostics, stage: captured ? 'PRICE ROI' : error instanceof Error && (error.message.startsWith('TAB') || error.message.startsWith('CHART') || error.message.startsWith('ASSET')) ? 'MAPPING' : 'CANVAS BOUNDS',
        message: error instanceof Error ? error.message : 'Capture unavailable' }
    }
    for (const configured of w.config.configuration.slots.filter(s => s.enabled && (probe || !w.resetting.has(s.id)))) {
      const slot = w.snapshot.slots.find(s => s.slotId === configured.id)!
      slot.lastAttemptAt = Date.now(); slot.attemptCount = (slot.attemptCount ?? 0) + 1
      if (probe) slot.observation = null
      slot.diagnostics = { stage: 'MAPPING' }
      try {
        if (!surface.available || surface.paused || !profile || !surface.gridReady || !calibrationZoomMatches(profile, surface))
          throw new Error('Verified grid and matching calibration required')
        const canvasSlotId = this.browsers.chartSlot(platform, configured.id, configured.assetName)
        const cell = calibrationToChartGrid(platform, profile.slots, 'LEGACY').slots.find(c => c.slotId === canvasSlotId)!
        const geometry = canvasPriceGeometry(platform, cell.chartBounds, surface.bounds.width, surface.zoomFactor)
        slot.pixelBounds = normalizedToPixel(geometry.chartBounds, surface.bounds.width, surface.bounds.height)
        slot.diagnostics = { stage: 'PRICE ROI', canvasSlotId, contextId: w.contextIds.get(configured.id)!,
          tabIdentity: `${platform}:${configured.id}:${configured.assetName}`,
          gridConfidence: this.browsers.command({ platform, operation: 'state' }).grid?.confidence,
          pricePixelBounds: normalizedToPixel(geometry.priceBounds, surface.bounds.width, surface.bounds.height) }
        contexts.push({ platform, slotId: configured.id, assetName: configured.assetName,
          contextId: w.contextIds.get(configured.id)!, calibrationProfileId: profile.id, bounds: geometry.chartBounds,
          priceBounds: geometry.priceBounds, diagnostics: slot.diagnostics })
        slot.state = 'CAPTURING'
      } catch (error) { fail(slot, error) }
    }
    if (!contexts.length) return
    const current = (context: ObservationContext): boolean => w.contextIds.get(context.slotId) === context.contextId &&
      signature === JSON.stringify(this.browsers.observationSurface(platform))
    let batch: Awaited<ReturnType<PlatformBrowserManager['captureSlots']>>
    try { batch = await this.browsers.captureSlots(contexts) }
    catch (error) {
      for (const context of contexts) if (current(context)) fail(w.snapshot.slots.find(s => s.slotId === context.slotId)!, error)
      return
    }
    // All ROIs are already captured. Two workers drain this bounded, nine-crop frame fairly.
    let cursor = 0
    await Promise.all(w.readers.map(async reader => {
      while (cursor < contexts.length) {
        const context = contexts[cursor++]!, slot = w.snapshot.slots.find(s => s.slotId === context.slotId)!
        if (!current(context)) continue
        let captured = false
        await w.scheduler.run(`${platform}:${context.slotId}`, true, 0, async () => {
          const image = batch.images.get(context.slotId)
          if (!image || image instanceof Error) throw image ?? new Error('Missing slot crop')
          captured = true
          slot.state = 'PARSING'
          return reader.visual.observeImage(context, image, batch.observedAt)
        }, value => {
          if (!current(context)) return
          slot.observation = value
          slot.pixelBounds = normalizedToPixel(context.bounds, surface.bounds.width, surface.bounds.height)
          slot.state = value.dataQuality.state === 'GOOD' ? 'READY' : value.dataQuality.state === 'STALE' ? 'STALE' : 'DATA_UNCERTAIN'
          w.count++
          if (value.price !== null) { slot.lastParsedPriceAt = Date.now(); slot.parsedCount = (slot.parsedCount ?? 0) + 1 }
          if (value.dataQuality.state === 'GOOD') { slot.lastGoodPriceAt = Date.now(); slot.goodCount = (slot.goodCount ?? 0) + 1 }
          if (value.dataQuality.state === 'UNCERTAIN') slot.uncertainCount = (slot.uncertainCount ?? 0) + 1
          this.captureMetric(platform, context.slotId, slot.state === 'DATA_UNCERTAIN', true)
          // Keep every observation until transmission. Bound offline backlog and report overflow.
          if (this.queue.size >= 180) { this.queue.delete(this.queue.keys().next().value!); w.snapshot.dropped++; slot.dropped++ }
          this.queue.set(value.id, value)
        }, error => { if (current(context)) fail(slot, error, captured) })
      }
    }))
  }
  private async resetSlots(platform: Platform, slotIds: number[], revision: number): Promise<void> {
    for (let attempt = 0; attempt < 4; attempt++) {
      const workspace = this.workspaces.get(platform)
      if (!workspace || workspace.resetRevision !== revision) return
      try {
        const response = await fetch(new URL('/api/market/slots/reset', this.connection.healthUrl), {
          method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ platform, slotIds }),
          signal: AbortSignal.timeout(2000), redirect: 'error' })
        if (response.status === 429) { this.transport.http429s++; throw new Error('Engine busy') }
        if (!response.ok) throw new Error('Engine reset unavailable')
        const value: unknown = await response.json()
        if (!value || typeof value !== 'object' || typeof (value as { reset?: unknown }).reset !== 'number') throw new Error('Invalid reset response')
        if (workspace.resetRevision === revision) for (const slotId of slotIds) workspace.resetting.delete(slotId)
        return
      } catch {
        if (attempt === 3) {
          const current = this.workspaces.get(platform)
          if (current?.resetRevision === revision)
            for (const slot of current.snapshot.slots) if (slotIds.includes(slot.slotId)) slot.state = 'ERROR'
          return
        }
        await new Promise(resolve => setTimeout(resolve, 100 * (attempt + 1)))
      }
    }
  }
  private async flush(): Promise<void> {
    if (this.sending || !this.queue.size) return
    this.sending = true
    const batch = [...this.queue.values()].slice(0, 18)
    for (const value of batch) this.queue.delete(value.id)
    const lag = Math.max(...batch.map(o => Date.now() - Date.parse(o.observedAt)))
    try {
      const response = await fetch(new URL('/api/market/observations', this.connection.healthUrl), {
        method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ observations: batch }),
        signal: AbortSignal.timeout(2000), redirect: 'error' })
      if (response.status === 429) this.transport.http429s++
      if (!response.ok) throw new Error('Engine unavailable')
      const result = MarketBatchResultSchema.parse(await response.json())
      for (const status of result.slots) {
        const w = this.workspaces.get(status.platform)
        const slot = w?.snapshot.slots.find(s => s.slotId === status.slotId)
        if (slot && w?.contextIds.get(status.slotId) === status.contextId) {
          slot.s5Samples = status.s5Samples ?? 0; slot.s5State = status.s5State ?? null; slot.secondSamples = status.secondSamples; slot.m1Samples = status.m1Samples; slot.m1State = status.m1State
        }
      }
      for (const platform of new Set(batch.map(o => o.platform))) {
        const w = this.workspaces.get(platform)!
        w.snapshot.engineAvailable = true; w.snapshot.queueLagMs = lag
      }
    } catch {
      this.transport.droppedBatches++
      for (const o of batch) {
        const w = this.workspaces.get(o.platform)
        if (w) { w.snapshot.engineAvailable = false; w.snapshot.dropped++; w.snapshot.queueLagMs = lag }
      }
    } finally { this.sending = false }
  }
  stop(): void {
    clearInterval(this.timer); clearInterval(this.flushTimer)
    for (const w of this.workspaces.values()) { w.scheduler.invalidate(); for (const reader of w.readers) { reader.visual.stop(); void reader.ocr.stop().catch(() => {}) } }
    this.queue.clear()
  }
}
