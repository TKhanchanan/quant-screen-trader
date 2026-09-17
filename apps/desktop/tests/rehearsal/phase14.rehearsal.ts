import { readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { gunzipSync } from 'node:zlib'
import { expect, it, vi } from 'vitest'
import { observation, VisualMarketDataProvider } from '../../electron/main/market-providers'
import { gateScenarios, REHEARSAL_LABEL, runTimeline, timelineChecks, type TimelineRecord } from './phase14-execution'
import { queueChecks, queueRehearsal } from './phase14-queue'
vi.mock('../../electron/main/market-ocr', () => ({ TesseractOCRProvider: class { stop = vi.fn(async () => {}) } }))
const { MarketManager } = await import('../../electron/main/market-manager')

/**
 * The desktop half of `npm run rehearsal:phase14`. REHEARSAL ONLY — NOT REAL LIVE ACCEPTANCE.
 * Reads the engine's exported execution timeline and writes execution-summary.json beside it.
 */
it('replays the engine timeline through the real execution and transport code', async () => {
  const folder = process.env.QST_REHEARSAL_DIR
  if (!folder) throw new Error('Set QST_REHEARSAL_DIR (npm run rehearsal:phase14 does this).')
  const records = gunzipSync(readFileSync(join(folder, 'execution-timeline.jsonl.gz'))).toString('utf8')
    .split('\n').filter(Boolean).map(line => JSON.parse(line) as TimelineRecord)
  const started = performance.now()
  const timeline = await runTimeline(records)
  const timelineSeconds = (performance.now() - started) / 1000

  vi.useFakeTimers()
  const gates = await gateScenarios(ms => vi.setSystemTime(ms))
  vi.setSystemTime(Date.UTC(2026, 2, 2, 9))
  vi.spyOn(VisualMarketDataProvider.prototype, 'observeImage').mockImplementation(async context =>
    observation(context, 'VISUAL', { asset: context.assetName, price: String(context.slotId), confidence: .94 }, Date.now()))
  const queue = await queueRehearsal({ create: browsers => new MarketManager(browsers,
    { host: '127.0.0.1', port: 8765, healthUrl: 'http://127.0.0.1:8765/health' }),
  advance: ms => vi.advanceTimersByTimeAsync(ms).then(() => undefined) })
  vi.useRealTimers()
  vi.restoreAllMocks()

  const checks = [...timelineChecks(timeline), ...gates.map(g => ({ ...g, name: `gate: ${g.name}` })),
    ...queueChecks(queue).map(q => ({ ...q, name: `queue: ${q.name}` }))]
  const passed = checks.every(check => check.passed)
  const totals = (key: 'boardsSeen' | 'boardsEvaluated' | 'paperTickets') =>
    timeline.platforms.capitalbear[key] + timeline.platforms.iqoption[key]
  writeFileSync(join(folder, 'execution-summary.json'), JSON.stringify({
    label: REHEARSAL_LABEL, notRealLiveAcceptance: true, source: timeline.source, scenario: timeline.scenario,
    executionResult: passed ? 'PASS' : 'FAIL', mode: 'PAPER', wallSeconds: Math.round(timelineSeconds * 10) / 10,
    boardsSeen: totals('boardsSeen'), boardsEvaluated: totals('boardsEvaluated'), paperTickets: totals('paperTickets'),
    wouldPressGateScenarios: gates.filter(g => g.name.startsWith('COOLDOWN') || g.name.startsWith('HOURLY_CAP')).length,
    pressCalls: timeline.pressCalls, realBrokerPresses: timeline.realBrokerPresses, timeline,
    queue: { normal: queue.normal, burst: queue.burst, overflow: queue.overflow,
      telemetry: { bodies: queue.telemetry.bodies, maxQueueDepth: queue.telemetry.maxQueueDepth,
        armedEver: queue.telemetry.armedEver, maxBrokerPresses: queue.telemetry.maxBrokerPresses } },
    telemetrySample: queue.telemetry.sample, checks
  }, null, 2) + '\n')
  expect(checks.filter(check => !check.passed)).toEqual([])
}, 1_800_000)
