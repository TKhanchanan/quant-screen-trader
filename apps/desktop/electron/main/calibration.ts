import { calibrationZoomMatches, isAutoCalibration, type ConfigurationRequest, type ConfigurationResult, type Platform } from '@quant-screen-trader/shared-types'
import type { PlatformBrowserManager } from './platform-browser'

/** Called by explicit Sync/Start and AUTO resize recovery; never changes broker zoom. */
export async function prepareCalibration(platform: Platform, browsers: PlatformBrowserManager,
  request: (input: ConfigurationRequest) => Promise<ConfigurationResult>): Promise<ConfigurationResult> {
  const before = await request({ platform, operation: 'get' })
  const profile = before.calibrations.find(p => p.id === before.activeCalibrationId)
  const browser = browsers.command({ platform, operation: 'state' })
  if (profile && !isAutoCalibration(profile)) {
    if (!calibrationZoomMatches(profile, browser)) throw new Error('CALIBRATION_ZOOM_MISMATCH: reopen Calibrate Chart Area and adjust one outer rectangle.')
    browsers.useCalibration(platform, profile)
    return before
  }
  const resolved = await browsers.resolveGrid(platform)
  if (!resolved.grid) throw new Error('CANVAS_GEOMETRY_UNCERTAIN: use Calibrate Chart Area.')
  const slots = resolved.grid.slots.map(s => ({ id: s.slotId, bounds: s.chartBounds }))
  if (profile && calibrationZoomMatches(profile, resolved) && profile.referenceBrowserWidth === resolved.bounds.width &&
    profile.referenceBrowserHeight === resolved.bounds.height && JSON.stringify(slots) === JSON.stringify(profile.slots)) return before
  const latest = await request({ platform, operation: 'get' })
  if (JSON.stringify(latest) !== JSON.stringify(before)) throw new Error('Calibration changed during detection; retry.')
  return request({ platform, operation: 'saveCalibration', ...(profile ? { id: profile.id } : {}),
    name: profile?.name ?? 'Auto Chart Grid', geometrySource: 'AUTO', slots,
    referenceBrowserWidth: resolved.bounds.width, referenceBrowserHeight: resolved.bounds.height, zoomFactor: resolved.zoomFactor })
}
