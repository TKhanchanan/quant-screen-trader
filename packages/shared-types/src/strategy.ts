import { z } from 'zod'

/**
 * Read-only Phase 7 diagnostics. The desktop never computes a regime, a strategy opinion or
 * an ensemble itself, and there is no command channel here: this contract carries analysis
 * out of the engine and nothing back into it.
 */
export const DirectionSchema = z.enum(['UP', 'DOWN', 'NEUTRAL', 'SKIP'])
export type Direction = z.infer<typeof DirectionSchema>

export const RegimeSchema = z.enum([
  'TREND_UP', 'TREND_DOWN', 'RANGE', 'BREAKOUT_UP', 'BREAKOUT_DOWN',
  'VOLATILITY_EXPANSION', 'VOLATILITY_COMPRESSION', 'NOISY', 'UNCERTAIN'
])
export type Regime = z.infer<typeof RegimeSchema>

export const StrategyVoteSchema = z.strictObject({
  strategyId: z.string().min(1).max(48),
  direction: DirectionSchema,
  confidence: z.number().min(0).max(1)
})
export type StrategyVote = z.infer<typeof StrategyVoteSchema>

export const StrategySlotSchema = z.looseObject({
  platform: z.enum(['capitalbear', 'iqoption']),
  slotId: z.number().int().min(1).max(9),
  assetName: z.string(),
  asOf: z.number().int(),
  primaryRegime: RegimeSchema,
  regimeConfidence: z.number().min(0).max(1),
  direction: DirectionSchema,
  confidence: z.number().min(0).max(1),
  eligibleStrategies: z.number().int().nonnegative(),
  activeStrategies: z.number().int().nonnegative(),
  votes: z.array(StrategyVoteSchema).max(16),
  vetoes: z.array(z.string()).max(12)
})
export type StrategySlot = z.infer<typeof StrategySlotSchema>

/** What the engine returns. Loose: it also reports counters the desktop does not render. */
export const StrategyEngineStateSchema = z.looseObject({
  featureVersion: z.string().min(1).max(40),
  regimeVersion: z.string().min(1).max(40),
  strategyVersion: z.string().min(1).max(40),
  slots: z.array(StrategySlotSchema).max(18)
})
export type StrategyEngineState = z.infer<typeof StrategyEngineStateSchema>

/** What the desktop bridge delivers. `available` is added by the main process, never the engine. */
export const StrategyStateSchema = z.strictObject({
  featureVersion: z.string().min(1).max(40),
  regimeVersion: z.string().min(1).max(40),
  strategyVersion: z.string().min(1).max(40),
  available: z.boolean(),
  slots: z.array(StrategySlotSchema).max(18)
})
export type StrategyState = z.infer<typeof StrategyStateSchema>

/** Short display names for the developer diagnostics line. Ids stay the wire contract. */
export const STRATEGY_LABELS: Record<string, string> = {
  trend_follow_v1: 'Trend',
  momentum_continuation_v1: 'Momentum',
  breakout_v1: 'Breakout',
  mean_reversion_v1: 'MeanRev',
  micro_impulse_v1: 'Micro',
  trend_pullback_v1: 'Pullback'
}

export function voteLabel(vote: StrategyVote): string {
  return `${STRATEGY_LABELS[vote.strategyId] ?? vote.strategyId} ${vote.direction}` +
    (vote.direction === 'UP' || vote.direction === 'DOWN' ? ` ${vote.confidence.toFixed(2)}` : '')
}

export function percent(value: number): string {
  return `${Math.round(value * 100)}%`
}
