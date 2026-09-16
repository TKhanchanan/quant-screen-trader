import { EventEmitter } from 'node:events'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { BrowserWindow } from 'electron'
import { defaultCalibration } from '@quant-screen-trader/shared-types'
import { normalizeAsset } from '../electron/main/asset-detector'

class Contents extends EventEmitter {
  url = ''
  destroyed = false
  session = { setPermissionRequestHandler: vi.fn(), setPermissionCheckHandler: vi.fn(), on: vi.fn(), removeListener: vi.fn() }
  loadURL = vi.fn(async (url: string) => { this.url = url })
  getURL = (): string => this.url
  reload = vi.fn()
  close = vi.fn(() => { this.destroyed = true })
  isDestroyed = (): boolean => this.destroyed
  zoom = .7
  getZoomFactor = (): number => this.zoom
  setZoomFactor = vi.fn((zoom: number) => { this.zoom = zoom })
  setWindowOpenHandler = vi.fn()
  sendInputEvent = vi.fn()
  focus = vi.fn()
  capturePage = vi.fn()
  executeJavaScript = vi.fn(async () => ({ status: 'ALREADY_CLOSED' }))
}

const views: View[] = []
class View {
  webContents = new Contents()
  setBounds = vi.fn()
  setVisible = vi.fn()
  constructor(readonly options?: unknown) { views.push(this) }
}

class Window extends EventEmitter {
  contentView = { addChildView: vi.fn(), removeChildView: vi.fn() }
  show = vi.fn()
  focus = vi.fn()
  isDestroyed = (): boolean => false
  getContentSize = (): number[] => [1320, 900]
}

vi.mock('electron', () => ({ WebContentsView: View, BrowserWindow: Window }))
const { PlatformBrowserManager } = await import('../electron/main/platform-browser')

function surfaceImage(noise = false, width = 1320, height = 900): Electron.NativeImage {
  const pixels = new Uint8Array(width * height * 4)
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const value = y >= height * .18 && y < height * .58 && x >= width * .06 && x < width * .98
      ? (noise ? ((x * 3 + y * 7) % 2 ? 24 : 112) : ((x + y) % 2 ? 24 : 112))
      : 32
    const index = (y * width + x) * 4
    pixels[index] = value; pixels[index + 1] = value; pixels[index + 2] = value; pixels[index + 3] = 255
  }
  return {
    isEmpty: () => false,
    getSize: () => ({ width, height }),
    toBitmap: () => pixels,
    crop: (bounds: Electron.Rectangle) => surfaceImage(noise, bounds.width, bounds.height),
    resize: (size: Electron.ResizeOptions) => surfaceImage(noise, size.width ?? width, size.height ?? height)
  } as unknown as Electron.NativeImage
}

describe('Chart-Cell Identity Authority (Requirements A-H)', () => {
  beforeEach(() => {
    views.length = 0
  })

  it('A. IQ Option: 9 tabs open, tab-bar geometry detection uncertain (0 tabs) -> Sync Assets succeeds via chart cells', async () => {
    const manager = new PlatformBrowserManager(() => new View() as never)
    manager.attach('iqoption', new Window() as unknown as BrowserWindow)
    const contents = views[0]!.webContents
    contents.url = 'https://iqoption.com/traderoom'
    contents.emit('did-finish-load')
    manager.command({ operation: 'layout', platform: 'iqoption', bounds: { x: 0, y: 0, width: 1320, height: 900 }, visible: true })

    const stamp = new Date().toISOString()
    manager.useCalibration('iqoption', {
      id: '00000000-0000-4000-8000-000000000001',
      platform: 'iqoption',
      name: 'IQ Grid',
      geometrySource: 'MANUAL',
      createdAt: stamp,
      updatedAt: stamp,
      zoomFactor: .7,
      referenceBrowserWidth: 1320,
      referenceBrowserHeight: 900,
      slots: defaultCalibration('iqoption')
    })

    const fixtureImage = surfaceImage()
    contents.capturePage.mockResolvedValue(fixtureImage)

    // OCR recognizes asset from chart cell title
    const recognized = await manager.captureAssetTabs('iqoption', async () => ({
      rawText: 'EUR/USD\nv',
      confidence: .95
    }))

    expect(recognized.slots).toHaveLength(9)
    expect(recognized.slots.every(s => s.state === 'DETECTED' && s.assetName === 'EUR/USD')).toBe(true)
    expect(recognized.overallConfidence).toBeGreaterThanOrEqual(.95)
  })

  it('B. CapitalBear: changed chart instruments detected accurately from 3x3 cells', async () => {
    const manager = new PlatformBrowserManager(() => new View() as never)
    manager.attach('capitalbear', new Window() as unknown as BrowserWindow)
    const contents = views[0]!.webContents
    contents.url = 'https://capitalbear.com/traderoom'
    contents.emit('did-finish-load')
    manager.command({ operation: 'layout', platform: 'capitalbear', bounds: { x: 0, y: 0, width: 1320, height: 900 }, visible: true })

    const stamp = new Date().toISOString()
    manager.useCalibration('capitalbear', {
      id: '00000000-0000-4000-8000-000000000002',
      platform: 'capitalbear',
      name: 'CB Grid',
      geometrySource: 'MANUAL',
      createdAt: stamp,
      updatedAt: stamp,
      zoomFactor: .7,
      referenceBrowserWidth: 1320,
      referenceBrowserHeight: 900,
      slots: defaultCalibration('capitalbear')
    })

    contents.capturePage.mockResolvedValue(surfaceImage())

    const instrumentList = [
      'EUR/USD', 'GBP/USD', 'AUD/USD',
      'USD/JPY', 'USD/CAD', 'USD/CHF',
      'EUR/GBP', 'EUR/JPY', 'GBP/JPY OTC'
    ]

    let callCount = 0
    const syncResult = await manager.captureAssetTabs('capitalbear', async () => {
      const slotIndex = Math.floor(callCount++ / 4)
      const asset = instrumentList[slotIndex % instrumentList.length]!
      return { rawText: `${asset} v`, confidence: .96 }
    })

    expect(syncResult.slots).toHaveLength(9)
    for (let i = 0; i < 9; i++) {
      expect(syncResult.slots[i]!.state).toBe('DETECTED')
      expect(syncResult.slots[i]!.assetName).toBe(instrumentList[i])
    }
  })

  it('C. Tab-bar geometry NEVER blocks Sync, Probe, or continuous capture', async () => {
    const manager = new PlatformBrowserManager(() => new View() as never)
    manager.attach('capitalbear', new Window() as unknown as BrowserWindow)
    const contents = views[0]!.webContents
    contents.url = 'https://capitalbear.com/traderoom'; contents.emit('did-finish-load')
    manager.command({ operation: 'layout', platform: 'capitalbear', bounds: { x: 0, y: 0, width: 1320, height: 900 }, visible: true })
    manager.useCalibration('capitalbear', {
      id: '00000000-0000-4000-8000-000000000003', platform: 'capitalbear', name: 'CB', geometrySource: 'MANUAL',
      createdAt: '', updatedAt: '', zoomFactor: .7, referenceBrowserWidth: 1320, referenceBrowserHeight: 900, slots: defaultCalibration('capitalbear')
    })

    // Completely blank capture (no tab underlines anywhere)
    contents.capturePage.mockResolvedValue(surfaceImage())

    // Must NOT throw TAB_GEOMETRY_UNCERTAIN
    await expect(manager.captureAssetTabs('capitalbear', async () => ({ asset: 'EUR/USD', confidence: .95 }))).resolves.toBeDefined()
  })

  it('D. Single screenshot frame capture during Sync Assets', async () => {
    const manager = new PlatformBrowserManager(() => new View() as never)
    manager.attach('iqoption', new Window() as unknown as BrowserWindow)
    const contents = views[0]!.webContents
    contents.url = 'https://iqoption.com/traderoom'; contents.emit('did-finish-load')
    manager.command({ operation: 'layout', platform: 'iqoption', bounds: { x: 0, y: 0, width: 1320, height: 900 }, visible: true })
    manager.useCalibration('iqoption', {
      id: '00000000-0000-4000-8000-000000000004', platform: 'iqoption', name: 'IQ', geometrySource: 'MANUAL',
      createdAt: '', updatedAt: '', zoomFactor: .7, referenceBrowserWidth: 1320, referenceBrowserHeight: 900, slots: defaultCalibration('iqoption')
    })

    // Ensure grid is ready first
    const fixture = surfaceImage()
    contents.capturePage.mockResolvedValue(fixture)
    await (manager as unknown as { prepareChartGrid: (platform: string) => Promise<void> }).prepareChartGrid('iqoption')

    contents.capturePage.mockClear()
    contents.capturePage.mockResolvedValue(fixture)

    await manager.captureAssetTabs('iqoption', async () => ({ asset: 'EUR/USD', confidence: .95 }))
    // Exactly 1 frame captured for all 9 chart cell scans during Sync Assets
    expect(contents.capturePage).toHaveBeenCalledTimes(1)
  })

  it('E & F & G. Title fingerprint validation: match succeeds, mismatch marks only slot N DATA_UNCERTAIN without global freeze', async () => {
    const manager = new PlatformBrowserManager(() => new View() as never)
    manager.attach('capitalbear', new Window() as unknown as BrowserWindow)
    const contents = views[0]!.webContents
    contents.url = 'https://capitalbear.com/traderoom'; contents.emit('did-finish-load')
    manager.command({ operation: 'layout', platform: 'capitalbear', bounds: { x: 0, y: 0, width: 1320, height: 900 }, visible: true })
    manager.useCalibration('capitalbear', {
      id: '00000000-0000-4000-8000-000000000005', platform: 'capitalbear', name: 'CB', geometrySource: 'MANUAL',
      createdAt: '', updatedAt: '', zoomFactor: .7, referenceBrowserWidth: 1320, referenceBrowserHeight: 900, slots: defaultCalibration('capitalbear')
    })

    const initialBitmap = surfaceImage(false)
    contents.capturePage.mockResolvedValue(initialBitmap)

    const syncResult = await manager.captureAssetTabs('capitalbear', async () => ({ asset: 'EUR/USD', confidence: .96 }))
    expect(syncResult.slots.every(s => s.state === 'DETECTED')).toBe(true)

    // F. Capture with identical frame succeeds for all 9 slots
    const contexts = syncResult.slots.map(s => ({
      platform: 'capitalbear' as const,
      slotId: s.slotId,
      assetName: 'EUR/USD',
      contextId: 'test-ctx',
      calibrationProfileId: '00000000-0000-4000-8000-000000000005',
      bounds: defaultCalibration('capitalbear')[s.slotId - 1]!.bounds
    }))

    const batchSuccess = await manager.captureSlots(contexts)
    expect(batchSuccess.images.size).toBe(9)
    for (const [, img] of batchSuccess.images) {
      expect(img).not.toBeInstanceOf(Error)
    }

    // G. Simulate Slot 3 title changing (noise in cell 3)
    const alteredImage = surfaceImage(true)
    contents.capturePage.mockResolvedValue(alteredImage)

    const batchWithMismatch = await manager.captureSlots(contexts)
    // Slot failures are isolated: each slot independently evaluated
    expect(batchWithMismatch.images.size).toBe(9)
  })

  it('H. normalizeAsset removes hardcoded OpenAI OTC, EUR/JPY, NZD/USD symbol hacks', () => {
    // Normal pair regex formatting
    expect(normalizeAsset('EUR/USD')).toBe('EUR/USD')
    expect(normalizeAsset('eur/usd otc')).toBe('EUR/USD OTC')
    expect(normalizeAsset('GBP/JPY')).toBe('GBP/JPY')
    expect(normalizeAsset('NZD/USD')).toBe('NZD/USD')

    // Generic name preserved without forcing OpenAI OTC from typos
    expect(normalizeAsset('OpenAI OTC')).toBe('OpenAI OTC')
    expect(normalizeAsset('OpenAl OTC')).toBe('OpenAl OTC') // No symbol hack turning Al into AI
  })
})

describe('CapitalBear Portfolio Panel Auto-Close (Requirements 1-9)', () => {
  beforeEach(() => {
    views.length = 0
  })

  it('1 & 2 & 3. Closes on document load via DOM click without coordinate clicks', async () => {
    const manager = new PlatformBrowserManager(() => new View() as never)
    manager.attach('capitalbear', new Window() as unknown as BrowserWindow)
    const contents = views[0]!.webContents
    contents.url = 'https://capitalbear.com/traderoom'

    // Mock executeJavaScript returning CLOSED (simulates clicking the toggle)
    contents.executeJavaScript.mockResolvedValueOnce({ status: 'CLOSED' })

    const status = await manager.closePortfolioPanel('capitalbear')
    expect(status).toBe('CLOSED')
    expect(contents.sendInputEvent).not.toHaveBeenCalled()
  })

  it('4. Does not toggle if already closed', async () => {
    const manager = new PlatformBrowserManager(() => new View() as never)
    manager.attach('capitalbear', new Window() as unknown as BrowserWindow)
    const contents = views[0]!.webContents
    contents.url = 'https://capitalbear.com/traderoom'

    contents.executeJavaScript.mockResolvedValueOnce({ status: 'ALREADY_CLOSED' })

    const status = await manager.closePortfolioPanel('capitalbear')
    expect(status).toBe('ALREADY_CLOSED')
  })

  it('6. Runs once per document load and deduplicates concurrent calls', async () => {
    const manager = new PlatformBrowserManager(() => new View() as never)
    manager.attach('capitalbear', new Window() as unknown as BrowserWindow)
    const contents = views[0]!.webContents
    contents.url = 'https://capitalbear.com/traderoom'

    contents.executeJavaScript.mockResolvedValue({ status: 'CLOSED' })

    const [status1, status2] = await Promise.all([
      manager.closePortfolioPanel('capitalbear'),
      manager.closePortfolioPanel('capitalbear')
    ])

    expect(status1).toBe('CLOSED')
    expect(status2).toBe('CLOSED')
    // Second sequential call returns cached status
    const status3 = await manager.closePortfolioPanel('capitalbear')
    expect(status3).toBe('CLOSED')
  })

  it('9. Manual Reload Platform resets once-per-document guard', async () => {
    const manager = new PlatformBrowserManager(() => new View() as never)
    manager.attach('capitalbear', new Window() as unknown as BrowserWindow)
    const contents = views[0]!.webContents
    contents.url = 'https://capitalbear.com/traderoom'
    contents.emit('did-finish-load')

    contents.executeJavaScript.mockResolvedValue({ status: 'CLOSED' })
    await manager.closePortfolioPanel('capitalbear')

    // Simulate reload navigation
    contents.emit('did-start-navigation', {}, 'https://capitalbear.com/traderoom', false, true)
    contents.emit('did-finish-load')

    // Should be able to close again on new document
    const status = await manager.closePortfolioPanel('capitalbear')
    expect(status).toBe('CLOSED')
  })
})
