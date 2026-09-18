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
  const state = vi.fn(() => ({ armed: false, tickets: [], lastBoardAsOf: null, settings: { mode: 'OFF' } }))
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
    { state: () => ({ armed: false, tickets: [], lastBoardAsOf: null, settings: { mode: 'OFF' } }) } as unknown as ExecutionManager,
    { host: '127.0.0.1', port: 8765, healthUrl: 'http://127.0.0.1:8765/health' })
  await vi.advanceTimersByTimeAsync(10000)
  expect(fetcher).toHaveBeenCalledTimes(1)
  finish(new Response('{}'))
  await vi.advanceTimersByTimeAsync(1000)
  expect(fetcher).toHaveBeenCalledTimes(2)
})
it('reports a PAPER-armed executor and its would-press tickets separately from a live arm', async () => {
  vi.useFakeTimers()
  const ticket = (id: string, state: 'PAPER' | 'BLOCKED', boardAsOf: number) => ({ id, platform: 'capitalbear', slotId: 3,
    assetName: 'EUR/USD OTC', direction: 'LOWER', boardAsOf, rankScore: .8, ensembleConfidence: .7, state,
    reasons: state === 'PAPER' ? ['NOT_SENT'] : ['TAB_IDENTITY_UNCERTAIN'], requestedAt: '2026-09-18T01:00:00.000Z',
    pressedAt: null, latencyMs: null })
  let execution = { armed: true, settings: { mode: 'PAPER' }, lastBoardAsOf: 1_000, tickets: [ticket('a', 'PAPER', 1_000)] }
  const bodies: Record<string, unknown>[] = []
  vi.stubGlobal('fetch', vi.fn(async (_url: URL, options: RequestInit) => {
    bodies.push(JSON.parse(String(options.body)) as Record<string, unknown>); return new Response('{}')
  }))
  telemetry = new ShadowLiveTelemetry({ operationalState: () => [{ platform: 'capitalbear', slots: [] }] } as unknown as MarketManager,
    { state: () => execution } as unknown as ExecutionManager,
    { host: '127.0.0.1', port: 8765, healthUrl: 'http://127.0.0.1:8765/health' })
  await vi.advanceTimersByTimeAsync(1000)
  execution = { ...execution, lastBoardAsOf: 2_000, tickets: [ticket('b', 'BLOCKED', 2_000), ...execution.tickets] }
  await vi.advanceTimersByTimeAsync(1000)
  expect(bodies[0]).toMatchObject({ armed: false, paperArmed: true, executionMode: 'PAPER', paperTickets: 1,
    blockedTickets: 0, boardsEvaluated: 1, brokerPresses: 0 })
  expect(bodies[1]).toMatchObject({ armed: false, paperTickets: 1, blockedTickets: 1, boardsEvaluated: 2 })
  expect((bodies[1]!.recentTickets as { id: string }[]).map(t => t.id)).toEqual(['b', 'a'])
  execution = { ...execution, settings: { mode: 'AUTO' } }
  await vi.advanceTimersByTimeAsync(1000)
  expect(bodies[2]).toMatchObject({ armed: true, paperArmed: false, executionMode: 'AUTO' })
})
it('preserves the accepted execution source files byte for byte', () => {
  const hashes: Record<string, string> = {"execution-manager.ts": "b30b5a6559f7e4db4e40bfd98a0ce82875484a02b6bcf120b9d4b53b90614420", "order-executor.ts": "a78827df4f31085708b0fe2b4dc3a7569289d3da7dfd3a159feec1d03fd05caf", "order-panel.ts": "fdf905d3aacc34e30f6ff106eccfd393e297728ba332a33c92dcc4553d0dd3a4"}
  for (const [name, digest] of Object.entries(hashes)) {
    const content = readFileSync(new URL(`../electron/main/${name}`, import.meta.url))
    expect(createHash('sha256').update(content).digest('hex')).toBe(digest)
  }
})
