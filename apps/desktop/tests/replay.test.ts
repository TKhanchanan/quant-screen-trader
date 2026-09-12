import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import {
  IPC_CHANNELS, REPLAY_NOT_APPLIED_NOTICE, ReplayCommandSchema, ReplayStateSchema,
  ReplaySummarySchema, ReplayTallySchema, emptyReplayState, replayFoldLine, replayLatencyLine,
  replayMoneyLine, replayPercentLabel, replayTallyLine, replayWarningLabel,
  type ReplayState
} from '@quant-screen-trader/shared-types'
import { ReplayPanel, ReplayResult } from '../src/renderer/src/components/ReplayPanel'

const tally = (changes: Record<string, unknown> = {}): Record<string, unknown> => ({
  platform: 'capitalbear', durationMs: 5_000, ensembles: 3_207, boardsFinalized: 718,
  boardsSelected: 263, selectionCoverage: 0.366, resolved: 216, wins: 118, losses: 92, draws: 6,
  invalid: 3, cancelled: 2, winRateExcludingDraws: 118 / 210, lower95: 0.494, upper95: 0.629,
  tradesPerHour: 2.6, maxWinStreak: 7, maxLossStreak: 5, moneyAvailable: true, currency: 'THB',
  netPaperPnl: 236, profitFactor: 1.05, expectancyPerTrade: 1.09, maxDrawdown: 576,
  maxDrawdownRelative: null, ...changes
})
const fold = (id: number, changes: Record<string, unknown> = {}): Record<string, unknown> => ({
  foldId: id, trainStart: 1, trainEnd: 100, validationStart: 115, validationEnd: 200,
  testStart: 215, testEnd: 300, purgeMs: 15_000, embargoMs: 15_000,
  candidateMetric: 'rankScore', candidateThreshold: 0.65, directionalStable: true,
  monetaryStable: false, monetaryVerdict: 'UNTESTED',
  test: { selected: 60, coverage: 0.4, winRateExcludingDraws: 0.58, lift: 0.04,
    expectancyPerTrade: 1.2 },
  warnings: ['MONETARY_UNVERIFIED'], ...changes
})
const summary = (changes: Record<string, unknown> = {}): Record<string, unknown> => ({
  replayRunId: 'affd79d2-494d-5474-9de0-528c00b0b49d', label: 'CURRENT_FROZEN_PIPELINE',
  dataset: { entryLayer: 'PHASE4_MARKET_OBSERVATION', sourceMode: 'REPLAY',
    inputFingerprint: 'ddcddb241c3b9e77d011fa33f8e3dc5b5d89d47f49268f74e9a7d96850d27e14',
    events: 21_239, startTime: 1_788_963_259_209, endTime: 1_789_139_284_475,
    durationMs: 176_025_266, assetsSeen: 23, contextsSeen: 204, gapSeconds: 14_211,
    gapShare: 0.4 },
  causality: { checks: 101_535, violations: 0 },
  overall: tally({ platform: null, durationMs: 0 }),
  platforms: [tally(), tally({ platform: 'iqoption', durationMs: 60_000, resolved: 17 })],
  coverage: { dominantRegime: 'RANGE', dominantRegimeShare: 0.33, topAsset: 'Gold OTC',
    topAssetShare: 0.37, hoursCovered: 7, weekdaysCovered: 5, tradingDates: 6,
    timezone: 'Asia/Bangkok' },
  latency: [
    { delayMs: 0, resolved: 216, winRateExcludingDraws: 0.56, expectancyPerTrade: 1.1,
      winRateDelta: null, expectancyDelta: null },
    { delayMs: 500, resolved: 216, winRateExcludingDraws: 0.52, expectancyPerTrade: -0.4,
      winRateDelta: -0.04, expectancyDelta: -1.5 }
  ],
  walkForward: { mode: 'COUNT', folds: 3, foldsWithCandidate: 2, foldsDirectionalPositive: 1,
    foldsMonetaryPositive: 0, medianTestWinRate: 0.57, candidateStability: 'UNSTABLE',
    thresholdSpread: 0.2, rows: [fold(1), fold(2, { candidateMetric: null,
      candidateThreshold: null, directionalStable: false, warnings: ['NO_STABLE_CANDIDATE'] })],
    warnings: ['PARAMETER_INSTABILITY'] },
  warnings: ['MULTIPLE_TESTING_WARNING', 'MONETARY_UNVERIFIED'],
  researchOnly: true, appliedToLiveExecution: false, ...changes
})
const state = (changes: Record<string, unknown> = {}): ReplayState => ReplayStateSchema.parse({
  replayVersion: 'qst-replay-v1', available: true, busy: false,
  jobId: '6b0f0a7c-6a1e-4e2a-9a1a-4c4f9d1a2b3c',
  replayRunId: 'affd79d2-494d-5474-9de0-528c00b0b49d', status: 'COMPLETED', phase: 'DONE',
  totalEvents: 21_239, processedEvents: 21_239, percent: 1, currentMarketTime: 1_789_139_284_475,
  error: null, summary: summary(), message: '', ...changes
})
const source = (file: string): string => readFileSync(join(import.meta.dirname, '..', file), 'utf8')
/** The same file with its comments removed, so prose can neither fail nor hide a check. */
const code = (file: string): string =>
  source(file).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('replay bridge', () => {
  it('accepts an engine summary and tolerates fields the desktop does not render', () => {
    const parsed = state()
    expect(parsed.summary?.platforms).toHaveLength(2)
    expect(IPC_CHANNELS.replay).toBe('replay:command')
    expect(ReplayTallySchema.safeParse({ ...tally(), extraDiagnostic: 7 }).success).toBe(true)
  })

  it('rejects a malformed result rather than rendering an invented number', () => {
    expect(ReplayTallySchema.safeParse(tally({ winRateExcludingDraws: 1.4 })).success).toBe(false)
    expect(ReplayTallySchema.safeParse(tally({ wins: -1 })).success).toBe(false)
    expect(ReplaySummarySchema.safeParse(summary({ label: 'TUNED' })).success).toBe(false)
    expect(ReplaySummarySchema.safeParse(summary({ appliedToLiveExecution: true })).success)
      .toBe(false)
  })

  it('accepts only the two offline commands it declares', () => {
    expect(ReplayCommandSchema.safeParse({ operation: 'start', platform: null, windowMs: null,
      warmupMs: null, latencyScenarios: [] }).success).toBe(true)
    expect(ReplayCommandSchema.safeParse({ operation: 'cancel' }).success).toBe(true)
    expect(ReplayCommandSchema.safeParse({ operation: 'state' }).success).toBe(true)
    // Nothing that could apply a finding, and nothing that could reach a broker.
    expect(ReplayCommandSchema.safeParse({ operation: 'apply', threshold: 0.7 }).success)
      .toBe(false)
    expect(ReplayCommandSchema.safeParse({ operation: 'arm' }).success).toBe(false)
  })

  it('reports an unreachable engine as unavailable rather than as an empty backtest', () => {
    const offline = emptyReplayState('ต่อเอ็นจิ้นไม่ได้')
    expect(offline.available).toBe(false)
    expect(offline.summary).toBeNull()
    expect(offline.percent).toBeNull()
  })
})

describe('replay formatting', () => {
  it('always states the horizon a platform measured', () => {
    expect(replayTallyLine(ReplayTallySchema.parse(tally()))).toContain('5s')
    expect(replayTallyLine(ReplayTallySchema.parse(tally({ durationMs: 60_000 })))).toContain('60s')
  })

  it('never presents simulated money as a broker balance', () => {
    const line = replayMoneyLine(ReplayTallySchema.parse(tally()))
    expect(line).toContain('เงินจำลอง')
    expect(line).not.toContain('ยอดคงเหลือจริง')
    expect(replayMoneyLine(ReplayTallySchema.parse(tally({ moneyAvailable: false, currency: null }))))
      .toBe('ยังไม่ได้ตั้งค่าเงินจำลอง')
  })

  it('says plainly when a fold found nothing', () => {
    const nothing = ReplaySummarySchema.parse(summary()).walkForward?.rows[1]
    expect(nothing).toBeDefined()
    expect(replayFoldLine(nothing!)).toContain('ไม่พบเกณฑ์')
  })

  it('shows a latency scenario against the zero-delay baseline', () => {
    const rows = ReplaySummarySchema.parse(summary()).latency
    expect(replayLatencyLine(rows[0])).not.toContain('ต่างจาก 0ms')
    expect(replayLatencyLine(rows[1])).toContain('ต่างจาก 0ms')
  })

  it('renders an unknown share as a dash rather than as zero', () => {
    expect(replayPercentLabel(null)).toBe('—')
    expect(replayPercentLabel(0.5)).toBe('50.0%')
  })

  it('translates every warning code the engine can raise', () => {
    for (const codeName of ['INSUFFICIENT_HISTORY', 'NARROW_TIME_COVERAGE', 'MONETARY_UNVERIFIED',
      'SYNTHETIC_BEHAVIOR_TEST', 'NO_STABLE_CANDIDATE', 'ASSET_CONCENTRATION_WARNING'])
      expect(replayWarningLabel(codeName)).not.toBe(codeName)
  })
})

describe('replay panel', () => {
  it('renders the dataset, both platforms, coverage, folds and latency', () => {
    const markup = renderToStaticMarkup(createElement(ReplayResult, { state: state() }))
    expect(markup).toContain('PHASE4_MARKET_OBSERVATION')
    expect(markup).toContain('CapitalBear')
    expect(markup).toContain('IQ Option')
    expect(markup).toContain('SIMULATION ONLY')
    expect(markup).toContain(REPLAY_NOT_APPLIED_NOTICE)
  })

  it('shows the data-quality context before any performance number', () => {
    const markup = renderToStaticMarkup(createElement(ReplayResult, { state: state() }))
    expect(markup.indexOf('ช่องว่างข้อมูล')).toBeLessThan(markup.indexOf('ผลฐาน'))
    expect(markup).toContain('ไม่ได้เติมค่าให้')
  })

  it('renders nothing rather than an empty backtest when there is no result', () => {
    const markup = renderToStaticMarkup(
      createElement(ReplayResult, { state: emptyReplayState() }))
    expect(markup).toContain('ยังไม่มีผลการจำลอง')
    expect(markup).not.toContain('ผลฐาน')
  })

  it('carries the not-applied notice on the collapsed panel itself', () => {
    const markup = renderToStaticMarkup(createElement(ReplayPanel))
    expect(markup).toContain(REPLAY_NOT_APPLIED_NOTICE)
  })

  it('has no control anywhere that could apply a finding or reach a broker', () => {
    // Scanned as code rather than as text, so the paragraph explaining that nothing is applied
    // cannot pass the check for a real button.
    const panel = code('src/renderer/src/components/ReplayPanel.tsx')
    // Whole words, so `warmup` cannot be read as `arm` — a check that cannot tell a warm-up
    // from an arm state is a check that would have to be switched off the first time it fired.
    for (const forbidden of ['apply', 'arm', 'armed', 'disarm', 'higher', 'lower', 'press',
      'order', 'execution', 'stake', 'wallet', 'balance'])
      expect(panel.toLowerCase()).not.toMatch(new RegExp(`\\b${forbidden}\\b`))
    expect(panel).toContain("operation: 'start'")
    expect(panel).toContain("operation: 'cancel'")
  })

  it('keeps the replay bridge separate from the one that can press a control', () => {
    const preload = code('electron/preload/index.ts')
    expect(preload).toContain('ReplayCommandSchema.parse')
    // The replay channel and the execution channel are different names on different schemas.
    expect(IPC_CHANNELS.replay).not.toBe(IPC_CHANNELS.execution)
  })
})
