import { z } from 'zod'

export const PlatformSchema = z.enum(['capitalbear', 'iqoption'])
export type Platform = z.infer<typeof PlatformSchema>

export const SlotIdSchema = z.number().int().min(1).max(9)

export const NormalizedBoundsSchema = z.object({
  x: z.number().min(0).max(1),
  y: z.number().min(0).max(1),
  width: z.number().positive().max(1),
  height: z.number().positive().max(1)
})

export const PlatformSlotSchema = z.object({
  id: SlotIdSchema,
  enabled: z.boolean(),
  assetName: z.string(),
  displayName: z.string().optional(),
  platform: PlatformSchema,
  normalizedBounds: NormalizedBoundsSchema.optional(),
  parserProfile: z.string().optional()
})
export type PlatformSlot = z.infer<typeof PlatformSlotSchema>

export const QuantEngineHealthPayloadSchema = z
  .object({
    type: z.literal('health'),
    service: z.literal('quant-engine'),
    version: z.string().min(1),
    status: z.enum(['ok', 'degraded']),
    database: z.enum(['ok', 'error']),
    timestamp: z.string().min(1),
    sequence: z.number().int().nonnegative()
  })
  .passthrough()
export type QuantEngineHealthPayload = z.infer<typeof QuantEngineHealthPayloadSchema>

export const EngineHealthSnapshotSchema = z.object({
  state: z.enum(['online', 'degraded', 'offline']),
  checkedAt: z.string().min(1),
  latencyMs: z.number().nonnegative().optional(),
  message: z.string().optional(),
  engine: QuantEngineHealthPayloadSchema.optional()
})
export type EngineHealthSnapshot = z.infer<typeof EngineHealthSnapshotSchema>

export const IPC_CHANNELS = {
  getEngineHealth: 'engine:get-health',
  openWorkspace: 'workspace:open'
} as const

export interface DesktopBridge {
  getEngineHealth: () => Promise<EngineHealthSnapshot>
  openWorkspace: (platform: Platform) => Promise<void>
}
