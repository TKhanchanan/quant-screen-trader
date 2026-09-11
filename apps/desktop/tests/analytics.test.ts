import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import {
  IPC_CHANNELS, AnalyticsStateSchema, CalibrationSchema, OutcomeMetricsSchema,
  MoneyMetricsSchema, ScoreBinSchema, SegmentMetricsSchema,
  ThresholdCandidateSchema, NOT_APPLIED_NOTICE, binLine, calibrationVerdict,
  emptyAnalyticsState, moneyLine, outcomeLine, percentLabel, segmentLine, stabilityLabel,
  thresholdLine, warningLabel, winRateLabel,
  type AnalyticsState
} from '@quant-screen-trader/shared-types'
import { AnalyticsPanel, AnalyticsTables } from '../src/renderer/src/components/AnalyticsPanel'

const outcomes = (changes: Record<string, unknown> = {}): Record<string, unknown> => ({
  resolved: 240, wins: 138, losses: 96, draws: 6, winRateExcludingDraws: 138 / 234,
  winRateIncludingDraws: 138 / 240, drawRate: 6 / 240, lower95: 0.526, upper95: 0.653,
  averagePriceDeltaBps: 1.4, medianPriceDeltaBps: 1.1, sampleCount: 240, sampleLabel: 'OK',
  ...changes
})
const money = (changes: Record<string, unknown> = {}): Record<string, unknown> => ({
  available: true, monetaryTrades: 240, currency: 'THB', grossProfit: 4416, grossLoss: 4800,
  netPaperPnl: -384, profitFactor: 0.92, expectancyPerTrade: -1.6, ...changes
})
const bin = (index: number, label: string, resolved: number, rate: number): Record<string, unknown> => ({
  index, label, scoreMean: 0.05 + index * 0.1,
  outcomes: outcomes({ resolved, wins: Math.round(resolved * rate), losses: resolved - Math.round(resolved * rate),
    draws: 0, winRateExcludingDraws: rate, sampleCount: resolved,
    sampleLabel: resolved >= 20 ? 'OK' : 'LOW_SAMPLE' }),
  money: money()
})
const calibration = (changes: Record<string, unknown> = {}): Record<string, unknown> => ({
  metric: 'rankScore',
  bins: [bin(4, '[0.40, 0.50)', 200, 0.49), bin(5, '[0.50, 0.60)', 340, 0.55),
    bin(6, '[0.60, 0.70)', 290, 0.61), bin(7, '[0.70, 0.80)', 110, 0.64)],
  correlation: { metric: 'rankScore', coefficient: 0.31, sampleCount: 934, drawsExcluded: 6,
    strength: 'MODERATE' },
  monotonic: true, monotonicityCoefficient: 1, sampleCount: 940, sampleLabel: 'OK', warnings: [],
  ...changes
})
const segment = (key: string, rate: number, resolved: number, rankable = true): Record<string, unknown> => ({
  key, label: key, platform: 'capitalbear',
  outcomes: outcomes({ resolved, sampleCount: resolved, winRateExcludingDraws: rate,
    lower95: Math.max(0, rate - 0.06), upper95: Math.min(1, rate + 0.06),
    sampleLabel: rankable ? 'OK' : 'LOW_SAMPLE' }),
  money: money(), averageRankScore: 0.58, averageConfidence: 0.61, rankable
})
const split = (name: string, count: number, rate: number): Record<string, unknown> => ({
  split: name, total: Math.round(count / 0.43), count, coverage: 0.43,
  outcomes: outcomes({ resolved: count, sampleCount: count, winRateExcludingDraws: rate }),
  baselineWinRate: 0.52, lift: rate - 0.52
})
const candidate = (changes: Record<string, unknown> = {}): Record<string, unknown> => ({
  metric: 'rankScore', operator: '>=', threshold: 0.61,
  train: split('TRAIN', 520, 0.59), validation: split('VALIDATION', 160, 0.58),
  test: split('TEST', 150, 0.57), stable: true, stability: 'STABLE',
  reasons: ['CONSISTENT_ACROSS_SPLITS'], appliedToLiveExecution: false, ...changes
})
const state = (changes: Record<string, unknown> = {}): AnalyticsState =>
  AnalyticsStateSchema.parse({
    analyticsVersion: 'qst-analytics-v1', available: true, busy: false, platform: 'capitalbear',
    sampleCount: 940, sampleLabel: 'OK', timezone: 'Asia/Bangkok',
    quality: { totalTrades: 1200, eligibleTrades: 1180, resolved: 940, invalid: 30, cancelled: 20,
      pendingEntry: 40, open: 150, unsupportedVersions: 20, resolvedRate: 940 / 1180 },
    overall: outcomes({ resolved: 940, sampleCount: 940 }), money: money(),
    rank: calibration(), confidence: calibration({ metric: 'ensembleConfidence' }),
    regimes: [segment('TREND_UP', 0.624, 180), segment('RANGE', 0.511, 220),
      segment('NOISY', 0.42, 90)],
    assets: [segment('capitalbear|EUR/USD OTC', 0.61, 300),
      segment('capitalbear|Gold OTC', 0.44, 220),
      segment('capitalbear|Thin OTC', 0.95, 4, false)],
    hours: [segment('09', 0.58, 120), segment('10', 0.49, 140)],
    strategyRegime: { name: 'strategyRegime', rows: ['trend_follow_v1'],
      columns: ['TREND_UP', 'NOISY'],
      cells: [
        { row: 'trend_follow_v1', column: 'TREND_UP', samples: 180, agreed: 150,
          outcomes: outcomes({ resolved: 150, sampleCount: 150, winRateExcludingDraws: 0.66 }),
          sampleLabel: 'OK' },
        { row: 'trend_follow_v1', column: 'NOISY', samples: 90, agreed: 4,
          outcomes: outcomes({ resolved: 4, sampleCount: 4, winRateExcludingDraws: 0.25 }),
          sampleLabel: 'LOW_SAMPLE' }
      ], sampleCount: 270 },
    thresholds: [candidate()],
    split: { ordering: 'CHRONOLOGICAL', shuffled: false, total: 940, train: 564, validation: 188,
      test: 188 },
    warnings: ['MULTIPLE_TESTING_WARNING'],
    ...changes
  })
const source = (file: string): string => readFileSync(join(import.meta.dirname, '..', file), 'utf8')
/**
 * The same file with its comments removed.
 *
 * The boundary checks below scan what the code does, not what it says about itself. Prose
 * explaining that nothing is applied must not fail the check, and prose must not be able to
 * hide a real one either.
 */
const code = (file: string): string =>
  source(file).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('analytics bridge', () => {
  it('accepts an engine snapshot and tolerates fields the desktop does not render', () => {
    const parsed = state()
    expect(parsed.overall?.wins).toBe(138)
    expect(parsed.rank?.bins).toHaveLength(4)
    expect(IPC_CHANNELS.analytics).toBe('analytics:state')
    expect(CalibrationSchema.safeParse({ ...calibration(), extraDiagnostic: 7 }).success).toBe(true)
  })

  it('rejects a malformed analysis rather than rendering an invented number', () => {
    expect(OutcomeMetricsSchema.safeParse(outcomes({ winRateExcludingDraws: 1.4 })).success).toBe(false)
    expect(OutcomeMetricsSchema.safeParse(outcomes({ wins: -1 })).success).toBe(false)
    expect(OutcomeMetricsSchema.safeParse(outcomes({ sampleLabel: 'PLENTY' })).success).toBe(false)
    expect(AnalyticsStateSchema.safeParse({ ...state(), platform: 'binance' }).success).toBe(false)
  })

  it('refuses a threshold that claims to be in force', () => {
    // The literal is the point. A panel must not be able to render a research candidate as if
    // it were a setting, and the wire type will not carry one that says it is.
    expect(ThresholdCandidateSchema.safeParse(candidate()).success).toBe(true)
    expect(ThresholdCandidateSchema.safeParse(
      candidate({ appliedToLiveExecution: true })).success).toBe(false)
    expect(ThresholdCandidateSchema.safeParse(candidate({ operator: '<=' })).success).toBe(false)
  })

  it('starts empty rather than zeroed before the first read returns', () => {
    const empty = emptyAnalyticsState('iqoption')
    expect(empty.available).toBe(false)
    expect(empty.overall).toBeNull()
    expect(empty.money).toBeNull()
    expect(empty.sampleCount).toBe(0)
    expect(empty.thresholds).toEqual([])
    // A zero win rate over zero trades would read as a losing system.
    expect(outcomeLine(empty.overall)).toBe('ยังไม่มีไม้ที่รู้ผล')
  })
})

describe('analytics labels', () => {
  it('never states a win rate without the interval around it', () => {
    const line = winRateLabel(OutcomeMetricsSchema.parse(outcomes()))
    expect(line).toContain('59.0%')
    expect(line).toContain('52.6%')
    expect(line).toContain('65.3%')
    expect(winRateLabel(null)).toBe('ยังไม่มีผล')
    expect(winRateLabel(OutcomeMetricsSchema.parse(
      outcomes({ winRateExcludingDraws: null })))).toBe('ยังไม่มีผล')
  })

  it('puts the sample size before the rate on every calibration band', () => {
    const line = binLine(CalibrationSchema.parse(calibration()).bins[1]!)
    expect(line.indexOf('n=340')).toBeLessThan(line.indexOf('55.0%'))
    expect(line).toContain('[0.50, 0.60)')
  })

  it('marks a thin band rather than hiding it', () => {
    const thin = binLine(ScoreBinSchema.parse(bin(9, '[0.90, 1.00]', 4, 1)))
    expect(thin).toContain('n=4')
    expect(thin).toContain('ตัวอย่างน้อย')
  })

  it('says plainly when a score is related to outcomes the wrong way round', () => {
    const inverted = CalibrationSchema.parse(calibration({
      correlation: { metric: 'ensembleConfidence', coefficient: -0.28, sampleCount: 400,
        drawsExcluded: 5, strength: 'WEAK' }
    }))
    expect(calibrationVerdict(inverted)).toContain('กลับทาง')
    expect(warningLabel('INVERSE_CONFIDENCE')).toContain('กลับทาง')
    expect(warningLabel('NON_MONOTONIC_RANK_SCORE')).toContain('rankScore')
    // An unknown code is shown as itself rather than swallowed.
    expect(warningLabel('SOMETHING_NEW')).toBe('SOMETHING_NEW')
  })

  it('never renders unpriced simulation as zero money', () => {
    expect(moneyLine(null)).toBe('ยังไม่ได้ตั้งค่าเงินจำลอง')
    expect(moneyLine(MoneyMetricsSchema.parse(money({ available: false }))))
      .toBe('ยังไม่ได้ตั้งค่าเงินจำลอง')
    expect(moneyLine(MoneyMetricsSchema.parse(money()))).toContain('profit factor 0.92')
  })

  it('states a threshold as a research observation across all three periods', () => {
    const line = thresholdLine(ThresholdCandidateSchema.parse(candidate()))
    expect(line).toContain('rankScore >= 0.61')
    expect(line).toContain('ฝึก n=520')
    expect(line).toContain('ตรวจ n=160')
    expect(line).toContain('ทดสอบ n=150')
    expect(line).toContain('ครอบคลุม 43.0%')
    expect(stabilityLabel('UNSTABLE')).toContain('ใช้ไม่ได้')
  })

  it('marks a segment that has not earned a ranking', () => {
    expect(segmentLine(SegmentMetricsSchema.parse(segment('capitalbear|Thin OTC', 0.95, 4, false))))
      .toContain('ตัวอย่างน้อย')
    expect(percentLabel(null)).toBe('—')
  })
})

describe('analytics panel', () => {
  const markup = (): string =>
    renderToStaticMarkup(createElement(AnalyticsPanel, { platform: 'capitalbear' }))

  it('renders as research and says so before anything else', () => {
    const html = markup()
    expect(html).toContain('วิเคราะห์ผลย้อนหลัง (Analytics / Calibration)')
    expect(html).toContain(NOT_APPLIED_NOTICE)
    expect(html).toContain('ยังไม่ได้ถูกนำไปใช้กับการเทรดจริง')
  })

  it('offers no way to apply anything it finds', () => {
    const text = code('src/renderer/src/components/AnalyticsPanel.tsx')
    // Phase 10 measures. There is no Apply button, and no bridge method that would accept one.
    expect(text).not.toMatch(/Apply|apply/)
    expect(text).not.toMatch(/<button|onClick|onSubmit/)
    expect(text).not.toMatch(/execution\(|sessionGuard\(|arm|disarm|testControls/)
    expect(text).not.toMatch(/sendInputEvent|pressPoint|OrderExecutor|ExecutionManager/)
    // It reads one bridge method, and that method is a read.
    expect(text.match(/window\.quantScreenTrader\.\w+/g)).toEqual(['window.quantScreenTrader.analytics'])
  })

  it('shows nothing rather than a zero before the first read returns', () => {
    const html = markup()
    expect(html).toContain('ยังไม่ได้อ่านผลวิเคราะห์')
    expect(html).not.toContain('0.0%')
    for (const empty of ['ยังไม่มีไม้ที่รู้ผล', 'ยังไม่ได้ตั้งค่าเงินจำลอง',
      'ยังไม่มีผลแยกตามสภาพตลาด', 'ยังไม่มีสินทรัพย์ไหนมีตัวอย่างพอจะจัดอันดับ',
      'ยังไม่มีเกณฑ์ที่ผ่านเงื่อนไขตัวอย่างขั้นต่ำ'])
      expect(html).toContain(empty)
  })

  it('renders every research table the phase requires', () => {
    const html = renderToStaticMarkup(createElement(AnalyticsTables, { state: state() }))
    for (const heading of ['ภาพรวม', 'คะแนน rankScore เทียบผลจริง',
      'ความมั่นใจ ensembleConfidence เทียบผลจริง', 'ตามสภาพตลาด (Regime)',
      'กลยุทธ์ × สภาพตลาด', 'ตามสินทรัพย์', 'ตามชั่วโมง', 'เกณฑ์ที่ค้นเจอ'])
      expect(html).toContain(heading)
    // Overall: the tally, the interval and the resolution rate that qualifies it.
    expect(html).toContain('รู้ผล 940')
    expect(html).toContain('สัดส่วนที่รู้ผล 79.7%')
    // Calibration bands, sample first.
    expect(html).toContain('[0.60, 0.70)')
    expect(html).toContain('n=290')
    // Regime table and the strategy matrix cell.
    expect(html).toContain('TREND_UP')
    expect(html).toContain('150 / 66.0%')
    // The hour table names its timezone rather than assuming the reader's.
    expect(html).toContain('ตามชั่วโมง (Asia/Bangkok)')
    // The threshold is stated across all three periods and marked as research.
    expect(html).toContain('rankScore &gt;= 0.61')
    expect(html).toContain('ทดสอบ n=150')
    expect(html).toContain(NOT_APPLIED_NOTICE)
  })

  it('never presents a low-sample slice as a finding', () => {
    const html = renderToStaticMarkup(createElement(AnalyticsTables, { state: state() }))
    // The four-trade asset is not offered as the best one, and the four-vote matrix cell is
    // dimmed rather than read as a 25% strategy.
    expect(html).not.toContain('Thin OTC')
    expect(html).toContain('low-sample')
  })

  it('keeps an unstable threshold visibly separate from a stable one', () => {
    const unstable = renderToStaticMarkup(createElement(AnalyticsTables, {
      state: state({ thresholds: [candidate({ stable: false, stability: 'UNSTABLE',
        reasons: ['DIRECTION_NOT_CONSISTENT', 'WEAK_IN_TEST'] })] })
    }))
    expect(unstable).toContain('threshold-unstable')
    expect(unstable).toContain('ไม่นิ่ง')
    expect(unstable).toContain('WEAK_IN_TEST')
  })

  it('surfaces a score that does not work instead of hiding it', () => {
    const html = renderToStaticMarkup(createElement(AnalyticsTables, {
      state: state({
        warnings: ['NON_MONOTONIC_RANK_SCORE', 'INVERSE_CONFIDENCE'],
        confidence: calibration({ metric: 'ensembleConfidence',
          warnings: ['INVERSE_CONFIDENCE'],
          correlation: { metric: 'ensembleConfidence', coefficient: -0.22, sampleCount: 900,
            drawsExcluded: 6, strength: 'WEAK' } })
      })
    }))
    expect(html).toContain(warningLabel('NON_MONOTONIC_RANK_SCORE'))
    expect(html).toContain(warningLabel('INVERSE_CONFIDENCE'))
    expect(html).toContain('กลับทาง')
  })

  it('keeps the execution layer unaware of the analytics layer', () => {
    // Phase 10 forms opinions about which threshold would have worked. The execution layer must
    // not be able to read one, or a research output becomes a live gate by accident.
    for (const file of ['electron/main/execution-manager.ts', 'electron/main/order-executor.ts',
      'electron/main/order-panel.ts']) {
      const text = source(file)
      expect(text).not.toMatch(/AnalyticsState|AnalyticsPanel|api\/analytics|thresholdCandidate/i)
      expect(text).not.toMatch(/IPC_CHANNELS\.analytics\b/)
    }
  })

  it('keeps the analytics bridge read-only in the main process', () => {
    const text = code('electron/main/index.ts')
    const handler = text.slice(text.indexOf('IPC_CHANNELS.analytics'))
    const body = handler.slice(0, handler.indexOf('ipcMain.handle(IPC_CHANNELS.getEngineHealth'))
    expect(body).toContain('/api/analytics/snapshot')
    expect(body).not.toMatch(/method:\s*'POST'/)
    expect(body).not.toMatch(/method:\s*'PUT'/)
    expect(body).not.toMatch(/method:\s*'DELETE'/)
  })
})
