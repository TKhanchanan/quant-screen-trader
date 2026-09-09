import { z } from 'zod'
const Platform = z.enum(['capitalbear', 'iqoption'])
export const DetectedAssetSchema = z.strictObject({ platform: Platform, slotId: z.number().int().min(1).max(9),
  state: z.enum(['DETECTED', 'UNCERTAIN', 'NOT_FOUND']), assetName: z.string().min(1).max(120).nullable(),
  displayName: z.string().min(1).max(120).nullable(), canonicalAssetId: z.string().max(160).nullable(),
  source: z.enum(['DOM', 'OCR']), confidence: z.number().min(0).max(1), detectedAt: z.iso.datetime(),
  evidenceType: z.enum(['CHART_LABEL', 'LABEL_TOOLTIP', 'CALIBRATED_OCR', 'NO_MAPPING']),
  tabIndex: z.number().int().min(1).max(9).optional(),
  pixelBounds: z.object({ x: z.number(), y: z.number(), width: z.number(), height: z.number() }).optional(),
  rawOCR: z.array(z.string()).optional()
}).refine(s => s.state !== 'DETECTED' || (s.assetName !== null && s.confidence >= .9), 'Detected assets require reliable identity')
export type DetectedAsset = z.infer<typeof DetectedAssetSchema>
export const AssetDetectionResultSchema = z.strictObject({ platform: Platform, slots: z.array(DetectedAssetSchema).length(9),
  overallConfidence: z.number().min(0).max(1), durationMs: z.number().nonnegative() })
  .refine(r => new Set(r.slots.map(s => s.slotId)).size === 9 && r.slots.every(s => s.platform === r.platform), 'Invalid platform slots')
export type AssetDetectionResult = z.infer<typeof AssetDetectionResultSchema>
export const AssetSyncCommandSchema = z.strictObject({ platform: Platform, operation: z.enum(['sync', 'state', 'auto']),
  enabled: z.boolean().optional(), intervalMs: z.number().int().min(2000).max(30000).optional(),
  stableChecks: z.number().int().min(2).max(10).optional() })
export type AssetSyncCommand = z.infer<typeof AssetSyncCommandSchema>
export const AssetSyncStateSchema = z.strictObject({ auto: z.boolean(), busy: z.boolean(), intervalMs: z.number(), stableChecks: z.number(),
  detection: AssetDetectionResultSchema.nullable(), applied: z.number().int().nonnegative(),
  manualPreserved: z.number().int().nonnegative(), error: z.string().nullable(), revision: z.number().int().nonnegative() })
export type AssetSyncState = z.infer<typeof AssetSyncStateSchema>
