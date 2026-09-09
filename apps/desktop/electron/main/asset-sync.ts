import { type AssetDetectionResult, type AssetSyncCommand, type AssetSyncState, type ConfigurationResult, type Platform, type PlatformSlot } from '@quant-screen-trader/shared-types'
import { TesseractOCRProvider } from './market-ocr'
import type { PlatformBrowserManager } from './platform-browser'

export class AssetStability {
  private readonly pending = new Map<number, { name: string; count: number; source: 'DOM' | 'OCR' }>()
  reset(): void { this.pending.clear() }
  apply(slots: PlatformSlot[], result: AssetDetectionResult, required: number, checked?: Set<number>): PlatformSlot[] {
    return slots.map(slot => {
      const detected = result.slots.find(d => d.slotId === slot.id)
      if (checked && !checked.has(slot.id) && this.pending.get(slot.id)?.source === 'OCR' && slot.assetMode !== 'MANUAL') return slot
      if (slot.assetMode === 'MANUAL' || !detected || detected.state === 'UNCERTAIN' || detected.confidence < .9 ||
        (detected.state === 'DETECTED' && !detected.assetName)) {
        this.pending.delete(slot.id); return slot
      }
      const name = detected.state === 'NOT_FOUND' ? '' : detected.assetName!
      const previous = this.pending.get(slot.id)
      const count = previous?.name === name && previous.source === detected.source ? previous.count + 1 : 1
      this.pending.set(slot.id, { name, count, source: detected.source })
      if (count < required) return slot
      if (!name) return { ...slot, assetMode: 'AUTO', assetName: '', displayName: undefined, enabled: false }
      return { ...slot, assetMode: 'AUTO', assetName: name, displayName: detected.displayName ?? name, enabled: true }
    })
  }
}
interface Entry { config: ConfigurationResult; state: AssetSyncState; stability: AssetStability; generation: number; next: number; signature: string; active: Promise<void> | null }
export class AssetSyncManager {
  private readonly entries = new Map<Platform, Entry>()
  private readonly timer: ReturnType<typeof setInterval>
  private readonly ocr = new TesseractOCRProvider()
  private ocrBusy = false
  constructor(private readonly browsers: PlatformBrowserManager,
    private readonly save: (platform: Platform, before: ConfigurationResult, slots: PlatformSlot[]) => Promise<ConfigurationResult | null>,
    private readonly apply: (config: ConfigurationResult) => void = () => {}) {
    this.timer = setInterval(() => { for (const [platform, entry] of this.entries) {
      if (entry.state.auto && !entry.state.busy && Date.now() >= entry.next) void this.run(platform, false)
    } }, 500)
  }
  configure(config: ConfigurationResult): void {
    const platform = config.configuration.platform, old = this.entries.get(platform)
    if (old) {
      if (JSON.stringify(old.config) !== JSON.stringify(config)) { old.generation++; old.stability.reset(); if (JSON.stringify(old.config.configuration) !== JSON.stringify(config.configuration)) old.state.detection = null; old.state.revision++ }
      old.config = config
    } else this.entries.set(platform, { config, generation: 0, next: 0, signature: '', active: null, stability: new AssetStability(),
      state: { auto: false, busy: false, intervalMs: 3000, stableChecks: 3, detection: null, applied: 0, manualPreserved: 0, error: null, revision: 0 } })
  }
  async command(command: AssetSyncCommand): Promise<AssetSyncState> {
    const entry = this.entries.get(command.platform)
    if (!entry) throw new Error('Load workspace configuration first')
    if (command.intervalMs !== undefined) entry.state.intervalMs = command.intervalMs
    if (command.stableChecks !== undefined) { entry.state.stableChecks = command.stableChecks; entry.stability.reset() }
    if (command.operation === 'auto') { entry.state.auto = command.enabled ?? false; entry.generation++; entry.stability.reset(); entry.next = 0 }
    if (command.operation === 'sync') await this.run(command.platform, true)
    return entry.state
  }
  private run(platform: Platform, once: boolean): Promise<void> {
    const entry = this.entries.get(platform)!
    if (entry.active) return once ? entry.active.then(() => this.run(platform, true)) : entry.active
    const active = this.execute(platform, once)
    entry.active = active
    return active.finally(() => { if (entry.active === active) entry.active = null })
  }
  private async execute(platform: Platform, once: boolean): Promise<void> {
    const entry = this.entries.get(platform)!
    entry.next = Date.now() + entry.state.intervalMs
    const surface = this.browsers.observationSurface(platform)
    const signature = JSON.stringify(surface)
    if (signature !== entry.signature) { entry.signature = signature; entry.generation++; entry.stability.reset() }
    if (!surface.available || surface.paused) { entry.state.error = 'Asset sync paused: show the platform and close calibration.'; return }
    const generation = entry.generation, before = entry.config
    entry.state.busy = true; entry.state.error = null; entry.state.applied = 0
    try {
      if (this.ocrBusy) { entry.state.error = 'Asset OCR is busy in the other workspace. Sync again shortly.'; return }
      this.ocrBusy = true
      let result: AssetDetectionResult
      try { result = await this.browsers.captureAssetTabs(platform, image => this.ocr.parseText(image)) }
      finally { this.ocrBusy = false }
      if (entry.generation !== generation || JSON.stringify(this.browsers.observationSurface(platform)) !== signature) return
      result.overallConfidence = result.slots.reduce((n, s) => n + s.confidence, 0) / 9
      entry.state.detection = result
      const slots = entry.stability.apply(before.configuration.slots, result, once ? 1 : entry.state.stableChecks)
      entry.state.manualPreserved = slots.filter(s => s.assetMode === 'MANUAL').length
      const changed = slots.filter((s, i) => JSON.stringify(s) !== JSON.stringify(before.configuration.slots[i])).length
      if (changed) {
        const saved = await this.save(platform, before, slots)
        if (!saved) { entry.state.error = 'Configuration changed during detection; sync again.'; return }
        if (entry.generation !== generation || JSON.stringify(this.browsers.observationSurface(platform)) !== signature) return
        entry.config = saved; entry.state.applied = changed; entry.state.revision++
        this.apply(saved)
      }
    } catch (error) { entry.state.error = error instanceof Error &&
      (error.message.startsWith('TAB_GEOMETRY_UNCERTAIN') || error.message.startsWith('Chart grid restoration failed') || error.message.startsWith('Chart grid preparation failed') || error.message.includes('platform was reloaded'))
      ? error.message : 'Asset detection unavailable. Existing assets were preserved.' }
    finally { entry.state.busy = false; entry.next = Date.now() + entry.state.intervalMs }
  }
  stop(): void { clearInterval(this.timer); for (const e of this.entries.values()) e.generation++; void this.ocr.stop().catch(() => {}) }
}
