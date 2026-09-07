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
  setZoomFactor = vi.fn()
  setWindowOpenHandler = vi.fn()
}
const views: View[] = []
class View {
  webContents = new Contents()
  setBounds = vi.fn()
  setVisible = vi.fn()
  constructor(readonly options?: unknown) { views.push(this) }
}
vi.mock('electron', () => ({ WebContentsView: View }))
const { PlatformBrowserManager } = await import('../electron/main/platform-browser')
class Window extends EventEmitter {
  contentView = { addChildView: vi.fn(), removeChildView: vi.fn() }
  isDestroyed = (): boolean => false
  getContentSize = (): number[] => [1320, 900]
}
describe('embedded browser lifecycle', () => {
  beforeEach(() => { views.length = 0 })
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
    expect(snapshot.session.state).toBe('LOGIN_REQUIRED')
    expect(snapshot.session.currentUrl).toBe('https://capitalbear.com')
    const preventDefault = vi.fn()
    contents.emit('will-navigate', { preventDefault }, 'https://unrelated.test')
    expect(preventDefault).toHaveBeenCalledOnce()
    contents.emit('did-fail-load', {}, -106, 'private error', 'secret', true)
    expect(JSON.stringify(manager.command({ operation: 'state', platform: 'capitalbear' }))).not.toContain('secret')
    expect(manager.command({ operation: 'state', platform: 'capitalbear' }).session.state).toBe('DISCONNECTED')
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
