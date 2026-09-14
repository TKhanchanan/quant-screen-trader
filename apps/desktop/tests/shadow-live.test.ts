import { readFileSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { afterEach, expect, it, vi } from 'vitest'
import type { MarketManager } from '../electron/main/market-manager'
import type { ExecutionManager } from '../electron/main/execution-manager'
import { ShadowLiveTelemetry } from '../electron/main/shadow-live'

let telemetry: ShadowLiveTelemetry | undefined
afterEach(() => { telemetry?.stop(); vi.useRealTimers(); vi.unstubAllGlobals() })
it('only sends bounded health telemetry and cannot invoke an execution command', async () => {
  vi.useFakeTimers()
  const command = vi.fn(() => { throw new Error('Must never issue a command') })
  const state = vi.fn(() => ({ armed: false, tickets: [] }))
  const operationalState = () => [{ platform: 'capitalbear', captureRunning: true,
    surfaceAvailable: true, engineAvailable: true, intervalMs: 500, queueDepth: 18,
    droppedBatches: 2, http429s: 1, slots: [] }]
  const fetcher = vi.fn(async () => new Response('{}'))
  vi.stubGlobal('fetch', fetcher)
  telemetry = new ShadowLiveTelemetry({ operationalState, command } as unknown as MarketManager,
    { state, command } as unknown as ExecutionManager,
    { host: '127.0.0.1', port: 8765, healthUrl: 'http://127.0.0.1:8765/health' })
  await vi.advanceTimersByTimeAsync(2000)
  expect(fetcher).toHaveBeenCalledTimes(2)
  const [url, options] = fetcher.mock.calls[0] as unknown as [URL, RequestInit]
  expect(url.pathname).toBe('/api/shadow-live/telemetry')
  expect(JSON.parse(String(options.body))).toMatchObject({ armed: false, brokerPresses: 0,
    queueDepth: 18, droppedBatches: 2, http429s: 1 })
  expect(command).not.toHaveBeenCalled()
})
it('never queues telemetry while a request is stalled', async () => {
  vi.useFakeTimers()
  let finish: (response: Response) => void = () => {}
  const fetcher = vi.fn(() => new Promise<Response>(resolve => { finish = resolve }))
  vi.stubGlobal('fetch', fetcher)
  telemetry = new ShadowLiveTelemetry({ operationalState: () => [{ platform: 'capitalbear', slots: [] }] } as unknown as MarketManager,
    { state: () => ({ armed: false, tickets: [] }) } as unknown as ExecutionManager,
    { host: '127.0.0.1', port: 8765, healthUrl: 'http://127.0.0.1:8765/health' })
  await vi.advanceTimersByTimeAsync(10000)
  expect(fetcher).toHaveBeenCalledTimes(1)
  finish(new Response('{}'))
  await vi.advanceTimersByTimeAsync(1000)
  expect(fetcher).toHaveBeenCalledTimes(2)
})
it('preserves the accepted execution source files byte for byte', () => {
  const hashes: Record<string, string> = {"execution-manager.ts": "b30b5a6559f7e4db4e40bfd98a0ce82875484a02b6bcf120b9d4b53b90614420", "order-executor.ts": "a78827df4f31085708b0fe2b4dc3a7569289d3da7dfd3a159feec1d03fd05caf", "order-panel.ts": "fdf905d3aacc34e30f6ff106eccfd393e297728ba332a33c92dcc4553d0dd3a4"}
  for (const [name, digest] of Object.entries(hashes)) {
    const content = readFileSync(new URL(`../electron/main/${name}`, import.meta.url))
    expect(createHash('sha256').update(content).digest('hex')).toBe(digest)
  }
})
