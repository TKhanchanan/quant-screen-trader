import { z } from 'zod'
import { DirectionSchema, RegimeSchema } from './strategy'

/**
 * Read-only Phase 8 diagnostics. The desktop renders a ranking the engine has already
 * produced; it never scores, orders or selects anything itself, and there is no command
 * channel here. A "selected" candidate is the current top analysis, not an instruction.
 */
export const CandidateStatusSchema = z.enum(['ACTIONABLE', 'WATCH', 'NEUTRAL', 'EXCLUDED'])
export type CandidateStatus = z.infer<typeof CandidateStatusSchema>

export const BoardStatusSchema = z.enum(['COLLECTING', 'READY', 'PARTIAL', 'NO_OPPORTUNITY', 'INVALID'])
export type BoardStatus = z.infer<typeof BoardStatusSchema>

export const WatchlistEntrySchema = z.looseObject({
  rank: z.number().int().min(1).max(9),
  slotId: z.number().int().min(1).max(9),
  assetName: z.string(),
  direction: DirectionSchema,
  rankScore: z.number().min(0).max(1),
  ensembleConfidence: z.number().min(0).max(1),
  regime: RegimeSchema,
  candidateStatus: CandidateStatusSchema
})
export type WatchlistEntry = z.infer<typeof WatchlistEntrySchema>

export const OpportunityCandidateSchema = z.looseObject({
  platform: z.enum(['capitalbear', 'iqoption']),
  slotId: z.number().int().min(1).max(9),
  assetName: z.string(),
  asOf: z.number().int(),
  direction: DirectionSchema,
  primaryRegime: RegimeSchema,
  ensembleConfidence: z.number().min(0).max(1),
  /** A relative ordering utility, never a probability, win rate or expected return. */
  rankScore: z.number().min(0).max(1),
  candidateStatus: CandidateStatusSchema,
  rank: z.number().int().min(1).max(9).nullable(),
  rankReasonCodes: z.array(z.string()).max(8),
  exclusionReasons: z.array(z.string()).max(8)
})
export type OpportunityCandidate = z.infer<typeof OpportunityCandidateSchema>

export const OpportunityBoardSchema = z.looseObject({
  platform: z.enum(['capitalbear', 'iqoption']),
  asOf: z.number().int(),
  primaryTimeframe: z.enum(['S5', 'M1', 'M5', 'M10']),
  featureVersion: z.string().min(1).max(40),
  regimeVersion: z.string().min(1).max(40),
  strategyVersion: z.string().min(1).max(40),
  rankingVersion: z.string().min(1).max(40),
  status: BoardStatusSchema,
  expectedSlots: z.number().int().min(0).max(9),
  receivedSlots: z.number().int().min(0).max(9),
  rankedSlots: z.number().int().min(0).max(9),
  excludedSlots: z.number().int().min(0).max(9),
  missingSlots: z.array(z.number().int().min(1).max(9)).max(9),
  candidates: z.array(OpportunityCandidateSchema).max(9),
  selectedSlotId: z.number().int().min(1).max(9).nullable(),
  selectedAssetName: z.string().nullable(),
  selectedDirection: DirectionSchema.nullable(),
  selectedScore: z.number().min(0).max(1).nullable(),
  runnerUpSlotId: z.number().int().min(1).max(9).nullable(),
  leadMargin: z.number().min(0).max(1).nullable(),
  watchlist: z.array(WatchlistEntrySchema).max(3),
  reasons: z.array(z.string()).max(8)
})
export type OpportunityBoard = z.infer<typeof OpportunityBoardSchema>

/** What the engine returns for one platform. Loose: it also reports gate constants. */
export const OpportunityResponseSchema = z.looseObject({
  featureVersion: z.string().min(1).max(40),
  regimeVersion: z.string().min(1).max(40),
  strategyVersion: z.string().min(1).max(40),
  rankingVersion: z.string().min(1).max(40),
  board: OpportunityBoardSchema
})
export type OpportunityResponse = z.infer<typeof OpportunityResponseSchema>

/** What the desktop bridge delivers. `available` is added by the main process, never the engine. */
export const OpportunityStateSchema = z.strictObject({
  rankingVersion: z.string().min(1).max(40),
  available: z.boolean(),
  board: OpportunityBoardSchema.nullable()
})
export type OpportunityState = z.infer<typeof OpportunityStateSchema>

const BOARD_LABELS: Record<BoardStatus, string> = {
  COLLECTING: 'กำลังเก็บข้อมูล',
  READY: 'จัดอันดับแล้ว',
  PARTIAL: 'ข้อมูลไม่ครบ (PARTIAL)',
  NO_OPPORTUNITY: 'ไม่มีโอกาส',
  INVALID: 'ข้อมูลใช้ไม่ได้'
}

export function boardLabel(board: OpportunityBoard | null): string {
  return board ? BOARD_LABELS[board.status] : 'ยังไม่มีบอร์ด'
}

/**
 * One compact ranking line. Deliberately descriptive: a rank is an analytical position, so
 * the wording never reads as an instruction to enter, buy, bet or place anything.
 */
export function candidateLabel(entry: WatchlistEntry): string {
  return `#${entry.rank} ช่อง ${entry.slotId} · ${entry.assetName} · ${entry.direction === 'UP' ? 'ขึ้น' : entry.direction === 'DOWN' ? 'ลง' : entry.direction} · ` +
    `คะแนน ${entry.rankScore.toFixed(2)} · ความมั่นใจ ${entry.ensembleConfidence.toFixed(2)} · ${entry.regime}`
}

/** How far ahead the leading analysis stands, or why none was named. */
export function leadLabel(board: OpportunityBoard | null): string {
  if (!board) return '—'
  if (board.selectedSlotId === null) {
    if (board.reasons.includes('LOW_LEAD_MARGIN')) return 'ไม่มีตัวนำชัดเจน (ทิ้งห่างน้อยไป)'
    if (board.reasons.includes('BELOW_SELECTION_SCORE')) return 'คะแนนสูงสุดยังต่ำกว่าเกณฑ์คัดเลือก'
    if (board.reasons.includes('COHORT_INCOMPLETE')) return 'ข้อมูลรอบนี้ไม่ครบ'
    return 'ไม่มีตัวเลือกที่มีทิศทาง'
  }
  return board.leadMargin === null ? 'นำอยู่ — (ตัวเดียวในรอบ)' : `นำอยู่ +${board.leadMargin.toFixed(2)}`
}
