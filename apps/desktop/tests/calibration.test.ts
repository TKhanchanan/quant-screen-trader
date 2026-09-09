import { randomUUID } from 'node:crypto'
import { expect, it, vi } from 'vitest'
import { defaultCalibration, deriveChartGrid, type ConfigurationRequest, type ConfigurationResult } from '@quant-screen-trader/shared-types'
import { prepareCalibration } from '../electron/main/calibration'
import type { PlatformBrowserManager } from '../electron/main/platform-browser'
import { createPlaceholderSlots } from '../src/renderer/src/features/slots/createPlaceholderSlots'

function fixture(geometrySource: 'AUTO' | 'MANUAL') {
  const id = randomUUID(), stamp = new Date().toISOString()
  const config: ConfigurationResult = { configuration: { platform: 'iqoption', slots: createPlaceholderSlots('iqoption') }, presets: [], activeCalibrationId: id,
    calibrations: [{ id, platform: 'iqoption', name: 'Old calibration', geometrySource, zoomFactor: 1,
      referenceBrowserWidth: 900, referenceBrowserHeight: 600, slots: defaultCalibration(), createdAt: stamp, updatedAt: stamp }] }
  const browser = { zoomFactor: .7, bounds: { x: 0, y: 0, width: 1400, height: 750 },
    grid: deriveChartGrid('iqoption', { x: .04, y: .08, width: .94, height: .83 }) }
  const request = vi.fn(async (input: ConfigurationRequest): Promise<ConfigurationResult> =>
    input.operation === 'saveCalibration' ? { ...config, calibrations: [{ ...config.calibrations[0]!, ...input, id }] } : config)
  const issued: string[] = []
  const browsers = { command: vi.fn((command: { operation: string }) => { issued.push(command.operation); return browser }),
    resolveGrid: vi.fn(async () => browser), useCalibration: vi.fn() }
  return { browsers, issued, request }
}
it('regenerates and resaves AUTO geometry at runtime zoom .7 instead of using old 1.0 bounds', async () => {
  const { browsers, issued, request } = fixture('AUTO')
  const saved = await prepareCalibration('iqoption', browsers as unknown as PlatformBrowserManager, request)
  expect(browsers.resolveGrid).toHaveBeenCalledOnce()
  expect(request).toHaveBeenLastCalledWith(expect.objectContaining({ operation: 'saveCalibration', geometrySource: 'AUTO', zoomFactor: .7,
    referenceBrowserWidth: 1400, referenceBrowserHeight: 750 }))
  expect(saved.calibrations[0]!.zoomFactor).toBe(.7)
  expect(issued.every(operation => operation === 'state')).toBe(true)
})
it('requires one-rectangle recalibration for a MANUAL zoom mismatch and preserves its geometry', async () => {
  const { browsers, request } = fixture('MANUAL')
  await expect(prepareCalibration('iqoption', browsers as unknown as PlatformBrowserManager, request)).rejects.toThrow('CALIBRATION_ZOOM_MISMATCH')
  expect(browsers.resolveGrid).not.toHaveBeenCalled()
  expect(request).toHaveBeenCalledOnce()
})
it('does not save AUTO guesses when visible separator detection fails', async () => {
  const { browsers, request } = fixture('AUTO')
  browsers.resolveGrid.mockRejectedValue(new Error('CANVAS_GEOMETRY_UNCERTAIN'))
  await expect(prepareCalibration('iqoption', browsers as unknown as PlatformBrowserManager, request)).rejects.toThrow('CANVAS_GEOMETRY_UNCERTAIN')
  expect(request).toHaveBeenCalledOnce()
})
