import {
  PlatformSlotSchema,
  type Platform,
  type PlatformSlot
} from '@quant-screen-trader/shared-types'

export const WORKSPACE_SLOT_COUNT = 9

// An unidentified slot has no asset identity. The renderer labels it "Unassigned";
// that word must never reach the configuration, detection or observation pipelines.
export function createPlaceholderSlots(platform: Platform): PlatformSlot[] {
  return Array.from({ length: WORKSPACE_SLOT_COUNT }, (_, index) =>
    PlatformSlotSchema.parse({
      id: index + 1,
      enabled: false,
      assetName: '',
      platform
    })
  )
}
