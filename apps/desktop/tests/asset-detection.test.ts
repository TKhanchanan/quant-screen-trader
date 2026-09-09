import { describe, expect, it } from 'vitest'
import { AssetDetectionResultSchema, defaultCalibration, type Platform } from '@quant-screen-trader/shared-types'
import { CapitalBearAssetDetector, IQOptionAssetDetector, mapChartLabels, normalizeAsset, type ChartLabel } from '../electron/main/asset-detector'
import { AssetStability } from '../electron/main/asset-sync'
import { parseOCRFields } from '../electron/main/market-ocr'
import { createPlaceholderSlots } from '../src/renderer/src/features/slots/createPlaceholderSlots'
const chart = (id: number, label: string): ChartLabel => ({ bounds: defaultCalibration()[id - 1]!.bounds, label, tooltip: null })
for (const platform of ['capitalbear', 'iqoption'] as const) describe(`${platform} asset detection`, () => {
  it('maps chart geometry independently of tab/input order and permits duplicate instruments', () => {
    const detected = mapChartLabels(platform, Array.from({ length: 9 }, (_, i) => chart(9 - i, 'EUR / USD (OTC)')))
    expect(detected.slots[8]?.assetName).toBe('EUR/USD OTC')
    // Sparse layouts require calibrated mapping; a full grid can use chart geometry.
    const calibrated = mapChartLabels(platform, [chart(9, 'EUR / USD (OTC)'), chart(1, 'EUR / USD (OTC)'), chart(4, 'GBP/JPY')], defaultCalibration())
    expect(calibrated.slots[8]?.assetName).toBe('EUR/USD OTC')
    expect(calibrated.slots[0]?.assetName).toBe('EUR/USD OTC')
    expect(calibrated.slots[3]?.assetName).toBe('GBP/JPY')
    expect(calibrated.slots[1]?.state).toBe('NOT_FOUND')
  })
  it('uses matching complete tooltips and rejects truncated or conflicting identity', () => {
    const result = mapChartLabels(platform, [{ ...chart(1, 'Injective (OT...'), tooltip: 'Injective (OTC)' }], defaultCalibration())
    expect(result.slots[0]?.assetName).toBe('Injective OTC')
    expect(result.slots[0]?.evidenceType).toBe('LABEL_TOOLTIP')
    const extended = mapChartLabels(platform, [{ ...chart(1, 'EUR / USD'), tooltip: 'EUR/USD (OTC)' }], defaultCalibration())
    expect(extended.slots[0]?.assetName).toBe('EUR/USD OTC')
    expect(extended.slots[0]?.evidenceType).toBe('LABEL_TOOLTIP')
    expect(mapChartLabels(platform, [chart(1, 'Injective (OT...')], defaultCalibration()).slots[0]?.state).toBe('UNCERTAIN')
    expect(mapChartLabels(platform, [chart(1, 'EUR/USD'), chart(1, 'GBP/USD')], defaultCalibration()).slots[0]?.state).toBe('UNCERTAIN')
    expect(normalizeAsset('EUR/US? O?C')).toBeNull()
    expect(normalizeAsset('EUR/USD')).not.toBe(normalizeAsset('EUR/USD OTC'))
  })
  it('uses a scoped platform adapter and validates the browser response', async () => {
    const Adapter = platform === 'capitalbear' ? CapitalBearAssetDetector : IQOptionAssetDetector
    const adapter = new Adapter(async script => { expect(script).toContain('canvas,svg'); expect(script).toContain('role="tablist"'); return [chart(1, 'EUR/USD')] })
    expect((await adapter.detectAssets(defaultCalibration())).slots[0]?.assetName).toBe('EUR/USD')
    await expect(new Adapter(async () => [{ ...chart(1, 'EUR/USD'), cookies: 'forbidden' }]).detectAssets()).rejects.toThrow()
  })
})
it('accepts a safe single-name OCR asset and rejects account or numeric text', () => {
  expect(parseOCRFields('Apple\n1.23456\n82%\n00:59', .98).asset).toBe('Apple')
  expect(parseOCRFields('Account balance\n1.23456', .98).asset).toBeUndefined()
  expect(parseOCRFields('12345\n1.23456', .98).asset).toBeUndefined()
  const title = [{ text: 'Apple v', confidence: 40, words: [{ text: 'Apple', confidence: 99 }, { text: 'v', confidence: 1 }] }]
  expect(parseOCRFields('Apple\nv', .4, title).confidence).toBe(.99)
  expect(parseOCRFields('EUR/USD (OTC) v', .98).asset).toBe('EUR/USD (OTC)')
  expect(parseOCRFields('Apple\n1.23456', .4, title).confidence).toBe(.4)
})
it('debounces stable changes, preserves uncertain and locked slots, and resets on new context', () => {
  const platform: Platform = 'capitalbear'
  const stability = new AssetStability()
  const slots = createPlaceholderSlots(platform).map(s => ({ ...s, assetMode: s.id === 2 ? 'MANUAL' as const : 'AUTO' as const, assetName: 'OLD', enabled: true }))
  const detect = (asset: string): ReturnType<typeof mapChartLabels> => mapChartLabels(platform, [chart(1, asset), chart(2, 'GBP/USD')], defaultCalibration())
  expect(stability.apply(slots, detect('EUR/USD'), 3)[0]?.assetName).toBe('OLD')
  expect(stability.apply(slots, detect('GBP/USD'), 3)[0]?.assetName).toBe('OLD')
  expect(stability.apply(slots, detect('EUR/USD'), 3)[0]?.assetName).toBe('OLD')
  stability.apply(slots, detect('EUR/USD'), 3)
  const ready = stability.apply(slots, detect('EUR/USD'), 3)
  expect(ready[0]?.assetName).toBe('EUR/USD'); expect(ready[1]?.assetName).toBe('OLD')
  expect(ready[2]?.assetName).toBe('OLD')
  stability.reset()
  expect(stability.apply(slots, detect('EUR/USD'), 3)[0]?.assetName).toBe('OLD')
  expect(stability.apply(slots, detect('EUR/US?'), 1)[0]?.assetName).toBe('OLD')
})
it('rejects cross-platform and duplicate-slot results', () => {
  const r = mapChartLabels('capitalbear', [chart(1, 'EUR/USD')], defaultCalibration())
  expect(AssetDetectionResultSchema.safeParse({ ...r, platform: 'iqoption' }).success).toBe(false)
  expect(AssetDetectionResultSchema.safeParse({ ...r, slots: r.slots.map(() => r.slots[0]) }).success).toBe(false)
})
