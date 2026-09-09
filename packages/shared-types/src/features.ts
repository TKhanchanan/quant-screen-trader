import { z } from 'zod'

/** Read-only Phase 6 diagnostics. The desktop never computes features itself. */
export const FeatureTimeframeSchema = z.strictObject({
  timeframe: z.enum(['S5', 'M1', 'M5', 'M10']),
  barCount: z.number().int().nonnegative(),
  hydratedBars: z.number().int().nonnegative(),
  status: z.enum(['WARMING', 'READY', 'DEGRADED', 'INVALID']).nullable(),
  featureTime: z.number().int().nullable()
})
export type FeatureTimeframe = z.infer<typeof FeatureTimeframeSchema>
export const FeatureSlotSchema = z.looseObject({
  platform: z.enum(['capitalbear', 'iqoption']),
  slotId: z.number().int().min(1).max(9),
  assetName: z.string(),
  primaryTimeframe: z.enum(['S5', 'M1', 'M5', 'M10']),
  microSamples: z.number().int().nonnegative(),
  timeframes: z.array(FeatureTimeframeSchema)
})
export type FeatureSlot = z.infer<typeof FeatureSlotSchema>
export const FeatureStateSchema = z.strictObject({
  featureVersion: z.string().min(1).max(40),
  available: z.boolean(),
  slots: z.array(FeatureSlotSchema).max(18)
})
export type FeatureState = z.infer<typeof FeatureStateSchema>
