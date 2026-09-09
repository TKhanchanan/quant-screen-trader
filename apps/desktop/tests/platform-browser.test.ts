import { EventEmitter } from 'node:events'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { BrowserWindow } from 'electron'
import { defaultCalibration } from '@quant-screen-trader/shared-types'
import { createPlaceholderSlots } from '../src/renderer/src/features/slots/createPlaceholderSlots'
import { requireScope } from '../electron/main/ipc-scope'

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
}
const views: View[] = []
class View {
  webContents = new Contents()
  setBounds = vi.fn()
  setVisible = vi.fn()
  constructor(readonly options?: unknown) { views.push(this) }
}
const popups: Popup[] = []
class Popup extends EventEmitter {
  webContents = new Contents()
  isDestroyed = (): boolean => this.webContents.destroyed
  destroy = vi.fn(() => { this.webContents.close(); this.emit('closed') })
  constructor(readonly options: Electron.BrowserWindowConstructorOptions) { super(); popups.push(this) }
}
vi.mock('electron', () => ({ WebContentsView: View, BrowserWindow: Popup }))
const { PlatformBrowserManager, chartSurfaceActivity, findAssetTabs, canvasSlotForTab, clippedPrefix } = await import('../electron/main/platform-browser')
class Window extends EventEmitter {
  contentView = { addChildView: vi.fn(), removeChildView: vi.fn() }
  show = vi.fn()
  focus = vi.fn()
  isDestroyed = (): boolean => false
  getContentSize = (): number[] => [1320, 900]
}
describe('embedded browser lifecycle', () => {
  beforeEach(() => { views.length = 0; popups.length = 0 })
  it.each(['capitalbear', 'iqoption'] as const)('scopes Google login and callbacks to %s', (platform) => {
    const manager = new PlatformBrowserManager(() => new View() as never)
    const owner = new Window(), other = new Window()
    const sibling = platform === 'capitalbear' ? 'iqoption' : 'capitalbear'
    manager.attach(platform, owner as unknown as BrowserWindow)
    manager.attach(sibling, other as unknown as BrowserWindow)
    const contents = views[0]!.webContents
    expect(contents.setZoomFactor).toHaveBeenCalledWith(.7)
    const open = contents.setWindowOpenHandler.mock.calls[0]![0] as
      (details: { url: string }) => Electron.WindowOpenHandlerResponse
    const callback = `https://${platform}.com/oauth/callback`
    const google = `https://accounts.google.com/o/oauth2/v2/auth?redirect_uri=${encodeURIComponent(callback)}`
    for (const url of [google, callback]) {
      const preventDefault = vi.fn()
      contents.emit('will-navigate', { preventDefault }, url)
      expect(preventDefault).not.toHaveBeenCalled()
    }
    for (const url of [`https://${sibling}.com/`, 'https://accounts.google.com.evil.test/',
      'https://user:pass@accounts.google.com/', 'about:blank',
      `https://accounts.google.com/o/oauth2/auth?redirect_uri=https://${sibling}.com/callback`]) {
      expect(open({ url }).action).toBe('deny')
    }
    const response = open({ url: google })
    expect(response.action).toBe('allow')
    response.createWindow!({ webPreferences: { partition: 'persist:wrong', nodeIntegration: true, preload: '/unsafe' } })
    const popup = popups[0]!
    expect(popup.options.parent).toBe(owner)
    expect(popup.options.webPreferences).toEqual({ session: contents.session, partition: `persist:${platform}-profile`,
      contextIsolation: true, nodeIntegration: false, sandbox: true, webSecurity: true,
      navigateOnDragDrop: false, spellcheck: false })
    expect(popup.options.webPreferences!.session).not.toBe(views[1]!.webContents.session)
    for (const remote of [contents, popup.webContents]) {
      const preventDefault = vi.fn()
      remote.emit('will-redirect', { preventDefault }, callback, false, true)
      expect(preventDefault).not.toHaveBeenCalled()
      remote.emit('will-redirect', { preventDefault }, `https://${sibling}.com/callback`, false, true)
      expect(preventDefault).toHaveBeenCalledOnce()
    }
    expect(popup.webContents.setWindowOpenHandler.mock.calls[0]![0]({ url: google }).action).toBe('deny')
    other.emit('closed')
    expect(popup.destroy).not.toHaveBeenCalled()
    owner.emit('closed')
    expect(popup.destroy).toHaveBeenCalledOnce()
    manager.attach(platform, new Window() as unknown as BrowserWindow)
    expect(views[2]!.options).toEqual(views[0]!.options)
  })
  it('isolates reload, crash, close, reopen and session partitions', () => {
    const manager = new PlatformBrowserManager(() => new View() as never)
    const capital = new Window(), iq = new Window()
    manager.attach('capitalbear', capital as unknown as BrowserWindow)
    manager.attach('iqoption', iq as unknown as BrowserWindow)
    const [first, second] = views
    expect(first?.options).toMatchObject({ webPreferences: { partition: 'persist:capitalbear-profile', sandbox: true, nodeIntegration: false, contextIsolation: true, webSecurity: true } })
    manager.command({ operation: 'reload', platform: 'capitalbear' })
    expect(first?.webContents.reload).toHaveBeenCalledOnce()
    expect(second?.webContents.reload).not.toHaveBeenCalled()
    first?.webContents.emit('render-process-gone')
    expect(manager.command({ operation: 'state', platform: 'capitalbear' }).session.state).toBe('ERROR')
    expect(manager.command({ operation: 'state', platform: 'iqoption' }).session.state).toBe('STARTING')
    capital.emit('closed')
    expect(first?.webContents.close).toHaveBeenCalledOnce()
    expect(second?.webContents.close).not.toHaveBeenCalled()
    manager.attach('capitalbear', new Window() as unknown as BrowserWindow)
    expect(views[2]?.options).toEqual(first?.options)
  })
  it('never claims READY on load; navigation is blocked and errors contain no URLs', () => {
    const manager = new PlatformBrowserManager(() => new View() as never)
    manager.attach('capitalbear', new Window() as unknown as BrowserWindow)
    const contents = views[0]!.webContents
    contents.emit('did-start-navigation', {}, 'https://capitalbear.com', false, true)
    expect(manager.command({ operation: 'state', platform: 'capitalbear' }).session.state).toBe('LOADING')
    contents.url = 'https://capitalbear.com/private?token=secret'
    contents.emit('did-finish-load')
    const snapshot = manager.command({ operation: 'state', platform: 'capitalbear' })
    expect(snapshot.session.state).toBe('UNKNOWN')
    expect(snapshot.session.currentUrl).toBe('https://capitalbear.com')
    const preventDefault = vi.fn()
    contents.emit('will-navigate', { preventDefault }, 'https://unrelated.test')
    expect(preventDefault).toHaveBeenCalledOnce()
    contents.emit('did-finish-load')
    expect(manager.command({ operation: 'state', platform: 'capitalbear' }).session).toMatchObject({
      state: 'ERROR', errorCode: 'LOGIN_NAVIGATION_BLOCKED' })
    contents.emit('did-fail-load', {}, -106, 'private error', 'secret', true)
    expect(JSON.stringify(manager.command({ operation: 'state', platform: 'capitalbear' }))).not.toContain('secret')
    expect(manager.command({ operation: 'state', platform: 'capitalbear' }).session.state).toBe('DISCONNECTED')
  })
  it.each(['capitalbear', 'iqoption'] as const)('reports blocked login and popup failures for %s without private URLs', (platform) => {
    const manager = new PlatformBrowserManager(() => new View() as never)
    manager.attach(platform, new Window() as unknown as BrowserWindow)
    const contents = views[0]!.webContents
    const open = contents.setWindowOpenHandler.mock.calls[0]![0] as
      (details: { url: string }) => Electron.WindowOpenHandlerResponse
    open({ url: 'https://unrelated.test/private?token=secret#secret' })
    const snapshot = (): ReturnType<typeof manager.command> => manager.command({ operation: 'state', platform })
    expect(snapshot().session.errorMessage).toContain('https://unrelated.test')
    expect(JSON.stringify(snapshot())).not.toMatch(/private|secret/)
    contents.emit('did-start-navigation', {}, `https://${platform}.com/`, false, true)
    expect(snapshot().session.errorCode).toBeUndefined()
    open({ url: 'https://accounts.google.com/' }).createWindow!({})
    popups[0]!.webContents.emit('did-fail-load', {}, -105, 'secret', 'https://accounts.google.com/private', true)
    expect(snapshot().session.errorCode).toBe('LOGIN_LOAD_-105')
    expect(JSON.stringify(snapshot())).not.toMatch(/private|secret/)
    popups[0]!.webContents.emit('render-process-gone')
    expect(snapshot().session.errorCode).toBe('LOGIN_RENDERER_GONE')
  })
  it('keeps overlay geometry relative to browser bounds and disposes it on cancel', () => {
    const manager = new PlatformBrowserManager(() => new View() as never)
    manager.attach('capitalbear', new Window() as unknown as BrowserWindow)
    manager.command({ operation: 'beginCalibration', platform: 'capitalbear', draft: {
      assets: { platform: 'capitalbear', slots: createPlaceholderSlots('capitalbear') }, slots: defaultCalibration(), zoomFactor: 1 } })
    const bounds = { x: 0, y: 200, width: 1320, height: 700 }
    manager.command({ operation: 'layout', platform: 'capitalbear', bounds, visible: true })
    expect(views[0]?.setBounds).toHaveBeenLastCalledWith(bounds)
    expect(views[1]?.setBounds).toHaveBeenLastCalledWith(bounds)
    expect(() => manager.command({ operation: 'layout', platform: 'capitalbear', bounds: { ...bounds, height: 900 }, visible: true })).toThrow()
    expect(manager.command({ operation: 'endCalibration', platform: 'capitalbear' }).draft).toBeNull()
    expect(views[1]?.webContents.close).toHaveBeenCalledOnce()
    expect(manager.command({ operation: 'state', platform: 'capitalbear' }).zoomFactor).toBe(.7)
    expect(views[0]?.webContents.setZoomFactor).not.toHaveBeenCalledWith(1)
  })
  const bitmap = (value: number, width = 2, height = 1): Electron.NativeImage => {
    const resize = vi.fn((size: Electron.ResizeOptions) => bitmap(value, size.width, size.height))
    const crop = vi.fn((bounds: Electron.Rectangle) => bitmap(value, bounds.width, bounds.height))
    return { isEmpty: () => false, resize, crop, getSize: () => ({ width, height }),
      toBitmap: () => new Uint8Array(width * height * 4).map((_, index) => index % 4 === 3 ? 255 : value) } as unknown as Electron.NativeImage
  }
  const ready = (platform: 'capitalbear' | 'iqoption' = 'capitalbear'):
  { manager: InstanceType<typeof PlatformBrowserManager>; contents: Contents } => {
    const manager = new PlatformBrowserManager(() => new View() as never)
    const window = new Window()
    manager.attach(platform, window as unknown as BrowserWindow)
    const contents = views[0]!.webContents
    contents.url = `https://${platform}.com/`
    contents.emit('did-finish-load')
    manager.command({ operation: 'layout', platform, bounds: { x: 0, y: 200, width: 900, height: 600 }, visible: true })
    return { manager, contents }
  }
  const tabs = (platform: 'capitalbear' | 'iqoption', tabWidth = 120): Electron.NativeImage => {
    const width = 900, height = 600, pixels = new Uint8Array(width * height * 4)
    for (let index = 0; index < pixels.length; index += 4) {
      pixels[index] = 32; pixels[index + 1] = 32; pixels[index + 2] = 32; pixels[index + 3] = 255
    }
    const left = platform === 'capitalbear' ? 220 : 160
    for (let y = 12; y < 63; y++) for (const x of [left, left + tabWidth, left + tabWidth + 20,
      left + tabWidth * 2 + 20, left + tabWidth * 2 + 40, left + tabWidth * 3 + 40]) {
      const column = x + Math.floor((y - 12) / 20)
      const index = (y * width + column) * 4
      pixels[index] = 96; pixels[index + 1] = 96; pixels[index + 2] = 96
    }
    const image = bitmap(200, width, height) as unknown as { toBitmap: () => Uint8Array }
    image.toBitmap = () => pixels
    return image as unknown as Electron.NativeImage
  }
  it.each(['capitalbear', 'iqoption'] as const)('captures the %s asset tab without broker input', async (platform) => {
    const { manager, contents } = ready(platform)
    const image = tabs(platform)
    contents.capturePage.mockResolvedValue(image)
    const recognize = vi.fn(async () => ({ asset: 'EUR/USD (OTC)', confidence: .98 }))
    await expect(manager.captureAssetLabel(platform, 2, defaultCalibration(platform), recognize))
      .resolves.toMatchObject({ asset: 'EUR/USD (OTC)', present: true })
    expect(contents.capturePage).toHaveBeenCalledWith()
    expect(image.crop).toHaveBeenCalledWith(expect.objectContaining({ width: 84, height: 21 }))
    expect(recognize).toHaveBeenCalledWith(expect.objectContaining({ width: 336, height: 84, purpose: 'ASSET' }))
    expect(recognize).toHaveBeenCalledTimes(4)
    expect(contents.sendInputEvent).not.toHaveBeenCalled()
  })
  it('maps the current tab count and reports slots beyond it as absent', async () => {
    const { manager, contents } = ready('iqoption')
    contents.capturePage.mockResolvedValue(tabs('iqoption'))
    const recognize = vi.fn()
    await expect(manager.captureAssetLabel('iqoption', 4, defaultCalibration('iqoption'), recognize))
      .resolves.toMatchObject({ confidence: 1, present: false })
    expect(recognize).not.toHaveBeenCalled()
  })
  it('refuses OCR fragments when tabs are too narrow or preprocessing votes tie', async () => {
    const { manager, contents } = ready('iqoption')
    contents.capturePage.mockResolvedValue(tabs('iqoption', 70))
    const recognize = vi.fn()
    await expect(manager.captureAssetLabel('iqoption', 1, defaultCalibration('iqoption'), recognize))
      .resolves.toMatchObject({ confidence: 0, present: true })
    expect(recognize).not.toHaveBeenCalled()

    contents.capturePage.mockResolvedValue(tabs('iqoption'))
    recognize.mockResolvedValueOnce({ asset: 'EUR/USD', confidence: .99 })
      .mockResolvedValueOnce({ asset: 'EUR/USD', confidence: .99 })
      .mockResolvedValueOnce({ asset: 'GBP/USD', confidence: .99 })
      .mockResolvedValueOnce({ asset: 'GBP/USD', confidence: .99 })
    await expect(manager.captureAssetLabel('iqoption', 1, defaultCalibration('iqoption'), recognize))
      .resolves.toMatchObject({ confidence: .94, present: true })
  })
  it('captures only against verified geometry, whatever sits below the charts', async () => {
    const { manager, contents } = ready()
    const width = 100, height = 100, pixels = new Uint8Array(width * height * 4)
    // Painted charts in the upper band, an expanded broker panel (flat) below them.
    for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
      const value = y >= 18 && y < 58 && x >= 6 && x < 98 ? (x + y) % 2 ? 24 : 112 : 32
      const index = (y * width + x) * 4
      pixels[index] = value; pixels[index + 1] = value; pixels[index + 2] = value; pixels[index + 3] = 255
    }
    contents.capturePage.mockResolvedValue({ isEmpty: () => false, getSize: () => ({ width, height }),
      toBitmap: () => pixels } as unknown as Electron.NativeImage)
    await expect(manager.captureSlot({ platform: 'capitalbear', slotId: 1, assetName: 'EUR/USD', contextId: 'test',
      calibrationProfileId: null, bounds: { x: .05, y: .12, width: .3, height: .26 } }))
      .rejects.toThrow('Capture unavailable: verified chart geometry required')
    expect(contents.sendInputEvent).not.toHaveBeenCalled()
  })
})
describe('visible chart surface preparation', () => {
  const image = (painted: boolean) => {
    const width = 100, height = 100, grayscale = new Uint8Array(width * height).fill(32)
    if (painted) for (let y = 18; y < 58; y++) for (let x = 6; x < 98; x++) grayscale[y * width + x] = (x + y) % 2 ? 24 : 112
    return { width, height, grayscale }
  }
  it('reports readiness from the chart band and ignores whatever fills the rest of the surface', () => {
    expect(chartSurfaceActivity(image(false)).ready).toBe(false)
    expect(chartSurfaceActivity(image(true)).ready).toBe(true)
  })
})
describe('visual asset tab segmentation', () => {
  it('finds skewed variable-count tabs and ignores square controls', () => {
    const width = 900, height = 600, grayscale = new Uint8Array(width * height).fill(32)
    for (let y = 12; y < 63; y++) for (const x of [100, 150, 180, 300, 320, 440, 460, 580, 605, 655])
      grayscale[y * width + x + Math.floor((y - 12) / 20)] = 96
    const result = findAssetTabs({ width, height, grayscale }, 'iqoption')
    expect(result).toHaveLength(3)
    expect(result.map(tab => tab.width)).toEqual([120, 120, 120])
  })
})
describe('IPC sender scope', () => {
  it('rejects unknown senders, subframes and cross-platform operations', () => {
    const scope = { platform: 'capitalbear' as const, overlay: false }
    expect(() => requireScope(undefined, true)).toThrow()
    expect(() => requireScope(scope, false)).toThrow()
    expect(() => requireScope(scope, true, 'iqoption')).toThrow()
    expect(requireScope(scope, true, 'capitalbear')).toEqual(scope)
  })
})

describe('nine opened tabs at operating zoom', () => {
  function screenshot(count: number, scale: number, missing = -1) {
    const width = Math.round(3000 * scale), height = Math.round(1200 * scale), grayscale = new Uint8Array(width * height).fill(32)
    for (let tab = 0; tab < count; tab++) {
      const left = Math.round((460 + tab * 210) * scale), right = left + Math.round((188 + tab % 3 * 4) * scale)
      for (let y = Math.round(16 * scale); y <= Math.round(85 * scale); y++) for (let x = left; x < right; x++)
        grayscale[y * width + x] = y >= Math.round(83 * scale) && tab !== missing ? 220 : 60
    }
    return { width, height, grayscale }
  }
  it.each([3, 5, 9])('preserves physical order for %i variable-width tabs across zoom and resize', count => {
    for (const scale of [.7, 1, 1.25]) for (const platform of ['capitalbear', 'iqoption'] as const) {
      const image = screenshot(count, scale), first = findAssetTabs(image, platform)
      expect(first).toHaveLength(count)
      expect(findAssetTabs(image, platform)).toEqual(first)
      expect(first.map(t => t.x)).toEqual(first.map(t => t.x).sort((a, b) => a - b))
    }
  })
  it('rejects a missing interior tab instead of shifting subsequent assets', () => {
    expect(findAssetTabs(screenshot(9, 1, 2), 'iqoption')).toEqual([])
  })
  it('reads a tab whose name area is far shorter than the captured surface', async () => {
    // A 2x capture of a nine-tab bar: tabs are wide enough to read, but far narrower than the
    // surface is tall. Judging legibility against the surface height rejected all of them.
    views.length = 0
    const manager = new PlatformBrowserManager(() => new View() as never)
    manager.attach('iqoption', new Window() as unknown as BrowserWindow)
    const contents = views[0]!.webContents
    contents.url = 'https://iqoption.com/'
    contents.emit('did-finish-load')
    manager.command({ operation: 'layout', platform: 'iqoption', bounds: { x: 0, y: 0, width: 1320, height: 860 }, visible: true })
    const image = screenshot(9, 1)
    contents.capturePage.mockResolvedValue({ isEmpty: () => false, getSize: () => ({ width: image.width, height: image.height }),
      toBitmap: () => Uint8Array.from({ length: image.width * image.height * 4 },
        (_, index) => index % 4 === 3 ? 255 : image.grayscale[Math.floor(index / 4)]!),
      crop: () => ({ getSize: () => ({ width: 40, height: 12 }), toBitmap: () => new Uint8Array(40 * 12 * 4),
        resize: () => ({ getSize: () => ({ width: 160, height: 48 }), toBitmap: () => new Uint8Array(160 * 48 * 4) }) })
    } as unknown as Electron.NativeImage)
    const recognize = vi.fn(async () => ({ asset: 'EUR/USD OTC', confidence: .97 }))
    await expect(manager.captureAssetLabel('iqoption', 1, defaultCalibration('iqoption'), recognize))
      .resolves.toMatchObject({ present: true, asset: 'EUR/USD OTC' })
    expect(recognize).toHaveBeenCalledTimes(4)
    expect(contents.sendInputEvent).not.toHaveBeenCalled()
  })
  it('completes a clipped tab name only from agreeing reads that continue it', () => {
    expect(clippedPrefix(['Australian D...', 'Australian D...', 'x'])).toBe('Australian D')
    expect(clippedPrefix(['Australian D…', 'Australian D…'])).toBe('Australian D')
    expect(clippedPrefix(['Australian D...'])).toBeNull()
    expect(clippedPrefix(['Australian Dollar Index', 'Australian Dollar Index'])).toBeNull()
    expect(clippedPrefix(['Pl...', 'Pl...'])).toBeNull()
    expect(clippedPrefix(undefined)).toBeNull()
  })
  it.each(['capitalbear', 'iqoption'] as const)('maps each %s tab onto the canvas cell verified to hold it', platform => {
    expect(Array.from({ length: 9 }, (_, i) => canvasSlotForTab(platform, i + 1, 9))).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9])
    expect(Array.from({ length: 5 }, (_, i) => canvasSlotForTab(platform, i + 1, 5))).toEqual([1, 2, 3, 4, 5])
    expect(() => canvasSlotForTab(platform, 6, 5)).toThrow('MAPPING')
    expect(() => canvasSlotForTab(platform, 1, 10)).toThrow('MAPPING')
  })
})
