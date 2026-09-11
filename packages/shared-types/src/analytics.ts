import { z } from 'zod'
import { paperMoneyLabel } from './paper'

// Declared here rather than imported from the barrel: the barrel re-exports this module, and a
// cycle through it leaves these undefined while this file is still evaluating.
const PlatformSchema = z.enum(['capitalbear', 'iqoption'])

/**
 * Read-only Phase 10 research. The desktop renders an analysis the engine produced from its own
 * durable record; it computes no metric, fits nothing, and — most importantly — has no way to
 * apply anything it displays.
 *
 * There is deliberately **no** command channel in this module and no "Apply threshold" anywhere
 * in the panel that consumes it. A candidate threshold is an observation about outcomes that
 * have already happened. Turning one into live behaviour is Phase 12's problem, and until then
 * the honest thing for a research panel to be is unable to act.
 *
 * Two words are used carefully, for the same reason the engine uses them carefully. A bin's win
 * rate is an *empirical outcome curve*: `rankScore` and `ensembleConfidence` were produced by
 * layers that had never observed an outcome, so neither has ever claimed to be a probability
 * and neither is being graded as one.
 */
export const SampleLabelSchema = z.enum(['OK', 'LOW_SAMPLE', 'INSUFFICIENT_SAMPLE'])
export type SampleLabel = z.infer<typeof SampleLabelSchema>

export const StabilitySchema = z.enum(['STABLE', 'UNSTABLE', 'UNTESTED'])
export type Stability = z.infer<typeof StabilitySchema>

export const OutcomeMetricsSchema = z.looseObject({
  resolved: z.number().int().nonnegative(),
  wins: z.number().int().nonnegative(),
  losses: z.number().int().nonnegative(),
  draws: z.number().int().nonnegative(),
  winRateExcludingDraws: z.number().min(0).max(1).nullable(),
  winRateIncludingDraws: z.number().min(0).max(1).nullable(),
  drawRate: z.number().min(0).max(1).nullable(),
  /** Wilson bounds. Present whenever there was at least one win or loss to bound. */
  lower95: z.number().min(0).max(1).nullable(),
  upper95: z.number().min(0).max(1).nullable(),
  averagePriceDeltaBps: z.number().nullable(),
  medianPriceDeltaBps: z.number().nullable(),
  sampleCount: z.number().int().nonnegative(),
  sampleLabel: SampleLabelSchema
})
export type OutcomeMetrics = z.infer<typeof OutcomeMetricsSchema>

export const MoneyMetricsSchema = z.looseObject({
  /** False whenever no stake and payout rate were configured. Never rendered as zero. */
  available: z.boolean(),
  monetaryTrades: z.number().int().nonnegative(),
  currency: z.string().max(8).nullable(),
  grossProfit: z.number().nullable(),
  grossLoss: z.number().nullable(),
  netPaperPnl: z.number().nullable(),
  profitFactor: z.number().nullable(),
  expectancyPerTrade: z.number().nullable()
})
export type MoneyMetrics = z.infer<typeof MoneyMetricsSchema>

export const ScoreBinSchema = z.looseObject({
  index: z.number().int().nonnegative(),
  label: z.string().min(1).max(32),
  scoreMean: z.number().nullable(),
  outcomes: OutcomeMetricsSchema,
  money: MoneyMetricsSchema
})
export type ScoreBin = z.infer<typeof ScoreBinSchema>

export const CorrelationSchema = z.looseObject({
  metric: z.string().min(1).max(48),
  coefficient: z.number().min(-1).max(1).nullable(),
  sampleCount: z.number().int().nonnegative(),
  drawsExcluded: z.number().int().nonnegative(),
  strength: z.enum(['NONE', 'NEGLIGIBLE', 'WEAK', 'MODERATE', 'STRONG'])
})
export type Correlation = z.infer<typeof CorrelationSchema>

export const CalibrationSchema = z.looseObject({
  metric: z.string().min(1).max(48),
  bins: z.array(ScoreBinSchema).max(50),
  correlation: CorrelationSchema,
  monotonic: z.boolean(),
  monotonicityCoefficient: z.number().min(-1).max(1).nullable(),
  sampleCount: z.number().int().nonnegative(),
  sampleLabel: SampleLabelSchema,
  warnings: z.array(z.string()).max(24)
})
export type Calibration = z.infer<typeof CalibrationSchema>

export const SegmentMetricsSchema = z.looseObject({
  key: z.string().min(1).max(160),
  label: z.string().min(1).max(160),
  platform: PlatformSchema.nullable(),
  outcomes: OutcomeMetricsSchema,
  money: MoneyMetricsSchema,
  averageRankScore: z.number().min(0).max(1).nullable(),
  averageConfidence: z.number().min(0).max(1).nullable(),
  /** Whether the segment cleared its own sample floor. Low-N rows are shown, never ranked. */
  rankable: z.boolean()
})
export type SegmentMetrics = z.infer<typeof SegmentMetricsSchema>

export const MatrixCellSchema = z.looseObject({
  row: z.string().min(1).max(80),
  column: z.string().min(1).max(80),
  samples: z.number().int().nonnegative(),
  agreed: z.number().int().nonnegative(),
  outcomes: OutcomeMetricsSchema,
  sampleLabel: SampleLabelSchema
})
export type MatrixCell = z.infer<typeof MatrixCellSchema>

export const MatrixSchema = z.looseObject({
  name: z.string().min(1).max(64),
  rows: z.array(z.string()).max(64),
  columns: z.array(z.string()).max(64),
  cells: z.array(MatrixCellSchema).max(512),
  sampleCount: z.number().int().nonnegative()
})
export type Matrix = z.infer<typeof MatrixSchema>

export const SplitMetricsSchema = z.looseObject({
  split: z.enum(['TRAIN', 'VALIDATION', 'TEST']),
  total: z.number().int().nonnegative(),
  count: z.number().int().nonnegative(),
  coverage: z.number().min(0).max(1).nullable(),
  outcomes: OutcomeMetricsSchema,
  baselineWinRate: z.number().min(0).max(1).nullable(),
  lift: z.number().nullable()
})
export type SplitMetrics = z.infer<typeof SplitMetricsSchema>

export const ThresholdCandidateSchema = z.looseObject({
  metric: z.string().min(1).max(48),
  operator: z.literal('>='),
  threshold: z.number().min(0).max(1),
  train: SplitMetricsSchema,
  validation: SplitMetricsSchema,
  test: SplitMetricsSchema,
  stable: z.boolean(),
  stability: StabilitySchema,
  reasons: z.array(z.string()).max(24),
  /** A literal on the wire too, so a panel cannot render one as if it were in force. */
  appliedToLiveExecution: z.literal(false)
})
export type ThresholdCandidate = z.infer<typeof ThresholdCandidateSchema>

export const AnalyticsQualitySchema = z.looseObject({
  totalTrades: z.number().int().nonnegative(),
  eligibleTrades: z.number().int().nonnegative(),
  resolved: z.number().int().nonnegative(),
  invalid: z.number().int().nonnegative(),
  cancelled: z.number().int().nonnegative(),
  pendingEntry: z.number().int().nonnegative(),
  open: z.number().int().nonnegative(),
  unsupportedVersions: z.number().int().nonnegative(),
  /** Resolved over eligible. The caveat every win rate below has to be read against. */
  resolvedRate: z.number().min(0).max(1).nullable()
})
export type AnalyticsQuality = z.infer<typeof AnalyticsQualitySchema>

export const TemporalSplitSchema = z.looseObject({
  ordering: z.literal('CHRONOLOGICAL'),
  shuffled: z.literal(false),
  total: z.number().int().nonnegative(),
  train: z.number().int().nonnegative(),
  validation: z.number().int().nonnegative(),
  test: z.number().int().nonnegative()
})
export type TemporalSplit = z.infer<typeof TemporalSplitSchema>

/** What the desktop bridge delivers. `available` is added by the main process, never the engine. */
export const AnalyticsStateSchema = z.strictObject({
  analyticsVersion: z.string().min(1).max(40),
  available: z.boolean(),
  /** True while the engine is mid-ingest or rebuilding: a retry, never an empty result. */
  busy: z.boolean(),
  platform: PlatformSchema,
  sampleCount: z.number().int().nonnegative(),
  sampleLabel: SampleLabelSchema,
  timezone: z.string().min(1).max(64),
  quality: AnalyticsQualitySchema.nullable(),
  overall: OutcomeMetricsSchema.nullable(),
  money: MoneyMetricsSchema.nullable(),
  rank: CalibrationSchema.nullable(),
  confidence: CalibrationSchema.nullable(),
  regimes: z.array(SegmentMetricsSchema).max(16),
  assets: z.array(SegmentMetricsSchema).max(24),
  hours: z.array(SegmentMetricsSchema).max(24),
  strategyRegime: MatrixSchema.nullable(),
  thresholds: z.array(ThresholdCandidateSchema).max(8),
  split: TemporalSplitSchema.nullable(),
  warnings: z.array(z.string()).max(24)
})
export type AnalyticsState = z.infer<typeof AnalyticsStateSchema>

/**
 * What the panel shows before the first read returns, and whenever one cannot be made.
 *
 * Every table is empty rather than zeroed. A zero win rate over zero trades reads as a losing
 * system; "no data yet" reads as no data yet, which is what it is.
 */
export function emptyAnalyticsState(
  platform: 'capitalbear' | 'iqoption', busy = false
): AnalyticsState {
  return {
    analyticsVersion: 'unknown', available: false, busy, platform, sampleCount: 0,
    sampleLabel: 'INSUFFICIENT_SAMPLE', timezone: 'Asia/Bangkok', quality: null, overall: null,
    money: null, rank: null, confidence: null, regimes: [], assets: [], hours: [],
    strategyRegime: null, thresholds: [], split: null, warnings: []
  }
}

const WARNINGS: Record<string, string> = {
  INSUFFICIENT_SAMPLE: 'ตัวอย่างยังน้อยเกินกว่าจะสรุปอะไรได้',
  LOW_RESOLUTION_RATE: 'สัดส่วนไม้ที่รู้ผลต่ำ — ตัวเลขข้างล่างมาจากส่วนน้อยของสัญญาณทั้งหมด',
  LOW_SAMPLE_SEGMENTS: 'บางกลุ่มมีตัวอย่างน้อย (ยังแสดงไว้ แต่อย่าใช้สรุป)',
  NON_MONOTONIC_RANK_SCORE: 'คะแนน rankScore ยังไม่สัมพันธ์กับผลจริงแบบไปทางเดียวกัน',
  NON_MONOTONIC_CONFIDENCE: 'ค่า ensembleConfidence ยังไม่สัมพันธ์กับผลจริงแบบไปทางเดียวกัน',
  INVERSE_RANK_SCORE: 'rankScore สัมพันธ์กับผลจริง “กลับทาง” — คะแนนสูงกลับแย่กว่า',
  INVERSE_CONFIDENCE: 'ensembleConfidence สัมพันธ์กับผลจริง “กลับทาง” — มั่นใจสูงกลับแย่กว่า',
  THRESHOLD_UNSTABLE: 'เกณฑ์ที่ค้นเจอไม่นิ่งข้ามช่วงเวลา',
  MULTIPLE_TESTING_WARNING: 'ค้นหลายเกณฑ์พร้อมกัน — ตัวที่ดูดีที่สุดอาจดีเพราะบังเอิญ',
  VERSION_MIXED: 'มีข้อมูลหลายเวอร์ชัน — ตัวที่ไม่รองรับถูกแยกออกแล้ว',
  PAPER_ACCOUNTING_UNAVAILABLE: 'ยังไม่ได้ตั้งค่าเงินจำลอง จึงไม่มีตัวเลขกำไร/ขาดทุน',
  NO_STRATEGY_EVIDENCE: 'ยังไม่มีคะแนนโหวตรายกลยุทธ์ที่จับคู่กับผลได้',
  SINGLE_PLATFORM: 'มีข้อมูลแค่แพลตฟอร์มเดียวในมุมมองนี้'
}

const SAMPLE_NOTES: Record<SampleLabel, string> = {
  OK: '',
  LOW_SAMPLE: 'ตัวอย่างน้อย',
  INSUFFICIENT_SAMPLE: 'ยังไม่มีข้อมูลพอ'
}

const STABILITY: Record<Stability, string> = {
  STABLE: 'นิ่งข้ามช่วงเวลา',
  UNSTABLE: 'ไม่นิ่ง — ใช้ไม่ได้',
  UNTESTED: 'ยังทดสอบนอกช่วงไม่ได้'
}

export function warningLabel(code: string): string {
  return WARNINGS[code] ?? code
}

export function sampleNote(label: SampleLabel): string {
  return SAMPLE_NOTES[label]
}

export function stabilityLabel(stability: Stability): string {
  return STABILITY[stability]
}

export function percentLabel(value: number | null): string {
  return value === null ? '—' : `${(value * 100).toFixed(1)}%`
}

/**
 * A win rate with its interval attached, never on its own.
 *
 * The point is the second half. "57.9%" over thirty outcomes and "57.9%" over three thousand
 * are different statements, and only the interval says which one is on screen.
 */
export function winRateLabel(metrics: OutcomeMetrics | null): string {
  if (!metrics || metrics.winRateExcludingDraws === null) return 'ยังไม่มีผล'
  const range = metrics.lower95 === null || metrics.upper95 === null
    ? '' : ` (ช่วง ${percentLabel(metrics.lower95)}–${percentLabel(metrics.upper95)})`
  return `${percentLabel(metrics.winRateExcludingDraws)}${range}`
}

/** The tally, with the caveat that a draw is neither a win nor a loss stated in the numbers. */
export function outcomeLine(metrics: OutcomeMetrics | null): string {
  if (!metrics || metrics.resolved === 0) return 'ยังไม่มีไม้ที่รู้ผล'
  return `รู้ผล ${metrics.resolved} · ถูก ${metrics.wins} · ผิด ${metrics.losses} · ` +
    `เสมอ ${metrics.draws} · สัดส่วนถูก (ไม่นับเสมอ) ${winRateLabel(metrics)}`
}

export function moneyLine(money: MoneyMetrics | null): string {
  if (!money || !money.available) return 'ยังไม่ได้ตั้งค่าเงินจำลอง'
  const factor = money.profitFactor === null ? '—' : money.profitFactor.toFixed(2)
  return `${paperMoneyLabel(money.netPaperPnl, money.currency)} · profit factor ${factor} · ` +
    `เฉลี่ยต่อไม้ ${paperMoneyLabel(money.expectancyPerTrade, money.currency)} · ` +
    `นับเงินได้ ${money.monetaryTrades} ไม้`
}

/** One calibration band. `n` first, because the count is what qualifies the rate. */
export function binLine(bin: ScoreBin): string {
  const rate = bin.outcomes.winRateExcludingDraws === null
    ? '—' : percentLabel(bin.outcomes.winRateExcludingDraws)
  const note = sampleNote(bin.outcomes.sampleLabel)
  return `${bin.label}  n=${bin.outcomes.resolved}  ${rate}${note ? ` · ${note}` : ''}`
}

/**
 * What the curve as a whole says — including when it says the score does not work.
 *
 * A layer that could only describe a score that works would be a layer that quietly
 * reinterprets its metric until the metric looks good.
 */
export function calibrationVerdict(calibration: Calibration | null): string {
  if (!calibration || calibration.correlation.coefficient === null)
    return 'ยังวัดความสัมพันธ์ไม่ได้ (ข้อมูลน้อยหรือคะแนนไม่กระจาย)'
  const value = calibration.correlation.coefficient
  const direction = value < 0 ? 'กลับทาง' : 'ไปทางเดียวกัน'
  return `ความสัมพันธ์กับผลจริง ${value.toFixed(2)} (${direction}) · ` +
    `n=${calibration.correlation.sampleCount} · ไม่นับเสมอ ${calibration.correlation.drawsExcluded} ไม้`
}

export function segmentLine(segment: SegmentMetrics): string {
  const note = segment.rankable ? '' : ` · ${sampleNote(segment.outcomes.sampleLabel) || 'ตัวอย่างน้อย'}`
  const score = segment.averageRankScore === null ? '—' : segment.averageRankScore.toFixed(2)
  return `${segment.label} · n=${segment.outcomes.resolved} · ${winRateLabel(segment.outcomes)} · ` +
    `คะแนนเฉลี่ย ${score}${note}`
}

/** One research candidate, worded so it can never be read as a setting that is in force. */
export function thresholdLine(candidate: ThresholdCandidate): string {
  const period = (metrics: SplitMetrics): string =>
    `n=${metrics.count} ${percentLabel(metrics.outcomes.winRateExcludingDraws)}`
  return `${candidate.metric} ${candidate.operator} ${candidate.threshold.toFixed(2)} · ` +
    `ฝึก ${period(candidate.train)} · ตรวจ ${period(candidate.validation)} · ` +
    `ทดสอบ ${period(candidate.test)} · ครอบคลุม ${percentLabel(candidate.train.coverage)} · ` +
    stabilityLabel(candidate.stability)
}

/** The one sentence that must appear wherever a threshold does. */
export const NOT_APPLIED_NOTICE =
  'เป็นผลวิจัยจากข้อมูลย้อนหลังเท่านั้น — ยังไม่ได้ถูกนำไปใช้กับการเทรดจริง และเฟสนี้ไม่มีปุ่มให้นำไปใช้'
