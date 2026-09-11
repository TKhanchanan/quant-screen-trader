import { z } from 'zod'

// Declared here rather than imported from the barrel: the barrel re-exports this module, and
// a cycle through it leaves these undefined while this file is still evaluating.
const PlatformSchema = z.enum(['capitalbear', 'iqoption'])
const SlotIdSchema = z.number().int().min(1).max(9)

/**
 * Read-only Phase 9 diagnostics. The desktop renders paper outcomes the engine has already
 * resolved; it never prices, enters, settles or scores anything itself, and there is no
 * command channel here.
 *
 * A paper result and an execution ticket are different statements about different things and
 * are never merged. CONFIRMED on a ticket means the broker panel visibly reacted to a press.
 * WIN on a paper trade means the market moved the way the analysis said it would — no order
 * existed, nothing was pressed, and no money changed hands. The two vocabularies below are
 * deliberately disjoint so a glance at the window cannot confuse them.
 */
export const PaperTradeStatusSchema = z.enum([
  'PENDING_ENTRY', 'OPEN', 'RESOLVED', 'CANCELLED', 'INVALID'
])
export type PaperTradeStatus = z.infer<typeof PaperTradeStatusSchema>

export const PaperOutcomeSchema = z.enum(['UNRESOLVED', 'WIN', 'LOSS', 'DRAW', 'INVALID'])
export type PaperOutcome = z.infer<typeof PaperOutcomeSchema>

export const PaperTradeSchema = z.looseObject({
  paperTradeId: z.uuid(),
  platform: PlatformSchema,
  slotId: SlotIdSchema,
  assetName: z.string().max(120),
  direction: z.enum(['UP', 'DOWN']),
  /** The primary close the ranking cohort described. Never the entry time. */
  boardAsOf: z.number().int(),
  /** When the finished decision first existed in market time. Never earlier than boardAsOf. */
  decisionAvailableAt: z.number().int(),
  rank: z.number().int().min(1).max(9).nullable(),
  rankScore: z.number().min(0).max(1),
  ensembleConfidence: z.number().min(0).max(1),
  durationMs: z.number().int().positive(),
  entryTime: z.number().int().nullable(),
  entryPrice: z.number().positive().nullable(),
  expiryTargetTime: z.number().int().nullable(),
  expiryTime: z.number().int().nullable(),
  expiryPrice: z.number().positive().nullable(),
  /** Descriptive movement. Not profit, and not payout-adjusted. */
  priceDeltaBps: z.number().nullable(),
  status: PaperTradeStatusSchema,
  outcome: PaperOutcomeSchema,
  paperCurrency: z.string().max(8).nullable(),
  paperStake: z.number().positive().nullable(),
  /** Simulated money, and null whenever no stake and payout rate were configured at entry. */
  realizedPaperPnl: z.number().nullable(),
  paperVersion: z.string().min(1).max(40),
  reasons: z.array(z.string()).max(8),
  invalidReasons: z.array(z.string()).max(8)
})
export type PaperTrade = z.infer<typeof PaperTradeSchema>

export const PaperStatsSchema = z.looseObject({
  resolved: z.number().int().nonnegative(),
  wins: z.number().int().nonnegative(),
  losses: z.number().int().nonnegative(),
  draws: z.number().int().nonnegative(),
  invalid: z.number().int().nonnegative(),
  cancelled: z.number().int().nonnegative(),
  winRateExcludingDraws: z.number().min(0).max(1).nullable(),
  winRateIncludingDraws: z.number().min(0).max(1).nullable(),
  currentWinStreak: z.number().int().nonnegative(),
  currentLossStreak: z.number().int().nonnegative(),
  averagePriceDeltaBps: z.number().nullable(),
  netPaperPnl: z.number().nullable(),
  paperVersion: z.string().min(1).max(40)
})
export type PaperStats = z.infer<typeof PaperStatsSchema>

/** What the engine returns for /api/paper/state. Loose: it also reports counters. */
export const PaperEngineStateSchema = z.looseObject({
  paperVersion: z.string().min(1).max(40),
  enabled: z.boolean(),
  accountingConfigured: z.boolean(),
  pending: z.number().int().nonnegative(),
  open: z.number().int().nonnegative(),
  resolved: z.number().int().nonnegative()
})

/** What the desktop bridge delivers. `available` is added by the main process, never the engine. */
export const PaperStateSchema = z.strictObject({
  paperVersion: z.string().min(1).max(40),
  available: z.boolean(),
  enabled: z.boolean(),
  accountingConfigured: z.boolean(),
  open: z.array(PaperTradeSchema).max(6),
  recent: z.array(PaperTradeSchema).max(20),
  stats: PaperStatsSchema.nullable()
})
export type PaperState = z.infer<typeof PaperStateSchema>

const OUTCOME_LABELS: Record<PaperOutcome, string> = {
  UNRESOLVED: 'ยังไม่รู้ผล',
  WIN: 'ทิศทางถูก',
  LOSS: 'ทิศทางผิด',
  DRAW: 'ราคาเท่าเดิม',
  INVALID: 'วัดผลไม่ได้'
}

const STATUS_LABELS: Record<PaperTradeStatus, string> = {
  PENDING_ENTRY: 'รอราคาแรกหลังมีสัญญาณ',
  OPEN: 'กำลังจับเวลา',
  RESOLVED: 'รู้ผลแล้ว',
  CANCELLED: 'ยกเลิก (ช่องเปลี่ยนสินทรัพย์)',
  INVALID: 'วัดผลไม่ได้'
}

const DIRECTIONS: Record<'UP' | 'DOWN', string> = { UP: 'ขึ้น', DOWN: 'ลง' }

/**
 * Thai money for a simulated result, or an explicit "not configured".
 *
 * `null` means the operator never stated a simulated stake and payout rate, so no monetary
 * result is knowable. It is never rendered as 0, which would read as break-even.
 */
export function paperMoneyLabel(value: number | null, currency: string | null): string {
  if (value === null || currency === null) return 'ยังไม่ได้ตั้งค่าเงินจำลอง'
  const symbol = currency === 'THB' ? '฿' : `${currency} `
  return `${value > 0 ? '+' : value < 0 ? '−' : ''}${symbol}${Math.abs(value).toFixed(2)}`
}

/** One compact line for a live paper trade. Descriptive: it reports, it does not instruct. */
export function paperOpenLine(trade: PaperTrade): string {
  const entry = trade.entryPrice === null ? 'ยังไม่ได้ราคาเข้า' : `เข้าที่ ${trade.entryPrice}`
  const expiry = trade.expiryTargetTime === null
    ? `ครบกำหนดอีก ${trade.durationMs / 1000} วิ หลังได้ราคาเข้า`
    : `ครบกำหนด ${new Date(trade.expiryTargetTime).toLocaleTimeString('th-TH')}`
  return `ช่อง ${trade.slotId} · ${trade.assetName} · ${DIRECTIONS[trade.direction]} · ` +
    `${STATUS_LABELS[trade.status]} · ${entry} · ${expiry} · คะแนน ${trade.rankScore.toFixed(2)}`
}

/** One compact line for a finished paper trade, including its simulated money when there is any. */
export function paperResultLine(trade: PaperTrade): string {
  const movement = trade.entryPrice !== null && trade.expiryPrice !== null
    ? `${trade.entryPrice} → ${trade.expiryPrice}` : '—'
  const bps = trade.priceDeltaBps === null
    ? '' : ` · ${trade.priceDeltaBps > 0 ? '+' : ''}${trade.priceDeltaBps.toFixed(2)} bps`
  const money = trade.realizedPaperPnl === null
    ? '' : ` · ${paperMoneyLabel(trade.realizedPaperPnl, trade.paperCurrency)}`
  const why = trade.invalidReasons.length ? ` · ${trade.invalidReasons.join(', ')}` : ''
  return `${trade.assetName} · ${DIRECTIONS[trade.direction]} · ` +
    `${OUTCOME_LABELS[trade.outcome]}${trade.status === 'CANCELLED' ? ' (ยกเลิก)' : ''} · ` +
    `${movement}${bps}${money}${why}`
}

/**
 * The tally, with the caveat attached rather than left to the reader.
 *
 * A rate over a few dozen simulated trades describes what was recorded. It is not a claim
 * about what will happen next, and nothing upstream is tuned on it.
 */
export function paperStatsLine(stats: PaperStats | null): string {
  if (!stats || stats.resolved === 0) return 'ยังไม่มีผลจำลอง'
  const rate = stats.winRateExcludingDraws === null
    ? '—' : `${(stats.winRateExcludingDraws * 100).toFixed(1)}%`
  const money = stats.netPaperPnl === null ? '' : ` · รวม ${paperMoneyLabel(stats.netPaperPnl, 'THB')}`
  return `ถูก ${stats.wins} · ผิด ${stats.losses} · เสมอ ${stats.draws} · ` +
    `วัดไม่ได้ ${stats.invalid} · สัดส่วนถูก ${rate}${money}`
}
