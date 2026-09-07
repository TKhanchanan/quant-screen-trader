import { describe, expect, it } from 'vitest'
import { adjustBounds, defaultCalibration, normalizedToPixel, NormalizedBoundsSchema,
  SlotConfigurationSchema, PlatformCommandSchema, ConfigurationRequestSchema, CalibrationSlotsSchema,
  PlatformSessionStateSchema } from '@quant-screen-trader/shared-types'
import { getPlatformConfig, allowedLoginNavigation, allowedNavigation, safeOrigin } from '../electron/platforms/config'
import { createPlaceholderSlots } from '../src/renderer/src/features/slots/createPlaceholderSlots'

describe('platform configuration and IPC contracts', () => {
  it('allows published platform login hops and Google callbacks only in their own workspace', () => {
    const capital = getPlatformConfig('capitalbear', {}), iq = getPlatformConfig('iqoption', {})
    for (const [owner, other] of [[capital, iq], [iq, capital]]) {
      for (const origin of owner!.allowedOrigins) {
        const callback = `${origin}/?social=google`
        const google = `https://accounts.google.com/o/oauth2/v2/auth?redirect_uri=${encodeURIComponent(callback)}`
        expect(allowedNavigation(owner!, callback)).toBe(true)
        expect(allowedNavigation(other!, callback)).toBe(false)
        expect(allowedLoginNavigation(owner!, google)).toBe(true)
        expect(allowedLoginNavigation(other!, google)).toBe(false)
      }
    }
    expect(allowedNavigation(capital, 'https://trade.capitalbear.com/en/login')).toBe(true)
    expect(allowedNavigation(iq, 'https://auth.iqoption.com/api/v3/oauth/login')).toBe(true)
    expect(allowedNavigation(iq, 'https://sc.iqoption.com/en/login')).toBe(true)
  })
  it('uses independent persistent profiles and canonical platform names', () => {
    const capital = getPlatformConfig('capitalbear', {})
    const iq = getPlatformConfig('iqoption', {})
    expect(capital.displayName).toBe('CapitalBear')
    expect(iq.displayName).toBe('IQ Option')
    expect(capital.startUrl).toBe('https://trade.capitalbear.com/traderoom')
    expect(iq.startUrl).toBe('https://iqoption.com/traderoom')
    expect(capital.sessionPartition).toBe('persist:capitalbear-profile')
    expect(iq.sessionPartition).toBe('persist:iqoption-profile')
    expect(capital.sessionPartition).not.toBe(iq.sessionPartition)
    expect(() => getPlatformConfig('other')).toThrow()
  })
  it.each(['http://capitalbear.com', 'https://evil.test', 'https://capitalbear.com.evil.test',
    'https://user:password@capitalbear.com', 'file:///tmp/local', 'javascript:alert(1)'])('rejects navigation to %s', (url) => {
    expect(allowedNavigation(getPlatformConfig('capitalbear', {}), url)).toBe(false)
    expect(() => getPlatformConfig('capitalbear', { QST_CAPITALBEAR_START_URL: url })).toThrow()
  })
  it('permits safe configured paths and strips all private URL components from state', () => {
    expect(getPlatformConfig('capitalbear', { QST_CAPITALBEAR_START_URL: 'https://capitalbear.com/en/' }).startUrl).toContain('/en/')
    expect(safeOrigin('https://capitalbear.com/account/private?token=secret#cookie')).toBe('https://capitalbear.com')
    expect(PlatformCommandSchema.safeParse({ operation: 'reload', platform: 'bad' }).success).toBe(false)
    expect(ConfigurationRequestSchema.safeParse({ operation: 'sql', platform: 'iqoption' }).success).toBe(false)
  })
  it.each(['STARTING', 'LOADING', 'LOGIN_REQUIRED', 'READY', 'DISCONNECTED', 'ERROR'])('validates %s state', (state) => {
    expect(PlatformSessionStateSchema.safeParse({ platform: 'iqoption', state, loadState: 'idle', lastUpdatedAt: '2026-01-01T00:00:00Z' }).success).toBe(true)
  })
})
describe('nine user-configured slots', () => {
  const configuration = (): { platform: 'capitalbear'; slots: ReturnType<typeof createPlaceholderSlots> } => ({ platform: 'capitalbear', slots: createPlaceholderSlots('capitalbear') })
  it('accepts arbitrary user assets and preserves disabled configuration', () => {
    const input = configuration()
    input.slots[0] = { id: 1, platform: 'capitalbear', enabled: true, assetName: 'Apple OTC' }
    input.slots[1] = { id: 2, platform: 'capitalbear', enabled: false, assetName: 'Gold' }
    expect(SlotConfigurationSchema.parse(input).slots[1]?.assetName).toBe('Gold')
  })
  it.each(['missing', 'duplicate', 'wrong-platform', 'blank-enabled'])('rejects %s slot input', (kind) => {
    const input = configuration()
    if (kind === 'missing') input.slots.pop()
    if (kind === 'duplicate') input.slots[0] = { ...input.slots[1]!, id: 2 }
    if (kind === 'wrong-platform') input.slots[0] = { ...input.slots[0]!, platform: 'iqoption' }
    if (kind === 'blank-enabled') input.slots[0] = { ...input.slots[0]!, enabled: true, assetName: ' ' }
    expect(SlotConfigurationSchema.safeParse(input).success).toBe(false)
    expect(ConfigurationRequestSchema.safeParse({ ...input, operation: 'savePreset', name: 'Test' }).success).toBe(false)
  })
})
describe('normalized calibration', () => {
  it.each([{ x: -0.1 }, { y: -0.1 }, { width: 0 }, { height: 0 }, { x: 0.8 }, { y: 0.8 }, { width: NaN }])('rejects invalid bounds %o', (patch) => {
    expect(NormalizedBoundsSchema.safeParse({ x: 0, y: 0, width: 0.5, height: 0.5, ...patch }).success).toBe(false)
  })
  it('generates deterministic nine-slot grid with no overflow', () => {
    const slots = defaultCalibration()
    expect(CalibrationSlotsSchema.parse(slots).map((s) => s.id)).toEqual([1,2,3,4,5,6,7,8,9])
    expect(slots).toEqual(defaultCalibration())
    expect(slots.every((s) => NormalizedBoundsSchema.safeParse(s.bounds).success)).toBe(true)
    expect(CalibrationSlotsSchema.safeParse([...slots.slice(1), slots[1]]).success).toBe(false)
  })
  it.each([[1320, 900], [1920, 1080], [800, 1200]])('scales proportionally at %i × %i', (width, height) => {
    expect(normalizedToPixel({ x: .25, y: .5, width: .5, height: .25 }, width, height))
      .toEqual({ x: width / 4, y: height / 2, width: width / 2, height: height / 4 })
  })
  it('clamps drag and resize at all edges', () => {
    const bounds = { x: .2, y: .2, width: .3, height: .3 }
    for (const resize of [true, false]) for (const delta of [-100, 100]) {
      expect(NormalizedBoundsSchema.safeParse(adjustBounds(bounds, delta, delta, resize)).success).toBe(true)
    }
  })
})
