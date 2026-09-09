import { describe, expect, it } from 'vitest'
import {
  WORKSPACE_SLOT_COUNT,
  createPlaceholderSlots
} from '../src/renderer/src/features/slots/createPlaceholderSlots'

describe('createPlaceholderSlots', () => {
  it.each(['capitalbear', 'iqoption'] as const)(
    'creates exactly nine ordered slots for %s',
    (platform) => {
      const slots = createPlaceholderSlots(platform)

      expect(slots).toHaveLength(WORKSPACE_SLOT_COUNT)
      expect(slots.map((slot) => slot.id)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9])
      expect(slots.every((slot) => slot.platform === platform)).toBe(true)
      expect(slots.every((slot) => !slot.enabled)).toBe(true)
      expect(slots.every((slot) => slot.assetName === '')).toBe(true)
      expect(slots.some((slot) => slot.assetName === 'Unassigned')).toBe(false)
    }
  )
})
