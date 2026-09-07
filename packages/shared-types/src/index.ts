import { z } from 'zod'

export const PlatformSchema = z.enum(['capitalbear', 'iqoption'])
export type Platform = z.infer<typeof PlatformSchema>

export const SlotIdSchema = z.number().int().min(1).max(9)

export const NormalizedBoundsSchema = z.object({
  x: z.number().min(0).max(1),
  y: z.number().min(0).max(1),
  width: z.number().positive().max(1),
  height: z.number().positive().max(1)
}).refine((bounds) => bounds.x + bounds.width <= 1 && bounds.y + bounds.height <= 1,
  'Bounds must remain inside the browser content area')
export type NormalizedBounds = z.infer<typeof NormalizedBoundsSchema>

export const PlatformSlotSchema = z.object({
  id: SlotIdSchema,
  enabled: z.boolean(),
  assetName: z.string().trim().max(120),
  displayName: z.string().trim().max(120).optional(),
  platform: PlatformSchema,
  normalizedBounds: NormalizedBoundsSchema.optional(),
  parserProfile: z.string().optional()
}).refine((slot) => !slot.enabled || slot.assetName.length > 0,
  'Enabled slots require an asset name')
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
  openWorkspace: 'workspace:open',
  platformCommand: 'platform:command', configuration: 'configuration:request'
} as const

export interface DesktopBridge {
  getEngineHealth: () => Promise<EngineHealthSnapshot>
  openWorkspace: (platform: Platform) => Promise<void>
  platformCommand: (request: PlatformCommand) => Promise<BrowserSnapshot>
  configuration: (request: ConfigurationRequest) => Promise<ConfigurationResult>
}

export const PLATFORM_DETAILS = {
  capitalbear: { name: 'CapitalBear', shortName: 'CB' },
  iqoption: { name: 'IQ Option', shortName: 'IQ' }
} as const
export const PLATFORMS = PlatformSchema.options
export const SlotConfigurationSchema = z.object({
  platform: PlatformSchema,
  slots: z.array(PlatformSlotSchema).length(9)
}).refine(({ platform, slots }) => new Set(slots.map((s) => s.id)).size === 9 &&
  slots.every((s) => s.platform === platform), 'Exactly slots 1–9 of the same platform are required')
export type SlotConfiguration = z.infer<typeof SlotConfigurationSchema>
export const CalibrationSlotSchema = z.object({ id: SlotIdSchema, bounds: NormalizedBoundsSchema })
export const CalibrationSlotsSchema = z.array(CalibrationSlotSchema).length(9)
  .refine((slots) => new Set(slots.map((s) => s.id)).size === 9, 'Exactly slots 1–9 are required')
export type CalibrationSlot = z.infer<typeof CalibrationSlotSchema>
const NamedRecord = { id: z.uuid(), platform: PlatformSchema, name: z.string().trim().min(1).max(120),
  createdAt: z.iso.datetime(), updatedAt: z.iso.datetime() }
export const CalibrationProfileSchema = z.object({ ...NamedRecord,
  referenceBrowserWidth: z.number().int().positive().max(32768),
  referenceBrowserHeight: z.number().int().positive().max(32768),
  zoomFactor: z.number().min(0.25).max(5), slots: CalibrationSlotsSchema })
export type CalibrationProfile = z.infer<typeof CalibrationProfileSchema>
export const AssetPresetSchema = z.object({ ...NamedRecord, slots: z.array(PlatformSlotSchema).length(9) })
  .refine((preset) => SlotConfigurationSchema.safeParse(preset).success, 'Invalid preset slots')
export type AssetPreset = z.infer<typeof AssetPresetSchema>
export const ConfigurationResultSchema = z.object({
  configuration: SlotConfigurationSchema,
  calibrations: z.array(CalibrationProfileSchema), presets: z.array(AssetPresetSchema),
  activeCalibrationId: z.uuid().nullable()
})
export type ConfigurationResult = z.infer<typeof ConfigurationResultSchema>
const RecordInput = { platform: PlatformSchema, id: z.uuid().optional(), name: z.string().trim().min(1).max(120) }
export const ConfigurationRequestSchema = z.discriminatedUnion('operation', [
  z.object({ operation: z.literal('get'), platform: PlatformSchema }),
  z.object({ operation: z.literal('slots'), ...SlotConfigurationSchema.shape }),
  z.object({ operation: z.literal('savePreset'), ...RecordInput, slots: z.array(PlatformSlotSchema).length(9) }),
  z.object({ operation: z.literal('saveCalibration'), ...RecordInput,
    ...CalibrationProfileSchema.omit({ id: true, createdAt: true, updatedAt: true }).shape }),
  z.object({ operation: z.enum(['deletePreset', 'loadPreset', 'deleteCalibration', 'loadCalibration']),
    platform: PlatformSchema, id: z.uuid() })
]).superRefine((request, ctx) => {
  if ((request.operation === 'slots' || request.operation === 'savePreset') &&
      !SlotConfigurationSchema.safeParse(request).success)
    ctx.addIssue({ code: 'custom', message: 'Invalid nine-slot configuration' })
})
export type ConfigurationRequest = z.infer<typeof ConfigurationRequestSchema>
export const PlatformSessionStateSchema = z.object({
  platform: PlatformSchema,
  state: z.enum(['STARTING', 'LOADING', 'LOGIN_REQUIRED', 'READY', 'DISCONNECTED', 'ERROR']),
  loadState: z.enum(['idle', 'loading', 'loaded', 'failed']),
  currentUrl: z.url().refine((url) => new URL(url).origin === url && new URL(url).protocol === 'https:',
    'Only a sanitized HTTPS origin may be exposed').optional(), lastUpdatedAt: z.iso.datetime(),
  errorCode: z.string().optional(), errorMessage: z.string().optional()
})
export type PlatformSessionState = z.infer<typeof PlatformSessionStateSchema>
export const BrowserRectangleSchema = z.object({ x: z.number().int().nonnegative(),
  y: z.number().int().nonnegative(), width: z.number().int().positive().max(32768),
  height: z.number().int().positive().max(32768) })
export const CalibrationDraftSchema = z.object({ slots: CalibrationSlotsSchema,
  assets: SlotConfigurationSchema, zoomFactor: z.number().min(0.25).max(5) })
export type CalibrationDraft = z.infer<typeof CalibrationDraftSchema>
export const PlatformCommandSchema = z.discriminatedUnion('operation', [
  z.object({ operation: z.enum(['state', 'reload', 'endCalibration']), platform: PlatformSchema }),
  z.object({ operation: z.literal('layout'), platform: PlatformSchema,
    bounds: BrowserRectangleSchema, visible: z.boolean() }),
  z.object({ operation: z.enum(['beginCalibration', 'draft']), platform: PlatformSchema,
    draft: CalibrationDraftSchema })
]).refine((r) => !('draft' in r) || r.draft.assets.platform === r.platform, 'Wrong platform')
export type PlatformCommand = z.infer<typeof PlatformCommandSchema>
export const BrowserSnapshotSchema = z.object({ session: PlatformSessionStateSchema,
  draft: CalibrationDraftSchema.nullable(), bounds: BrowserRectangleSchema,
  zoomFactor: z.number().min(0.25).max(5) })
export type BrowserSnapshot = z.infer<typeof BrowserSnapshotSchema>

export function defaultCalibration(): CalibrationSlot[] {
  return Array.from({ length: 9 }, (_, i) => ({ id: i + 1,
    bounds: { x: (i % 3) / 3, y: Math.floor(i / 3) / 3, width: 1 / 3, height: 1 / 3 } }))
}
export function normalizedToPixel(bounds: NormalizedBounds, width: number, height: number): NormalizedBounds {
  return { x: bounds.x * width, y: bounds.y * height, width: bounds.width * width, height: bounds.height * height }
}
export function adjustBounds(bounds: NormalizedBounds, dx: number, dy: number, resize: boolean): NormalizedBounds {
  const clamp = (v: number, min: number, max: number): number => Math.max(min, Math.min(max, v))
  return resize ? { ...bounds, width: clamp(bounds.width + dx, 0.01, 1 - bounds.x),
    height: clamp(bounds.height + dy, 0.01, 1 - bounds.y) }
    : { ...bounds, x: clamp(bounds.x + dx, 0, 1 - bounds.width), y: clamp(bounds.y + dy, 0, 1 - bounds.height) }
}
