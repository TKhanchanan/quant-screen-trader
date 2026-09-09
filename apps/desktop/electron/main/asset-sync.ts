import { type AssetDetectionResult, type AssetSyncCommand, type AssetSyncState, type ConfigurationResult, type Platform, type PlatformSlot } from '@quant-screen-trader/shared-types'
import { normalizeAsset } from './asset-detector'
import { TesseractOCRProvider } from './market-ocr'
import type { PlatformBrowserManager } from './platform-browser'

export class AssetStability {
  private readonly pending = new Map<number, { name: string; count: number; source: 'DOM' | 'OCR' }>()
  reset(): void { this.pending.clear() }
  apply(slots: PlatformSlot[], result: AssetDetectionResult, required: number, checked?: Set<number>): PlatformSlot[] {
    return slots.map(slot => {
      const detected = result.slots.find(d => d.slotId === slot.id)
      if (checked && !checked.has(slot.id) && this.pending.get(slot.id)?.source === 'OCR' && slot.assetMode !== 'MANUAL') return slot
      if (slot.assetMode === 'MANUAL' || !detected || detected.state !== 'DETECTED' || !detected.assetName || detected.confidence < .9) {
        this.pending.delete(slot.id); return slot
      }
      const previous = this.pending.get(slot.id)
      const count = previous?.name === detected.assetName && previous.source === detected.source ? previous.count + 1 : 1
      this.pending.set(slot.id, { name: detected.assetName, count, source: detected.source })
      if (count < required) return slot
      return { ...slot, assetMode: 'AUTO', assetName: detected.assetName, displayName: detected.displayName ?? detected.assetName, enabled: true }
    })
  }
}
interface Entry { config: ConfigurationResult; state: AssetSyncState; stability: AssetStability; generation: number; next: number; signature: string; ocrCursor: number; active: Promise<void> | null }
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
      if (JSON.stringify(old.config) !== JSON.stringify(config)) { old.generation++; old.stability.reset(); old.state.detection = null; old.state.revision++ }
      old.config = config
    } else this.entries.set(platform, { config, generation: 0, next: 0, signature: '', ocrCursor: 0, active: null, stability: new AssetStability(),
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
    const profile = before.calibrations.find(p => p.id === before.activeCalibrationId)
    entry.state.busy = true; entry.state.error = null; entry.state.applied = 0
    try {
      const start = Date.now(), result = await this.browsers.detectAssets(platform, profile?.slots)
      // Auto mode OCRs at most one small label per cycle. Explicit sync visits each missing label.
      const missing = result.slots.filter(s => s.state !== 'DETECTED' && before.configuration.slots.find(c => c.id === s.slotId)?.assetMode !== 'MANUAL')
      const checked = new Set(result.slots.filter(s => s.state === 'DETECTED').map(s => s.slotId))
      const fallbacks = once ? missing : missing.slice(entry.ocrCursor % Math.max(1, missing.length), entry.ocrCursor % Math.max(1, missing.length) + 1)
      entry.ocrCursor++
      if (missing.length && !profile) entry.state.error = 'No chart grid profile is active. Sync Assets can create an Auto Chart Grid from the workspace first.'
      if (once && fallbacks.length && profile && this.ocrBusy) entry.state.error = 'Asset OCR is busy in the other workspace. Sync again shortly.'
      if (fallbacks.length && profile && !this.ocrBusy) {
        this.ocrBusy = true
        try {
          for (const fallback of fallbacks) {
            if (entry.generation !== generation || JSON.stringify(this.browsers.observationSurface(platform)) !== signature) return
            checked.add(fallback.slotId)
            try {
              const text = await this.browsers.captureAssetLabel(platform, fallback.slotId, profile.slots,
                image => this.ocr.parseText(image))
              const name = text.asset ? normalizeAsset(text.asset) : null
              Object.assign(fallback, { source: 'OCR', evidenceType: 'CALIBRATED_OCR', confidence: text.confidence,
                detectedAt: new Date().toISOString(), state: name && text.confidence >= .95 ? 'DETECTED' : 'UNCERTAIN',
                assetName: name && text.confidence >= .95 ? name : null,
                displayName: name && text.confidence >= .95 ? text.asset : null,
                canonicalAssetId: name && text.confidence >= .95 ? `${platform}:${name}` : null })
            } catch (error) {
              if (error instanceof Error && (error.message.startsWith('Chart grid restoration failed') || error.message.includes('platform was reloaded'))) throw error
              Object.assign(fallback, { source: 'OCR', state: 'UNCERTAIN', confidence: 0, evidenceType: 'CALIBRATED_OCR' })
            }
          }
        } finally { this.ocrBusy = false }
      }
      if (entry.generation !== generation || JSON.stringify(this.browsers.observationSurface(platform)) !== signature) return
      result.durationMs = Date.now() - start
      result.overallConfidence = result.slots.reduce((n, s) => n + s.confidence, 0) / 9
      entry.state.detection = result
      const slots = entry.stability.apply(before.configuration.slots, result, once ? 1 : entry.state.stableChecks, checked)
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
      (error.message.startsWith('Chart grid restoration failed') || error.message.startsWith('Chart grid preparation failed') || error.message.includes('platform was reloaded'))
      ? error.message : 'Asset detection unavailable. Existing assets were preserved.' }
    finally { entry.state.busy = false; entry.next = Date.now() + entry.state.intervalMs }
  }
  stop(): void { clearInterval(this.timer); for (const e of this.entries.values()) e.generation++; void this.ocr.stop().catch(() => {}) }
}
