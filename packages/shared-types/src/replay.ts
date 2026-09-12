import { z } from 'zod'
import { MoneyMetricsSchema, OutcomeMetricsSchema, StabilitySchema } from './analytics'

// Declared here rather than imported from the barrel: the barrel re-exports this module, and a
// cycle through it leaves this undefined while this file is still evaluating.
const PlatformSchema = z.enum(['capitalbear', 'iqoption'])

/**
 * Read-only Phase 11 research: what the frozen pipeline *would have decided* over recorded
 * history, and what those decisions would have produced.
 *
 * Two things this module deliberately does not contain. There is no "apply" of any kind — a
 * threshold a fold reports is an observation about outcomes that already happened, and turning
 * one into live behaviour is a later phase's decision. And there is no execution surface: a
 * replay is offline compute over a recorded file, so nothing here can reach an order, a broker
 * control, an arm state or a live session limit.
 *
 * The only commands are `start` and `cancel`, and both act on *offline compute*. Cancelling a
 * replay stops that replay; it does not stop capture and it does not touch the trading day.
 */
export const ReplayStatusSchema = z.enum(['PENDING', 'RUNNING', 'COMPLETED', 'CANCELLED', 'FAILED'])
export type ReplayStatus = z.infer<typeof ReplayStatusSchema>

export const ReplayCommandSchema = z.discriminatedUnion('operation', [
  z.object({
    operation: z.literal('start'),
    platform: PlatformSchema.nullable().default(null),
    /** Milliseconds of history to evaluate, counted back from the end of the record. */
    windowMs: z.number().int().positive().max(365 * 86_400_000).nullable().default(null),
    warmupMs: z.number().int().nonnegative().max(30 * 86_400_000).nullable().default(null),
    latencyScenarios: z.array(z.number().int().min(0).max(600_000)).max(8).default([])
  }),
  z.object({ operation: z.literal('state') }),
  z.object({ operation: z.literal('cancel') })
])
export type ReplayCommand = z.infer<typeof ReplayCommandSchema>

export const ReplayDatasetSchema = z.looseObject({
  entryLayer: z.string().max(48),
  sourceMode: z.enum(['REPLAY', 'SYNTHETIC']),
  inputFingerprint: z.string().max(64),
  events: z.number().int().nonnegative(),
  startTime: z.number().int().nullable(),
  endTime: z.number().int().nullable(),
  durationMs: z.number().int().nonnegative(),
  assetsSeen: z.number().int().nonnegative(),
  contextsSeen: z.number().int().nonnegative(),
  gapSeconds: z.number().int().nonnegative(),
  gapShare: z.number().min(0).max(1).nullable()
})
export type ReplayDataset = z.infer<typeof ReplayDatasetSchema>

export const ReplayTallySchema = z.looseObject({
  platform: PlatformSchema.nullable(),
  durationMs: z.number().int().nonnegative(),
  ensembles: z.number().int().nonnegative(),
  boardsFinalized: z.number().int().nonnegative(),
  boardsSelected: z.number().int().nonnegative(),
  selectionCoverage: z.number().min(0).max(1).nullable(),
  resolved: z.number().int().nonnegative(),
  wins: z.number().int().nonnegative(),
  losses: z.number().int().nonnegative(),
  draws: z.number().int().nonnegative(),
  invalid: z.number().int().nonnegative(),
  cancelled: z.number().int().nonnegative(),
  winRateExcludingDraws: z.number().min(0).max(1).nullable(),
  lower95: z.number().min(0).max(1).nullable(),
  upper95: z.number().min(0).max(1).nullable(),
  tradesPerHour: z.number().nonnegative().nullable(),
  maxWinStreak: z.number().int().nonnegative(),
  maxLossStreak: z.number().int().nonnegative(),
  moneyAvailable: z.boolean(),
  currency: z.string().max(8).nullable(),
  netPaperPnl: z.number().nullable(),
  profitFactor: z.number().nullable(),
  expectancyPerTrade: z.number().nullable(),
  /** Simulated paper drawdown. Never a broker balance, and never a fraction of an unstated one. */
  maxDrawdown: z.number().nonnegative().nullable(),
  maxDrawdownRelative: z.number().nonnegative().nullable()
})
export type ReplayTally = z.infer<typeof ReplayTallySchema>

export const ReplayFoldSchema = z.looseObject({
  foldId: z.number().int().positive(),
  trainStart: z.number().int(),
  trainEnd: z.number().int(),
  validationStart: z.number().int(),
  validationEnd: z.number().int(),
  testStart: z.number().int(),
  testEnd: z.number().int(),
  purgeMs: z.number().int().nonnegative(),
  embargoMs: z.number().int().nonnegative(),
  candidateMetric: z.string().max(48).nullable(),
  candidateThreshold: z.number().min(0).max(1).nullable(),
  directionalStable: z.boolean(),
  monetaryStable: z.boolean(),
  monetaryVerdict: StabilitySchema,
  test: z.looseObject({
    selected: z.number().int().nonnegative(),
    coverage: z.number().min(0).max(1).nullable(),
    winRateExcludingDraws: z.number().min(0).max(1).nullable(),
    lift: z.number().nullable(),
    expectancyPerTrade: z.number().nullable()
  }),
  warnings: z.array(z.string().max(64)).max(24)
})
export type ReplayFold = z.infer<typeof ReplayFoldSchema>

export const ReplayWalkForwardSchema = z.looseObject({
  mode: z.enum(['COUNT', 'DURATION']),
  folds: z.number().int().nonnegative(),
  foldsWithCandidate: z.number().int().nonnegative(),
  foldsDirectionalPositive: z.number().int().nonnegative(),
  foldsMonetaryPositive: z.number().int().nonnegative(),
  medianTestWinRate: z.number().min(0).max(1).nullable(),
  candidateStability: StabilitySchema,
  thresholdSpread: z.number().nonnegative().nullable(),
  rows: z.array(ReplayFoldSchema).max(64),
  warnings: z.array(z.string().max(64)).max(32)
})
export type ReplayWalkForward = z.infer<typeof ReplayWalkForwardSchema>

export const ReplayLatencySchema = z.looseObject({
  delayMs: z.number().int().nonnegative(),
  resolved: z.number().int().nonnegative(),
  winRateExcludingDraws: z.number().min(0).max(1).nullable(),
  expectancyPerTrade: z.number().nullable(),
  winRateDelta: z.number().nullable(),
  expectancyDelta: z.number().nullable()
})
export type ReplayLatency = z.infer<typeof ReplayLatencySchema>

export const ReplayCoverageSchema = z.looseObject({
  dominantRegime: z.string().max(40).nullable(),
  dominantRegimeShare: z.number().min(0).max(1).nullable(),
  topAsset: z.string().max(120).nullable(),
  topAssetShare: z.number().min(0).max(1).nullable(),
  hoursCovered: z.number().int().min(0).max(24),
  weekdaysCovered: z.number().int().min(0).max(7),
  tradingDates: z.number().int().nonnegative(),
  timezone: z.string().max(64)
})
export type ReplayCoverage = z.infer<typeof ReplayCoverageSchema>

export const ReplaySummarySchema = z.looseObject({
  replayRunId: z.string(),
  label: z.literal('CURRENT_FROZEN_PIPELINE'),
  dataset: ReplayDatasetSchema,
  causality: z.looseObject({ checks: z.number().int().nonnegative(), violations: z.number().int().nonnegative() }),
  overall: ReplayTallySchema,
  platforms: z.array(ReplayTallySchema).max(2),
  coverage: ReplayCoverageSchema,
  latency: z.array(ReplayLatencySchema).max(16),
  walkForward: ReplayWalkForwardSchema.nullable(),
  warnings: z.array(z.string().max(64)).max(32),
  researchOnly: z.literal(true),
  appliedToLiveExecution: z.literal(false)
})
export type ReplaySummary = z.infer<typeof ReplaySummarySchema>

export const ReplayStateSchema = z.object({
  replayVersion: z.string(),
  available: z.boolean(),
  busy: z.boolean(),
  jobId: z.string().nullable(),
  replayRunId: z.string().nullable(),
  status: ReplayStatusSchema,
  phase: z.string().max(40),
  totalEvents: z.number().int().nonnegative(),
  processedEvents: z.number().int().nonnegative(),
  percent: z.number().min(0).max(1).nullable(),
  currentMarketTime: z.number().int().nullable(),
  error: z.string().nullable(),
  summary: ReplaySummarySchema.nullable(),
  message: z.string()
})
export type ReplayState = z.infer<typeof ReplayStateSchema>

export function emptyReplayState(message = ''): ReplayState {
  return {
    replayVersion: 'unknown', available: false, busy: false, jobId: null, replayRunId: null,
    status: 'PENDING', phase: 'PENDING', totalEvents: 0, processedEvents: 0, percent: null,
    currentMarketTime: null, error: null, summary: null, message
  }
}

const REPLAY_WARNINGS: Record<string, string> = {
  INSUFFICIENT_HISTORY: 'ประวัติน้อยเกินกว่าจะสรุปผลได้ — ตัวเลขข้างล่างไม่ใช่หลักฐานเรื่องกำไร',
  LOW_RESOLUTION_RATE: 'สัดส่วนไม้ที่รู้ผลต่ำ — ตัวเลขมาจากส่วนน้อยของสัญญาณทั้งหมด',
  LOW_SELECTION_COUNT: 'จำนวนสัญญาณที่ถูกเลือกน้อยเกินไป',
  LOW_ASSET_SAMPLE: 'บางสินทรัพย์มีตัวอย่างน้อยเกินกว่าจะจัดอันดับได้',
  SINGLE_REGIME_DOMINANCE: 'ประวัติเกือบทั้งหมดอยู่ในสภาพตลาดแบบเดียว',
  LIMITED_REGIME_COVERAGE: 'เห็นสภาพตลาดไม่ครบ — ผลนี้ยังไม่ผ่านตลาดแบบอื่น',
  NARROW_TIME_COVERAGE: 'ช่วงเวลาที่ครอบคลุมแคบ (ชั่วโมง/วันน้อย) — ยังไม่ใช่ประวัติที่กว้าง',
  ASSET_CONCENTRATION_WARNING: 'สินทรัพย์เดียวกินสัดส่วนไม้ส่วนใหญ่',
  HIGH_GAP_RATE: 'ข้อมูลขาดหายเป็นสัดส่วนสูง — ช่องว่างถูกเก็บไว้ตามจริง ไม่ได้เติมค่าให้',
  MIXED_CURRENCY: 'มีหลายสกุลเงิน — นับเฉพาะสกุลหลัก ไม่มีการแปลงค่า',
  MONETARY_UNVERIFIED: 'ยังยืนยันความนิ่งของ “เงิน” ไม่ได้ — ดูได้เฉพาะทิศทาง',
  PAPER_ACCOUNTING_UNAVAILABLE: 'ยังไม่ได้ตั้งค่าเงินจำลอง จึงไม่มีตัวเลขกำไร/ขาดทุน',
  MULTIPLE_TESTING_WARNING: 'ค้นหลายเกณฑ์พร้อมกัน — ตัวที่ดูดีที่สุดอาจดีเพราะบังเอิญ',
  OUT_OF_SAMPLE_DEGRADATION: 'เกณฑ์ที่ดีในช่วงฝึก แย่ลงในช่วงทดสอบ',
  PARAMETER_INSTABILITY: 'เกณฑ์ที่ค้นเจอเปลี่ยนไปมาระหว่างช่วง — ไม่นิ่ง',
  INSUFFICIENT_FOLDS: 'จำนวนช่วง walk-forward น้อยเกินไป',
  NO_STABLE_CANDIDATE: 'ไม่พบเกณฑ์ที่นิ่งพอ — และไม่มีการลดเกณฑ์ขั้นต่ำเพื่อให้เจอ',
  SINGLE_PLATFORM: 'ผลนี้มาจากโบรกเกอร์เดียว',
  STRATEGY_EVIDENCE_TRUNCATED: 'คะแนนโหวตรายกลยุทธ์ถูกตัดตามขีดจำกัดหน่วยความจำ',
  SYNTHETIC_BEHAVIOR_TEST: 'ข้อมูลสังเคราะห์ — ใช้ทดสอบพฤติกรรมซอฟต์แวร์เท่านั้น ไม่ใช่ผลการเทรด',
  MALFORMED_INPUT_ROWS: 'มีแถวข้อมูลที่อ่านไม่ได้ — ถูกข้ามและนับไว้ ไม่ได้ซ่อมค่าให้',
  DUPLICATE_INPUT_ROWS: 'มีแถวซ้ำหรือชนกันในบันทึก — ถูกนับไว้แล้ว'
}

export function replayWarningLabel(code: string): string {
  return REPLAY_WARNINGS[code] ?? code
}

export function replayPercentLabel(value: number | null): string {
  return value === null ? '—' : `${(value * 100).toFixed(1)}%`
}

/** One platform's baseline line. The horizon is always stated: 5s and 60s are not one number. */
export function replayTallyLine(tally: ReplayTally): string {
  const horizon = tally.durationMs ? `${tally.durationMs / 1000}s` : 'รวม'
  const interval = tally.lower95 === null || tally.upper95 === null
    ? ''
    : ` (ช่วงมั่นใจ ${replayPercentLabel(tally.lower95)}–${replayPercentLabel(tally.upper95)})`
  return `${horizon} · บอร์ด ${tally.boardsFinalized} · เลือก ${tally.boardsSelected} · ` +
    `รู้ผล ${tally.resolved} · ช/พ/เสมอ ${tally.wins}/${tally.losses}/${tally.draws} · ` +
    `ชนะ ${replayPercentLabel(tally.winRateExcludingDraws)}${interval}`
}

export function replayMoneyLine(tally: ReplayTally): string {
  if (!tally.moneyAvailable || tally.currency === null) return 'ยังไม่ได้ตั้งค่าเงินจำลอง'
  const net = tally.netPaperPnl === null ? '—' : tally.netPaperPnl.toFixed(2)
  const factor = tally.profitFactor === null ? '—' : tally.profitFactor.toFixed(2)
  const expectancy = tally.expectancyPerTrade === null ? '—' : tally.expectancyPerTrade.toFixed(2)
  const drawdown = tally.maxDrawdown === null ? '—' : tally.maxDrawdown.toFixed(2)
  return `สุทธิ ${net} ${tally.currency} · profit factor ${factor} · ต่อไม้ ${expectancy} · ` +
    `ถอยลึกสุด ${drawdown} (เงินจำลอง ไม่ใช่ยอดในบัญชีโบรกเกอร์)`
}

export function replayFoldLine(fold: ReplayFold): string {
  const candidate = fold.candidateThreshold === null
    ? 'ไม่พบเกณฑ์'
    : `${fold.candidateMetric} ≥ ${fold.candidateThreshold.toFixed(2)}`
  const result = fold.test.lift === null
    ? '—'
    : `${fold.test.lift > 0 ? 'ดีกว่าฐาน' : 'ไม่ดีกว่าฐาน'} ${replayPercentLabel(fold.test.winRateExcludingDraws)}`
  const stability = fold.candidateThreshold === null
    ? '—'
    : `ทิศทาง ${fold.directionalStable ? 'นิ่ง' : 'ไม่นิ่ง'} · เงิน ${fold.monetaryVerdict}`
  return `${fold.foldId} · ${candidate} · ทดสอบ n=${fold.test.selected} ${result} · ${stability}`
}

export function replayLatencyLine(row: ReplayLatency): string {
  const delta = row.winRateDelta === null ? '' : ` (ต่างจาก 0ms ${(row.winRateDelta * 100).toFixed(1)}pp)`
  const expectancy = row.expectancyPerTrade === null ? '—' : row.expectancyPerTrade.toFixed(2)
  return `${row.delayMs}ms · n=${row.resolved} · ชนะ ${replayPercentLabel(row.winRateExcludingDraws)}` +
    `${delta} · ต่อไม้ ${expectancy}`
}

/** The one sentence that must appear wherever a replay result does. */
export const REPLAY_NOT_APPLIED_NOTICE =
  'เป็นการจำลองย้อนหลังเพื่อวิจัยเท่านั้น — ไม่ได้เปลี่ยนเกณฑ์ ไม่ได้แตะรอบวัน ' +
  'ไม่ได้แตะการส่งคำสั่ง และเฟสนี้ไม่มีปุ่มให้นำผลไปใช้'

export { MoneyMetricsSchema, OutcomeMetricsSchema }
