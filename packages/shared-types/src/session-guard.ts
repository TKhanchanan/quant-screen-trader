import { z } from 'zod'

/**
 * Phase 9.5 daily session guard. A stop system, not a strategy.
 *
 * The desktop renders a day's realized accounting and can change the operator's own limits or
 * end the day. It never sizes, directs or scores anything, and nothing it sends can make the
 * quant layers produce a different decision: the guard only ever withdraws permission.
 *
 * `canOpenNewEntry` is a veto and nothing else. It is deliberately separate from Arm/Disarm:
 * disarming controls the execution layer, and stopping the session ends the trading day.
 */

// Declared here rather than imported from the barrel: the barrel re-exports this module, and a
// cycle through it leaves these undefined while this file is still evaluating.
const CurrencyAmount = z.number()

export const DailySessionStatusSchema = z.enum([
  'DISABLED', 'ACTIVE', 'TARGET_REACHED', 'LOSS_LIMIT_REACHED', 'WAITING_FOR_SETTLEMENT',
  'STOPPED_MANUALLY', 'COMPLETED', 'LOCKED_FOR_DAY', 'ACCOUNTING_ERROR'
])
export type DailySessionStatus = z.infer<typeof DailySessionStatusSchema>

export const SessionBlockReasonSchema = z.enum([
  'DAILY_PROFIT_TARGET', 'DAILY_LOSS_LIMIT', 'MANUAL_STOP', 'LOCKED_FOR_DAY',
  'ACCOUNTING_ERROR', 'GUARD_DISABLED'
])
export type SessionBlockReason = z.infer<typeof SessionBlockReasonSchema>

export const DailySessionSchema = z.looseObject({
  sessionId: z.uuid(),
  sessionDate: z.string().min(10).max(10),
  timezone: z.string().min(1).max(64),
  resetHour: z.number().int().min(0).max(23),
  startedAt: z.number().int(),
  completedAt: z.number().int().nullable(),
  nextResetAt: z.number().int(),
  status: DailySessionStatusSchema,
  accountingSource: z.literal('PAPER'),
  currency: z.string().min(1).max(8),
  profitTarget: CurrencyAmount.positive().nullable(),
  lossLimit: CurrencyAmount.positive().nullable(),
  /** Realized only. An open paper trade that is probably going to win is worth nothing here. */
  realizedPnl: CurrencyAmount,
  grossProfit: CurrencyAmount.min(0),
  grossLoss: CurrencyAmount.min(0),
  wins: z.number().int().min(0),
  losses: z.number().int().min(0),
  draws: z.number().int().min(0),
  invalid: z.number().int().min(0),
  resolvedTrades: z.number().int().min(0),
  /** The subset that moved the money. The gap is how much of the day is unpriced. */
  monetaryTrades: z.number().int().min(0),
  openTrades: z.number().int().min(0),
  largestWin: CurrencyAmount.min(0),
  largestLoss: CurrencyAmount.max(0),
  peakRealizedPnl: CurrencyAmount,
  troughRealizedPnl: CurrencyAmount,
  maxRealizedDrawdown: CurrencyAmount.min(0),
  targetReachedAt: z.number().int().nullable(),
  lossLimitReachedAt: z.number().int().nullable(),
  tradesToTarget: z.number().int().min(0).nullable(),
  stopReason: z.string().max(48).nullable(),
  canOpenNewEntry: z.boolean(),
  blockReason: SessionBlockReasonSchema.nullable(),
  currencyMismatches: z.number().int().min(0),
  nonMonetarySettlements: z.number().int().min(0),
  sessionGuardVersion: z.string().min(1).max(40)
})
export type DailySession = z.infer<typeof DailySessionSchema>

export const DailySessionSummarySchema = z.looseObject({
  sessionId: z.uuid(),
  date: z.string().min(10).max(10),
  status: DailySessionStatusSchema,
  currency: z.string().min(1).max(8),
  realizedPnl: CurrencyAmount,
  wins: z.number().int().min(0),
  losses: z.number().int().min(0),
  draws: z.number().int().min(0),
  resolvedTrades: z.number().int().min(0),
  winRateExcludingDraws: z.number().min(0).max(1).nullable(),
  maxRealizedDrawdown: CurrencyAmount.min(0),
  tradesToTarget: z.number().int().min(0).nullable(),
  durationToTargetMs: z.number().int().min(0).nullable()
})
export type DailySessionSummary = z.infer<typeof DailySessionSummarySchema>

export const SessionNotificationSchema = z.looseObject({
  eventId: z.uuid(),
  sessionId: z.uuid(),
  type: z.enum(['PROFIT_TARGET_REACHED', 'LOSS_LIMIT_REACHED', 'SESSION_COMPLETED', 'ACCOUNTING_ERROR']),
  title: z.string().min(1).max(80),
  message: z.string().min(1).max(400),
  occurredAt: z.number().int()
})
export type SessionNotification = z.infer<typeof SessionNotificationSchema>

/** The operator's own limits. There are no default amounts, and none is invented here either. */
export const SessionGuardSettingsSchema = z.object({
  enabled: z.boolean(),
  dailyProfitTarget: z.number().positive().max(100_000_000).nullable(),
  dailyLossLimit: z.number().positive().max(100_000_000).nullable(),
  currency: z.string().trim().min(1).max(8),
  timezone: z.string().trim().min(1).max(64),
  resetHour: z.number().int().min(0).max(23),
  notifyOnProfitTarget: z.boolean(),
  notifyOnLossLimit: z.boolean(),
  closeAppOnProfitTarget: z.boolean(),
  closeAppOnLossLimit: z.boolean(),
  waitForOpenTradesBeforeClose: z.boolean(),
  lockAfterProfitTarget: z.boolean(),
  lockAfterLossLimit: z.boolean()
})
export type SessionGuardSettings = z.infer<typeof SessionGuardSettingsSchema>

export function defaultSessionGuardSettings(): SessionGuardSettings {
  return {
    enabled: false, dailyProfitTarget: null, dailyLossLimit: null, currency: 'THB',
    timezone: 'Asia/Bangkok', resetHour: 0, notifyOnProfitTarget: true, notifyOnLossLimit: true,
    closeAppOnProfitTarget: true, closeAppOnLossLimit: true, waitForOpenTradesBeforeClose: true,
    lockAfterProfitTarget: true, lockAfterLossLimit: true
  }
}

export const SessionGuardCommandSchema = z.discriminatedUnion('operation', [
  z.object({ operation: z.literal('state') }),
  z.object({ operation: z.literal('settings'), settings: SessionGuardSettingsSchema }),
  /** Ends the trading day. Deliberately not Disarm: that controls execution, this controls the day. */
  z.object({ operation: z.literal('stop') })
])
export type SessionGuardCommand = z.infer<typeof SessionGuardCommandSchema>

/** What the desktop bridge delivers. `available` is added by the main process, never the engine. */
export const SessionGuardStateSchema = z.strictObject({
  sessionGuardVersion: z.string().min(1).max(40),
  available: z.boolean(),
  enabled: z.boolean(),
  canOpenNewEntry: z.boolean(),
  blockReason: SessionBlockReasonSchema.nullable(),
  shutdownRequested: z.boolean(),
  /** False means Phase 9 measured no money, so a daily P/L cannot be shown — and never as 0. */
  paperAccountingConfigured: z.boolean(),
  settingsError: z.string().max(200).nullable(),
  session: DailySessionSchema.nullable(),
  settings: SessionGuardSettingsSchema,
  targetProgress: z.number().min(0).max(1).nullable(),
  lossProgress: z.number().min(0).max(1).nullable(),
  remainingToTarget: CurrencyAmount.nullable(),
  nextResetAt: z.number().int().nullable(),
  openTrades: z.number().int().min(0),
  notifications: z.array(SessionNotificationSchema).max(16),
  history: z.array(DailySessionSummarySchema).max(30)
})
export type SessionGuardState = z.infer<typeof SessionGuardStateSchema>

const STATUS_LABELS: Record<DailySessionStatus, string> = {
  DISABLED: 'ปิดการคุมรอบอยู่',
  ACTIVE: 'กำลังเทรด',
  TARGET_REACHED: 'ถึงเป้ากำไรแล้ว',
  LOSS_LIMIT_REACHED: 'ถึงขีดขาดทุนแล้ว',
  WAITING_FOR_SETTLEMENT: 'หยุดแล้ว รอไม้ที่ค้างรู้ผล',
  STOPPED_MANUALLY: 'หยุดเองแล้ว',
  COMPLETED: 'จบรอบวันแล้ว',
  LOCKED_FOR_DAY: 'ล็อกวันนี้แล้ว',
  ACCOUNTING_ERROR: 'บัญชีวันนี้เชื่อถือไม่ได้ — หยุดไว้ก่อน'
}

const BLOCK_LABELS: Record<SessionBlockReason, string> = {
  DAILY_PROFIT_TARGET: 'ถึงเป้ากำไรของวันแล้ว',
  DAILY_LOSS_LIMIT: 'ถึงขีดขาดทุนของวันแล้ว',
  MANUAL_STOP: 'หยุดรอบวันเอง',
  LOCKED_FOR_DAY: 'ล็อกไว้จนถึงรอบถัดไป',
  ACCOUNTING_ERROR: 'บัญชีวันนี้เชื่อถือไม่ได้',
  GUARD_DISABLED: 'ยังไม่ได้เปิดการคุมรอบ'
}

export function sessionStatusLabel(status: DailySessionStatus | null): string {
  return status ? STATUS_LABELS[status] : 'ยังไม่เริ่มรอบวันนี้'
}

/** Why new entries are refused, or that they are not. GUARD_DISABLED is never a refusal. */
export function sessionBlockLabel(state: SessionGuardState): string {
  if (state.canOpenNewEntry) {
    return state.blockReason === 'GUARD_DISABLED' ? 'ไม่ได้คุมรอบ — ไม่ได้ห้ามอะไร' : 'เข้าไม้ใหม่ได้'
  }
  return state.blockReason ? BLOCK_LABELS[state.blockReason] : 'หยุดรับไม้ใหม่แล้ว'
}

export function sessionMoney(value: number, currency: string): string {
  const symbol = currency === 'THB' ? '฿' : `${currency} `
  return `${value > 0 ? '+' : value < 0 ? '−' : ''}${symbol}${Math.abs(value).toFixed(2)}`
}

/**
 * The day's realized P/L, or an explicit statement that it cannot be known.
 *
 * Phase 9 reports money only when a simulated stake and payout were configured. Rendering an
 * unmeasured day as ฿0 would read as break-even, which is a claim about a day nobody priced.
 */
export function dailyPnlLabel(state: SessionGuardState): string {
  const session = state.session
  if (!session) return 'ยังไม่มีรอบวันนี้'
  if (!state.paperAccountingConfigured && session.monetaryTrades === 0)
    return 'ยังไม่ได้ตั้งค่าเงินจำลอง'
  return sessionMoney(session.realizedPnl, session.currency)
}

/** 0..1 for a bar. A losing day is zero progress toward a profit target, never a negative bar. */
export function progressPercent(progress: number | null): string {
  return progress === null ? '—' : `${Math.round(progress * 100)}%`
}

export function nextResetLabel(state: SessionGuardState): string {
  if (state.nextResetAt === null) return '—'
  const zone = state.session?.timezone ?? state.settings.timezone
  const at = new Date(state.nextResetAt).toLocaleString('th-TH', { timeZone: zone })
  return `${at} · ${zone}`
}
