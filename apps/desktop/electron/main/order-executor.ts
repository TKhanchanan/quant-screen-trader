import { calibrationToChartGrid, normalizedToPixel, OrderDirectionSchema, ControlMapSchema,
  type CalibrationProfile, type ControlMap, type OrderDirection, type Platform, type SlotControls } from '@quant-screen-trader/shared-types'
import { findOrderButtons, orderPanelBounds, type PanelImage } from './order-panel'
import { normalizeBitmap } from './market-providers'
import type { PlatformBrowserManager } from './platform-browser'

/**
 * Measures the broker's own entry controls, and presses one.
 *
 * Everything here is deliberately re-measured rather than remembered. A control map is a set of
 * coordinates on a canvas the broker can re-lay at any moment, so it carries the surface revision
 * and zoom it was taken at, and a press refuses the moment either has moved.
 */

/** How much of the panel must change after a press before it counts as confirmed. */
const VERIFY_DELTA = 6
const VERIFY_DELAY_MS = 350

export interface PressResult {
  pressedAt: number
  latencyMs: number
  verified: boolean
  reasons: string[]
}

function cropPanel(image: Electron.NativeImage, panel: { x: number; y: number; width: number; height: number }): PanelImage {
  const crop = image.crop(panel), size = crop.getSize()
  return { width: size.width, height: size.height, bgra: crop.toBitmap() }
}

/** Mean absolute grayscale difference. Two captures of an idle panel sit close to zero. */
function panelDelta(before: PanelImage, after: PanelImage): number {
  if (before.width !== after.width || before.height !== after.height) return 255
  const a = normalizeBitmap(before.bgra, before.width, before.height, undefined, false).grayscale
  const b = normalizeBitmap(after.bgra, after.width, after.height, undefined, false).grayscale
  let total = 0
  for (let i = 0; i < a.length; i++) total += Math.abs(a[i]! - b[i]!)
  return total / a.length
}

export class OrderExecutor {
  constructor(private readonly browsers: PlatformBrowserManager) {}

  /**
   * Locate both controls in every cell from a single capture.
   *
   * Slots are keyed by canvas cell, the same identity the capture pipeline uses, so a caller
   * still has to resolve a configured slot to its cell — and re-prove the tab identity — before
   * it can turn one of these coordinates into a press.
   */
  async calibrate(platform: Platform, profile: CalibrationProfile, directionForGreen: OrderDirection): Promise<ControlMap> {
    OrderDirectionSchema.parse(directionForGreen)
    const { image, size, surface } = await this.browsers.captureSurface(platform)
    if (!surface.gridReady) throw new Error('CONTROLS_UNCALIBRATED: verify the chart grid before measuring order controls.')
    const grid = calibrationToChartGrid(platform, profile.slots, 'LEGACY')
    const scaleX = size.width / surface.bounds.width, scaleY = size.height / surface.bounds.height
    const slots: SlotControls[] = [], reasons: string[] = []
    for (const cell of grid.slots) {
      let panel
      try { panel = orderPanelBounds(platform, cell.chartBounds, surface.bounds.width, surface.zoomFactor) }
      catch { reasons.push(`SLOT_${cell.slotId}_NO_PANEL`); continue }
      const roi = normalizedToPixel(panel, surface.bounds.width, surface.bounds.height)
      const pixels = { x: Math.max(0, Math.floor(roi.x * scaleX)), y: Math.max(0, Math.floor(roi.y * scaleY)),
        width: Math.max(1, Math.floor(roi.width * scaleX)), height: Math.max(1, Math.floor(roi.height * scaleY)) }
      if (pixels.x + pixels.width > size.width || pixels.y + pixels.height > size.height) {
        reasons.push(`SLOT_${cell.slotId}_PANEL_OFF_SURFACE`); continue
      }
      let reading
      try { reading = findOrderButtons(cropPanel(image, pixels)) }
      catch { reasons.push(`SLOT_${cell.slotId}_UNREADABLE`); continue }
      if (!reading.green || !reading.red) {
        reasons.push(`SLOT_${cell.slotId}_${reading.reasons.join('+') || 'NO_CONTROLS'}`); continue
      }
      // Back to normalized surface coordinates: the map has to survive a window resize, and the
      // press path multiplies by the browser's own bounds rather than by a capture scale.
      const center = (control: NonNullable<typeof reading.green>): { x: number; y: number } => ({
        x: (pixels.x + control.bounds.x + control.bounds.width / 2) / size.width,
        y: (pixels.y + control.bounds.y + control.bounds.height / 2) / size.height
      })
      const green = center(reading.green), red = center(reading.red)
      slots.push({ slotId: cell.slotId, panelBounds: panel, confidence: reading.confidence,
        higher: directionForGreen === 'HIGHER' ? green : red,
        lower: directionForGreen === 'HIGHER' ? red : green })
    }
    return ControlMapSchema.parse({ platform, zoomFactor: surface.zoomFactor, surfaceRevision: surface.revision,
      directionForGreen, slots, measuredAt: new Date().toISOString(), reasons: reasons.slice(0, 32) })
  }

  /**
   * Press one control and then look at the panel again.
   *
   * A confirmed press is one the panel visibly reacted to. An unconfirmed press is reported as
   * exactly that: the events left the application and the broker's answer could not be read, which
   * is not the same as knowing the order did not happen.
   */
  async press(platform: Platform, map: ControlMap, canvasSlotId: number, direction: OrderDirection): Promise<PressResult> {
    const controls = map.slots.find(slot => slot.slotId === canvasSlotId)
    if (!controls) throw new Error(`CONTROLS_UNCALIBRATED: canvas cell ${canvasSlotId} has no measured controls.`)
    const point = direction === 'HIGHER' ? controls.higher : controls.lower
    const guard = { revision: map.surfaceRevision, zoomFactor: map.zoomFactor }
    const before = await this.panelImage(platform, controls)
    const { pressedAt } = await this.browsers.pressPoint(platform, point, guard)
    const reasons: string[] = []
    let verified = false
    try {
      await new Promise(resolve => setTimeout(resolve, VERIFY_DELAY_MS))
      const after = await this.panelImage(platform, controls)
      const delta = panelDelta(before, after)
      verified = delta >= VERIFY_DELTA
      if (!verified) reasons.push(`PANEL_UNCHANGED_${delta.toFixed(1)}`)
    } catch (error) {
      reasons.push(error instanceof Error && error.message.startsWith('PRESS') ? 'VERIFY_SURFACE_GONE' : 'VERIFY_CAPTURE_FAILED')
    }
    return { pressedAt, latencyMs: Date.now() - pressedAt, verified, reasons }
  }

  private async panelImage(platform: Platform, controls: SlotControls): Promise<PanelImage> {
    const { image, size, surface } = await this.browsers.captureSurface(platform)
    const roi = normalizedToPixel(controls.panelBounds, surface.bounds.width, surface.bounds.height)
    const scaleX = size.width / surface.bounds.width, scaleY = size.height / surface.bounds.height
    return cropPanel(image, { x: Math.max(0, Math.floor(roi.x * scaleX)), y: Math.max(0, Math.floor(roi.y * scaleY)),
      width: Math.max(1, Math.floor(roi.width * scaleX)), height: Math.max(1, Math.floor(roi.height * scaleY)) })
  }
}
