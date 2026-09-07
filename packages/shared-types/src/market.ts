import { z } from 'zod'

export const SourceTypeSchema = z.enum(['DOM', 'VISUAL', 'REPLAY', 'SYNTHETIC'])
export const QualityStateSchema = z.enum(['GOOD', 'DEGRADED', 'UNCERTAIN', 'STALE', 'INVALID'])
const unit = z.number().min(0).max(1)
export const DataQualitySchema = z.strictObject({ state: QualityStateSchema,
  confidence: unit, freshness: unit, completeness: unit, sourceReliability: unit,
  latencyMs: z.number().nonnegative() })
export const MarketObservationSchema = z.strictObject({
  id: z.uuid(), platform: z.enum(['capitalbear', 'iqoption']), slotId: z.number().int().min(1).max(9),
  assetName: z.string().trim().min(1).max(120), contextId: z.uuid(),
  observedAt: z.iso.datetime({ offset: true }), parsedAt: z.iso.datetime({ offset: true }), sourceType: SourceTypeSchema,
  price: z.number().positive().nullable(), payout: unit.nullable(), timerSeconds: z.number().int().min(0).max(86399).nullable(),
  parserConfidence: unit, dataQuality: DataQualitySchema,
  captureLatencyMs: z.number().nonnegative(), parseLatencyMs: z.number().nonnegative(),
  calibrationProfileId: z.uuid().nullable(), parserVersion: z.string().min(1).max(80)
}).refine((o) => Date.parse(o.parsedAt) >= Date.parse(o.observedAt), 'Parsing precedes observation')
export type MarketObservation = z.infer<typeof MarketObservationSchema>
export type SourceType = z.infer<typeof SourceTypeSchema>
export type DataQuality = z.infer<typeof DataQualitySchema>
export const ObservationStateSchema = z.enum(['DISABLED', 'WAITING', 'CAPTURING', 'PARSING', 'READY', 'DATA_UNCERTAIN', 'STALE', 'ERROR', 'PAUSED'])
export const MarketCommandSchema = z.strictObject({ platform: z.enum(['capitalbear', 'iqoption']),
  operation: z.enum(['start', 'stop', 'state']), intervalMs: z.number().int().min(250).max(10000).optional() })
export type MarketCommand = z.infer<typeof MarketCommandSchema>
export const SlotDataSchema = z.strictObject({ slotId: z.number().int().min(1).max(9), state: ObservationStateSchema,
  secondSamples: z.number().int().nonnegative(), m1Samples: z.number().int().nonnegative(), m1State: z.enum(['FORMING', 'CLOSED']).nullable(),
  observation: MarketObservationSchema.nullable(), dropped: z.number().int().nonnegative(),
  pixelBounds: z.strictObject({ x: z.number(), y: z.number(), width: z.number(), height: z.number() }).nullable() })
export const MarketSnapshotSchema = z.strictObject({ running: z.boolean(), intervalMs: z.number(),
  slots: z.array(SlotDataSchema).max(9), queueDepth: z.number(), dropped: z.number(), queueLagMs: z.number(),
  captureRate: z.number(), engineAvailable: z.boolean() })
export type MarketSnapshot = z.infer<typeof MarketSnapshotSchema>

const SeriesIdentity = { platform: z.enum(['capitalbear', 'iqoption']), slotId: z.number().int().min(1).max(9),
  assetName: z.string().min(1).max(120), contextId: z.uuid(), calibrationProfileId: z.uuid().nullable(), sourceType: SourceTypeSchema }
export const PriceSampleSchema = z.strictObject({ ...SeriesIdentity, timestamp: z.number().int(), bucketTime: z.number().int().nullable().default(null),
  price: z.number().positive(), quality: DataQualitySchema })
export type PriceSample = z.infer<typeof PriceSampleSchema>
export const CandleSchema = z.strictObject({ ...SeriesIdentity, timeframe: z.enum(['S5', 'M1', 'M5', 'M10']),
  openTime: z.number().int(), closeTime: z.number().int(), open: z.number().positive(), high: z.number().positive(),
  low: z.number().positive(), close: z.number().positive(), sampleCount: z.number().int().nonnegative(),
  expectedSamples: z.number().int().positive(), coverage: unit, gapDurationMs: z.number().int().nonnegative(),
  quality: QualityStateSchema, state: z.enum(['FORMING', 'CLOSED']) })
export type Candle = z.infer<typeof CandleSchema>

export const MarketBatchResultSchema = z.strictObject({ accepted: z.number().int().nonnegative(),
  queueDepth: z.number().int().nonnegative(), rejected: z.number().int().nonnegative(),
  slots: z.array(z.strictObject({ platform: z.enum(['capitalbear', 'iqoption']), slotId: z.number().int().min(1).max(9),
    contextId: z.uuid(), secondSamples: z.number().int().nonnegative(), m1Samples: z.number().int().nonnegative(), m1State: z.enum(['FORMING', 'CLOSED']).nullable() })).max(18) })
