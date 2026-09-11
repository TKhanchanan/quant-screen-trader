import { z } from 'zod'
import type { AssetSyncCommand, AssetSyncState } from './assets'
import type { FeatureState } from './features'
import type { StrategyState } from './strategy'
import type { OpportunityState } from './opportunity'
import type { PaperState } from './paper'
import type { AnalyticsState } from './analytics'
import type { SessionGuardCommand, SessionGuardState } from './session-guard'
import type { ExecutionCommand, ExecutionState } from './execution'
import type { MarketCommand, MarketSnapshot } from './market'

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
  assetMode: z.enum(['AUTO', 'MANUAL']).optional(),
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
  getEngineHealth: 'engine:get-health', market: 'market:command', assetSync: 'assets:sync',
  features: 'features:state', strategy: 'strategy:state', opportunities: 'opportunities:state',
  paper: 'paper:state', sessionGuard: 'session-guard:command', analytics: 'analytics:state',
  openWorkspace: 'workspace:open', openTrading: 'trading:open', execution: 'execution:command',
  platformCommand: 'platform:command', configuration: 'configuration:request'
} as const

export interface DesktopBridge {
  assetSync: (request: AssetSyncCommand) => Promise<AssetSyncState>
  market: (request: MarketCommand) => Promise<MarketSnapshot>
  getEngineHealth: () => Promise<EngineHealthSnapshot>
  openWorkspace: (platform: Platform) => Promise<void>
  /** The board and execution controls, in their own window so they cost the charts no height. */
  openTrading: (platform: Platform) => Promise<void>
  platformCommand: (request: PlatformCommand) => Promise<BrowserSnapshot>
  configuration: (request: ConfigurationRequest) => Promise<ConfigurationResult>
  features: (platform: Platform) => Promise<FeatureState>
  strategy: (platform: Platform) => Promise<StrategyState>
  opportunities: (platform: Platform) => Promise<OpportunityState>
  /** Read-only Phase 9 outcomes. A paper WIN is a measurement, never an order. */
  paper: (platform: Platform) => Promise<PaperState>
  /** Phase 9.5 daily accounting. It can stop the day; it can never start anything. */
  sessionGuard: (request: SessionGuardCommand) => Promise<SessionGuardState>
  /** Read-only Phase 10 research. It measures what happened; it cannot apply what it finds. */
  analytics: (platform: Platform) => Promise<AnalyticsState>
  /** The only bridge method that can lead to a press. Workspace main frames only. */
  execution: (request: ExecutionCommand) => Promise<ExecutionState>
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
export const ChartGeometrySourceSchema = z.enum(['AUTO', 'MANUAL', 'LEGACY'])
export type ChartGeometrySource = z.infer<typeof ChartGeometrySourceSchema>
export const ChartSlotGeometrySchema = z.object({
  slotId: SlotIdSchema,
  chartBounds: NormalizedBoundsSchema,
  assetTitleBounds: NormalizedBoundsSchema.optional(),
  priceBounds: NormalizedBoundsSchema.optional(),
  timerBounds: NormalizedBoundsSchema.optional(),
  payoutBounds: NormalizedBoundsSchema.optional()
})
export type ChartSlotGeometry = z.infer<typeof ChartSlotGeometrySchema>
export const ChartGridGeometrySchema = z.object({
  platform: PlatformSchema,
  bounds: NormalizedBoundsSchema,
  source: ChartGeometrySourceSchema,
  confidence: z.number().min(0).max(1),
  slots: z.array(ChartSlotGeometrySchema).length(9)
    .refine(slots => slots.map(slot => slot.slotId).join(',') === '1,2,3,4,5,6,7,8,9',
      'Chart slots must be in row-major Slot 1–9 order')
})
export type ChartGridGeometry = z.infer<typeof ChartGridGeometrySchema>
const NamedRecord = { id: z.uuid(), platform: PlatformSchema, name: z.string().trim().min(1).max(120),
  createdAt: z.iso.datetime(), updatedAt: z.iso.datetime() }
export const CalibrationProfileSchema = z.object({ ...NamedRecord,
  geometrySource: z.enum(['AUTO', 'MANUAL']).optional(),
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
  z.object({ operation: z.literal('syncAssets'), ...SlotConfigurationSchema.shape, expectedSlots: z.array(PlatformSlotSchema).length(9), expectedCalibrationVersion: z.string().max(128).nullable() }),
  z.object({ operation: z.literal('savePreset'), ...RecordInput, slots: z.array(PlatformSlotSchema).length(9) }),
  z.object({ operation: z.literal('saveCalibration'), ...RecordInput,
    ...CalibrationProfileSchema.omit({ id: true, createdAt: true, updatedAt: true }).shape }),
  z.object({ operation: z.enum(['deletePreset', 'loadPreset', 'deleteCalibration', 'loadCalibration']),
    platform: PlatformSchema, id: z.uuid() })
]).superRefine((request, ctx) => {
  if ((request.operation === 'slots' || request.operation === 'savePreset' || request.operation === 'syncAssets') &&
      !SlotConfigurationSchema.safeParse(request).success)
    ctx.addIssue({ code: 'custom', message: 'Invalid nine-slot configuration' })
})
export type ConfigurationRequest = z.infer<typeof ConfigurationRequestSchema>
export const PlatformSessionStateSchema = z.object({
  platform: PlatformSchema,
  state: z.enum(['STARTING', 'LOADING', 'LOGIN_REQUIRED', 'UNKNOWN', 'READY', 'DISCONNECTED', 'ERROR']),
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
  z.object({ operation: z.enum(['state', 'reload', 'endCalibration', 'resolveGrid']), platform: PlatformSchema }),
  z.object({ operation: z.literal('layout'), platform: PlatformSchema,
    bounds: BrowserRectangleSchema, visible: z.boolean() }),
  z.object({ operation: z.enum(['beginCalibration', 'draft']), platform: PlatformSchema,
    draft: CalibrationDraftSchema })
]).refine((r) => !('draft' in r) || r.draft.assets.platform === r.platform, 'Wrong platform')
export type PlatformCommand = z.infer<typeof PlatformCommandSchema>
export const BrowserSnapshotSchema = z.object({ session: PlatformSessionStateSchema,
  draft: CalibrationDraftSchema.nullable(), bounds: BrowserRectangleSchema,
  grid: ChartGridGeometrySchema.nullable().optional(),
  zoomFactor: z.number().min(0.25).max(5) })
export type BrowserSnapshot = z.infer<typeof BrowserSnapshotSchema>

export function isAutoCalibration(profile: CalibrationProfile): boolean {
  return profile.geometrySource === 'AUTO' || (!profile.geometrySource && profile.name === 'Auto Chart Grid')
}
export function calibrationZoomMatches(profile: Pick<CalibrationProfile, 'zoomFactor'>, browser: Pick<BrowserSnapshot, 'zoomFactor'>): boolean {
  return Math.abs(profile.zoomFactor - browser.zoomFactor) <= .001
}

const AUTO_GRID_BOUNDS: NormalizedBounds = { x: .05, y: .12, width: .95, height: .78 }
export function deriveChartGrid(platform: Platform, bounds: NormalizedBounds, source: ChartGeometrySource = 'AUTO',
  confidence = source === 'AUTO' ? .96 : 1): ChartGridGeometry {
  NormalizedBoundsSchema.parse(bounds)
  const width = bounds.width / 3, height = bounds.height / 3
  const slots = Array.from({ length: 9 }, (_, index): ChartSlotGeometry => {
    const column = index % 3, row = Math.floor(index / 3)
    const chartBounds = { x: bounds.x + column * width, y: bounds.y + row * height, width, height }
    const region = (x: number, y: number, regionWidth: number, regionHeight: number): NormalizedBounds => ({
      x: chartBounds.x + chartBounds.width * x, y: chartBounds.y + chartBounds.height * y,
      width: chartBounds.width * regionWidth, height: chartBounds.height * regionHeight
    })
    return { slotId: index + 1, chartBounds,
      assetTitleBounds: region(.02, .02, .5, .18),
      priceBounds: region(.68, .14, .14, .74),
      timerBounds: region(.72, .25, .25, .5),
      payoutBounds: region(.72, .02, .25, .2) }
  })
  return ChartGridGeometrySchema.parse({ platform, bounds, source, confidence, slots })
}
export function chartGridBounds(slots: CalibrationSlot[]): NormalizedBounds {
  CalibrationSlotsSchema.parse(slots)
  const left = Math.min(...slots.map(slot => slot.bounds.x)), top = Math.min(...slots.map(slot => slot.bounds.y))
  const right = Math.max(...slots.map(slot => slot.bounds.x + slot.bounds.width))
  const bottom = Math.max(...slots.map(slot => slot.bounds.y + slot.bounds.height))
  return NormalizedBoundsSchema.parse({ x: left, y: top, width: right - left, height: bottom - top })
}
export function calibrationToChartGrid(platform: Platform, slots: CalibrationSlot[], source: ChartGeometrySource = 'MANUAL'): ChartGridGeometry {
  const bounds = chartGridBounds(slots)
  const regular = deriveChartGrid(platform, bounds, source)
  const ordered = [...CalibrationSlotsSchema.parse(slots)].sort((a, b) => a.id - b.id)
  return ChartGridGeometrySchema.parse({ ...regular, slots: ordered.map((slot, index) => {
    const auto = regular.slots[index]!, chartBounds = slot.bounds
    const remap = (region: NormalizedBounds | undefined): NormalizedBounds | undefined => region && ({
      x: chartBounds.x + (region.x - auto.chartBounds.x) / auto.chartBounds.width * chartBounds.width,
      y: chartBounds.y + (region.y - auto.chartBounds.y) / auto.chartBounds.height * chartBounds.height,
      width: region.width / auto.chartBounds.width * chartBounds.width,
      height: region.height / auto.chartBounds.height * chartBounds.height
    })
    return { slotId: slot.id, chartBounds, assetTitleBounds: remap(auto.assetTitleBounds),
      priceBounds: remap(auto.priceBounds), timerBounds: remap(auto.timerBounds), payoutBounds: remap(auto.payoutBounds) }
  }) })
}
export function defaultChartGrid(platform: Platform): ChartGridGeometry {
  return deriveChartGrid(platform, AUTO_GRID_BOUNDS, 'AUTO', 0)
}
export function defaultCalibration(platform: Platform = 'capitalbear'): CalibrationSlot[] {
  return defaultChartGrid(platform).slots.map(slot => ({ id: slot.slotId, bounds: slot.chartBounds }))
}
export function legacyDefaultCalibration(): CalibrationSlot[] {
  return Array.from({ length: 9 }, (_, index) => ({ id: index + 1,
    bounds: { x: (index % 3) / 3, y: Math.floor(index / 3) / 3, width: 1 / 3, height: 1 / 3 } }))
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

export * from './market'

export * from './assets'

export * from './features'

export * from './strategy'

export * from './opportunity'
export * from './execution'
export * from './paper'
export * from './session-guard'
export * from './analytics'
