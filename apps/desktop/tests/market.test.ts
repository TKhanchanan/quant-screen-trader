import { randomUUID } from 'node:crypto'
import { describe, expect, it } from 'vitest'
import { MarketObservationSchema, normalizedToPixel } from '@quant-screen-trader/shared-types'
import { DOMMarketDataProvider, ReplayMarketDataProvider, SyntheticMarketDataProvider, normalizeBitmap,
  observation, parsePayout, parsePrice, parseTimer, type ObservationContext } from '../electron/main/market-providers'
import { CaptureScheduler } from '../electron/main/market-scheduler'

const context: ObservationContext = { platform: 'capitalbear', slotId: 1, assetName: 'EUR/USD OTC',
  contextId: randomUUID(), calibrationProfileId: randomUUID(), bounds: { x: .5, y: .5, width: .5, height: .5 } }
const fixture = (): ReturnType<typeof observation> => observation(context, 'SYNTHETIC',
  { asset: context.assetName, price: '1.23456', confidence: .95 }, Date.now())
describe('strict UI parsers', () => {
  it.each(['1.23456', '0.98765', '150.42', '102345.5'])('accepts %s', text => expect(parsePrice(text)).toBe(Number(text)))
  it.each(['1.O845', '', 'NaN', 'Infinity', '-1', '0', '1,234.5', '1e5', '12abc', '.5'])('rejects %s', text => expect(parsePrice(text)).toBeNull())
  it('uses payout ratios and timer seconds', () => {
    expect(parsePayout('82%')).toBe(.82); expect(parsePayout('100.1%')).toBeNull()
    expect(parseTimer('00:59')).toBe(59); expect(parseTimer('00:05')).toBe(5); expect(parseTimer('1:23')).toBe(83)
    expect(parseTimer('1:60')).toBeNull()
  })
  it('scales normalized ROI with resized browser', () => {
    expect(normalizedToPixel(context.bounds, 1000, 600)).toEqual({ x: 500, y: 300, width: 500, height: 300 })
    expect(normalizedToPixel(context.bounds, 2000, 1200)).toEqual({ x: 1000, y: 600, width: 1000, height: 600 })
  })
  it('normalizes without changing original bytes', () => {
    const bitmap = new Uint8Array([20, 20, 20, 255, 100, 100, 100, 255])
    expect([...normalizeBitmap(bitmap, 2, 1).grayscale]).toEqual([0, 255])
    expect(bitmap[0]).toBe(20)
    expect(() => normalizeBitmap(bitmap, 10, 10)).toThrow()
  })
})
describe('provenance and confidence', () => {
  it('rejects arbitrary metadata and session fields', () => {
    expect(MarketObservationSchema.safeParse({ ...fixture(), cookies: 'secret' }).success).toBe(false)
    expect(MarketObservationSchema.safeParse({ ...fixture(), price: NaN }).success).toBe(false)
  })
  it('requires exact asset identity, high confidence, freshness', () => {
    expect(observation(context, 'DOM', { price: '1', confidence: 1 }, Date.now()).dataQuality.state).toBe('UNCERTAIN')
    expect(observation(context, 'DOM', { asset: context.assetName, price: '1', confidence: .4 }, Date.now()).dataQuality.state).toBe('UNCERTAIN')
    expect(observation(context, 'DOM', { asset: context.assetName, price: '1', confidence: 1 }, Date.now() - 4000).dataQuality.state).toBe('STALE')
  })
  it('providers preserve context and explicitly identify fixtures', async () => {
    const dom = new DOMMarketDataProvider(async () => ({ asset: context.assetName, price: '1.2', confidence: .9 }))
    await expect(dom.observe(context)).rejects.toThrow()
    dom.start(); expect((await dom.observe(context)).sourceType).toBe('DOM'); dom.stop()
    for (const provider of [new ReplayMarketDataProvider([fixture()]), new SyntheticMarketDataProvider([fixture()])]) {
      provider.start(); const value = await provider.observe(context)
      expect(value.sourceType).toBe(provider.sourceType); expect(value.assetName).toBe(context.assetName)
      expect(value.platform).toBe(context.platform); expect(value.slotId).toBe(1)
    }
    const replay = new ReplayMarketDataProvider([fixture()]); replay.start()
    await expect(replay.observe({ ...context, platform: 'iqoption' })).rejects.toThrow()
  })
})
describe('bounded scheduler', () => {
  it('skips disabled, prevents overlap, invalidates in-flight results and isolates exceptions', async () => {
    const scheduler = new CaptureScheduler(); let count = 0, accepted = 0, errors = 0
    let release!: () => void
    const job = async (): Promise<ReturnType<typeof fixture>> => { count++; await new Promise<void>(r => { release = r }); return fixture() }
    const accept = (): void => { accepted++ }, fail = (): void => { errors++ }
    await scheduler.run('a', false, 250, job, accept, fail, 0); expect(count).toBe(0)
    const pending = scheduler.run('a', true, 250, job, accept, fail, 0)
    await scheduler.run('a', true, 250, job, accept, fail, 500); expect(count).toBe(1)
    scheduler.invalidate()
    await scheduler.run('a', true, 250, job, accept, fail, 600); expect(count).toBe(1)
    release(); await pending; expect(accepted).toBe(0)
    await scheduler.run('b', true, 250, async () => { throw new Error('parser') }, accept, fail, 1000)
    await scheduler.run('c', true, 250, async () => fixture(), accept, fail, 1000)
    expect(errors).toBe(1); expect(accepted).toBe(1); expect(scheduler.dropped).toBe(2)
  })
})
