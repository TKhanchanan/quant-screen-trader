import { z } from 'zod'
import { BoardStatusSchema } from './opportunity'

// Declared locally, as the sibling contracts do, so this module never imports the barrel it
// is re-exported from.
const PlatformSchema = z.enum(['capitalbear', 'iqoption'])
const SlotIdSchema = z.number().int().min(1).max(9)
const NormalizedBoundsSchema = z.object({
  x: z.number().min(0).max(1), y: z.number().min(0).max(1),
  width: z.number().positive().max(1), height: z.number().positive().max(1)
})

/**
 * Phase 9 execution contracts.
 *
 * Everything above this file forms opinions; this is the first layer that acts on one. The
 * shapes here are deliberately explicit about that: an order ticket records what was pressed,
 * when, on whose analysis, and whether the press could be confirmed afterwards. Nothing is
 * inferred later from a missing field.
 */

export const OrderDirectionSchema = z.enum(['HIGHER', 'LOWER'])
export type OrderDirection = z.infer<typeof OrderDirectionSchema>

/** OFF sends nothing. PAPER runs every gate and records the ticket without pressing. AUTO presses. */
export const ExecutionModeSchema = z.enum(['OFF', 'PAPER', 'AUTO'])
export type ExecutionMode = z.infer<typeof ExecutionModeSchema>

export const NormalizedPointSchema = z.object({ x: z.number().min(0).max(1), y: z.number().min(0).max(1) })
export type NormalizedPoint = z.infer<typeof NormalizedPointSchema>

/**
 * Where one cell's two controls are, in normalized browser-surface coordinates so the map
 * survives a resize the way calibration slots do. Zoom does not: it re-lays the broker canvas,
 * so a map is only valid at the zoom it was measured at.
 */
export const SlotControlsSchema = z.object({
  slotId: SlotIdSchema,
  higher: NormalizedPointSchema,
  lower: NormalizedPointSchema,
  panelBounds: NormalizedBoundsSchema,
  confidence: z.number().min(0).max(1)
})
export type SlotControls = z.infer<typeof SlotControlsSchema>

/**
 * Which color this broker paints on which side is a fact about the broker, established once by
 * calibration and stored — never assumed from a convention that a redesign can silently break.
 */
export const ControlMapSchema = z.object({
  platform: PlatformSchema,
  zoomFactor: z.number().positive(),
  surfaceRevision: z.number().int().min(0),
  directionForGreen: OrderDirectionSchema,
  slots: z.array(SlotControlsSchema).max(9),
  measuredAt: z.iso.datetime(),
  reasons: z.array(z.string()).max(32)
})
export type ControlMap = z.infer<typeof ControlMapSchema>

export const ExecutionLimitsSchema = z.object({
  /** The engine's own selection gate already ran; these are the operator's limits on top of it. */
  minRankScore: z.number().min(0).max(1),
  minEnsembleConfidence: z.number().min(0).max(1),
  minControlConfidence: z.number().min(0).max(1),
  acceptBoardStatus: z.array(BoardStatusSchema).min(1).max(5),
  cooldownMs: z.number().int().min(0).max(3_600_000),
  maxOrdersPerHour: z.number().int().min(0).max(240),
  /** Consecutive presses that could not be confirmed before the executor disarms itself. */
  maxUnverifiedInARow: z.number().int().min(1).max(20)
})
export type ExecutionLimits = z.infer<typeof ExecutionLimitsSchema>

export const ExecutionSettingsSchema = z.object({
  mode: ExecutionModeSchema,
  limits: ExecutionLimitsSchema,
  allowedSlots: z.array(SlotIdSchema).max(9)
})
export type ExecutionSettings = z.infer<typeof ExecutionSettingsSchema>

/**
 * SENT means the input events left the application. CONFIRMED means the panel visibly changed
 * afterwards. UNVERIFIED means it did not, which is not proof the order failed — only that this
 * layer cannot claim it succeeded.
 */
export const TicketStateSchema = z.enum(['BLOCKED', 'PAPER', 'SENT', 'CONFIRMED', 'UNVERIFIED', 'FAILED'])
export type TicketState = z.infer<typeof TicketStateSchema>

export const OrderTicketSchema = z.object({
  id: z.string().min(1).max(64),
  platform: PlatformSchema,
  slotId: SlotIdSchema,
  assetName: z.string().max(120),
  direction: OrderDirectionSchema,
  /** The board close this acted on. One board can produce at most one ticket per platform. */
  boardAsOf: z.number().int(),
  rankScore: z.number().min(0).max(1),
  ensembleConfidence: z.number().min(0).max(1),
  state: TicketStateSchema,
  reasons: z.array(z.string()).max(12),
  requestedAt: z.iso.datetime(),
  pressedAt: z.iso.datetime().nullable(),
  latencyMs: z.number().int().min(0).nullable()
})
export type OrderTicket = z.infer<typeof OrderTicketSchema>

export const ExecutionStateSchema = z.strictObject({
  platform: PlatformSchema,
  settings: ExecutionSettingsSchema,
  /** AUTO alone presses nothing. The operator arms it, and any breaker can take the arm away. */
  armed: z.boolean(),
  controls: ControlMapSchema.nullable(),
  controlsValid: z.boolean(),
  /** Why a press cannot happen right now. Empty means the next qualifying board would fire. */
  blocked: z.array(z.string()).max(12),
  lastBoardAsOf: z.number().int().nullable(),
  ordersLastHour: z.number().int().min(0),
  unverifiedInARow: z.number().int().min(0),
  tickets: z.array(OrderTicketSchema).max(50),
  executionVersion: z.string().min(1).max(40)
})
export type ExecutionState = z.infer<typeof ExecutionStateSchema>

export const ExecutionCommandSchema = z.discriminatedUnion('operation', [
  z.object({ operation: z.literal('state'), platform: PlatformSchema }),
  z.object({ operation: z.literal('settings'), platform: PlatformSchema, settings: ExecutionSettingsSchema }),
  z.object({ operation: z.literal('arm'), platform: PlatformSchema }),
  /** The stop control. Disarms, and is the one operation no gate, breaker or state can refuse. */
  z.object({ operation: z.literal('disarm'), platform: PlatformSchema }),
  z.object({ operation: z.literal('calibrateControls'), platform: PlatformSchema }),
  z.object({ operation: z.literal('directionForGreen'), platform: PlatformSchema, direction: OrderDirectionSchema }),
  /**
   * Operator-triggered control test. Presses every measured control once, in order, so the
   * resulting entries can be compared against the broker's own order list. This places real
   * entries at whatever stake and expiry each panel already carries; it exists because proving
   * the coordinates land is otherwise only possible by pressing them.
   */
  z.object({ operation: z.literal('testControls'), platform: PlatformSchema,
    /** Must be the literal string below. A mistyped confirmation presses nothing. */
    confirm: z.literal('PRESS ALL CONTROLS') })
])
export type ExecutionCommand = z.infer<typeof ExecutionCommandSchema>

export const EXECUTION_VERSION = 'qst-execution-v1'

/** Conservative starting limits. AUTO with these still requires the engine to name a leader. */
export const DEFAULT_EXECUTION_LIMITS: ExecutionLimits = {
  minRankScore: .6,
  minEnsembleConfidence: .55,
  minControlConfidence: .8,
  acceptBoardStatus: ['READY'],
  cooldownMs: 60_000,
  maxOrdersPerHour: 12,
  maxUnverifiedInARow: 3
}

export function defaultExecutionSettings(): ExecutionSettings {
  return { mode: 'OFF', limits: { ...DEFAULT_EXECUTION_LIMITS }, allowedSlots: [1, 2, 3, 4, 5, 6, 7, 8, 9] }
}

// Operator-facing text is Thai: this panel is read under time pressure, in the same glance as the
// broker's own Thai controls, and a mistranslated gate is a gate nobody checks.
const BLOCK_LABELS: Record<string, string> = {
  MODE_OFF: 'โหมดยังปิดอยู่ (OFF)',
  NOT_ARMED: 'ยังไม่ได้ Arm',
  NO_CONTROL_MAP: 'ยังไม่ได้วัดตำแหน่งปุ่ม',
  CONTROL_MAP_STALE: 'ตำแหน่งปุ่มเก่าแล้ว (zoom หรือขนาดหน้าต่างเปลี่ยน) — วัดใหม่',
  CONTROL_CONFIDENCE_LOW: 'ความมั่นใจตำแหน่งปุ่มต่ำเกินกด',
  SURFACE_UNAVAILABLE: 'หน้าต่างโบรกยังไม่แสดงตาราง 3×3 ที่ยืนยันแล้ว',
  HOURLY_CAP: 'ครบโควตาออเดอร์ต่อชั่วโมงแล้ว',
  COOLDOWN: 'กำลังพักหลังออเดอร์ล่าสุด',
  UNVERIFIED_BREAKER: 'ปลด Arm อัตโนมัติ เพราะยืนยันการกดไม่ได้ติดกันหลายครั้ง',
  BOARD_UNAVAILABLE: 'ยังไม่มีบอร์ดสัญญาณ',
  BOARD_STATUS: 'สถานะบอร์ดไม่อยู่ในเกณฑ์ที่ตั้งไว้',
  NO_SELECTION: 'บอร์ดไม่ได้ชี้ตัวนำ',
  BELOW_LIMITS: 'ตัวนำคะแนนต่ำกว่าเกณฑ์ที่ตั้งไว้',
  SLOT_NOT_ALLOWED: 'ช่องนั้นถูกกันออกจากการเทรด',
  // The Phase 9.5 daily session guard. It ends the trading day; it is not Disarm, and it is
  // never a reason to change a score, a stake or a strategy.
  SESSION_DAILY_PROFIT_TARGET: 'ถึงเป้ากำไรของวันแล้ว — รอบวันหยุดรับไม้ใหม่',
  SESSION_DAILY_LOSS_LIMIT: 'ถึงขีดขาดทุนของวันแล้ว — รอบวันหยุดรับไม้ใหม่',
  SESSION_MANUAL_STOP: 'หยุดรอบวันเอง',
  SESSION_LOCKED_FOR_DAY: 'ล็อกรอบวันนี้แล้ว',
  SESSION_ACCOUNTING_ERROR: 'บัญชีรอบวันเชื่อถือไม่ได้ — หยุดไว้ก่อน',
  SESSION_STOPPED: 'รอบวันหยุดรับไม้ใหม่'
}

export function blockLabel(code: string): string { return BLOCK_LABELS[code] ?? code }

const TICKET_STATE_LABELS: Record<TicketState, string> = {
  BLOCKED: 'ไม่ได้ส่ง (ติดด่าน)',
  PAPER: 'PAPER — ไม่ได้กดจริง',
  SENT: 'ส่งแล้ว',
  CONFIRMED: 'สำเร็จ (แผงตอบสนอง)',
  UNVERIFIED: 'กดแล้วแต่ยืนยันไม่ได้',
  FAILED: 'กดไม่สำเร็จ'
}

const DIRECTION_LABELS: Record<OrderDirection, string> = { HIGHER: 'ขึ้น', LOWER: 'ลง' }

/** UP/DOWN is an analytical direction; HIGHER/LOWER is a control on a broker panel. */
export function directionForVote(direction: 'UP' | 'DOWN' | 'NEUTRAL' | 'SKIP'): OrderDirection | null {
  return direction === 'UP' ? 'HIGHER' : direction === 'DOWN' ? 'LOWER' : null
}

export function ticketLabel(ticket: OrderTicket): string {
  return `${new Date(ticket.requestedAt).toLocaleTimeString('th-TH')} · ช่อง ${ticket.slotId} · ${ticket.assetName} · ` +
    `${DIRECTION_LABELS[ticket.direction]} · ${TICKET_STATE_LABELS[ticket.state]}` +
    `${ticket.reasons.length ? ` · ${ticket.reasons.join(', ')}` : ''}`
}
