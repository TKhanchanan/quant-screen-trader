import { z } from 'zod'

export const POLICY_VERSION = 'qst-policy-v1'
export const WATCHDOG_VERSION = 'qst-watchdog-v1'
export const PolicyModeSchema = z.enum(['OFF', 'SHADOW', 'PAPER_GATED'])
export const PolicyActionSchema = z.enum(['ALLOW', 'WATCH', 'SKIP'])
export const PolicyEvidenceStatusSchema = z.enum([
  'NO_EVIDENCE', 'INSUFFICIENT_SAMPLE', 'UNSTABLE', 'DIRECTIONAL_ONLY',
  'MONETARY_UNVERIFIED', 'VALIDATED', 'STALE', 'DRIFTED', 'VERSION_MISMATCH', 'INVALID'
])
export const PolicyDecisionSchema = z.looseObject({
  decisionId: z.uuid(), platform: z.enum(['capitalbear', 'iqoption']),
  slotId: z.number().int().min(1).max(9).nullable(), assetName: z.string().nullable(),
  asOf: z.number().int(), decisionAvailableAt: z.number().int(),
  baselineAction: PolicyActionSchema, policyAction: PolicyActionSchema,
  originalDirection: z.enum(['UP', 'DOWN', 'NEUTRAL', 'SKIP']).nullable(),
  mode: PolicyModeSchema, evidenceStatus: PolicyEvidenceStatusSchema,
  reasons: z.array(z.string()), analyticalOnly: z.literal(true),
  appliedToLiveExecution: z.literal(false)
})
export const PolicyStateSchema = z.object({
  policyVersion: z.string(), watchdogVersion: z.string(), mode: PolicyModeSchema,
  snapshotId: z.uuid().nullable(), evidenceCutoffTime: z.number().int().nullable(),
  evidenceStatus: PolicyEvidenceStatusSchema,
  watchdogState: z.enum(['HEALTHY', 'WARMING', 'WARNING', 'DRIFTED',
    'INSUFFICIENT_DATA', 'ACCOUNTING_UNAVAILABLE', 'VERSION_MISMATCH']),
  decisions: z.array(PolicyDecisionSchema).max(20), busy: z.boolean(),
  error: z.string().nullable(), analyticalOnly: z.literal(true)
})
export type PolicyState = z.infer<typeof PolicyStateSchema>
export const emptyPolicyState = (error: string | null = null): PolicyState => ({
  policyVersion: POLICY_VERSION, watchdogVersion: WATCHDOG_VERSION, mode: 'SHADOW',
  snapshotId: null, evidenceCutoffTime: null, evidenceStatus: 'NO_EVIDENCE',
  watchdogState: 'WARMING', decisions: [], busy: false, error, analyticalOnly: true
})
