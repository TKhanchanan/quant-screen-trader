import {
  PlatformSlotSchema,
  type Platform,
  type PlatformSlot
} from '@quant-screen-trader/shared-types'

export const WORKSPACE_SLOT_COUNT = 9

export function createPlaceholderSlots(platform: Platform): PlatformSlot[] {
  return Array.from({ length: WORKSPACE_SLOT_COUNT }, (_, index) =>
    PlatformSlotSchema.parse({
      id: index + 1,
      enabled: false,
      assetName: 'Unassigned',
      platform
    })
  )
}
