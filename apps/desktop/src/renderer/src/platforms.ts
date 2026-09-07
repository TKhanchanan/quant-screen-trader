import type { Platform } from '@quant-screen-trader/shared-types'

interface PlatformDetails {
  name: string
  shortName: string
}

export const PLATFORM_DETAILS: Record<Platform, PlatformDetails> = {
  capitalbear: {
    name: 'CapitalBear',
    shortName: 'CB'
  },
  iqoption: {
    name: 'IQ Option',
    shortName: 'IQ'
  }
}

export const PLATFORMS = ['capitalbear', 'iqoption'] as const satisfies readonly Platform[]
