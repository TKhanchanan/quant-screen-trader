import { WebContentsView, BrowserWindow, type WebContents } from 'electron'
import { type BrowserSnapshot, type Platform, type PlatformCommand } from '@quant-screen-trader/shared-types'
import { allowedLoginNavigation, allowedNavigation, getPlatformConfig, safeOrigin } from '../platforms/config'

interface Entry {
  window: BrowserWindow
  view: WebContentsView
  overlay: WebContentsView | null
  snapshot: BrowserSnapshot
  visible: boolean
}
export class PlatformBrowserManager {
  private readonly entries = new Map<Platform, Entry>()
  constructor(private readonly createOverlay: (platform: Platform) => WebContentsView) {}
  attach(platform: Platform, window: BrowserWindow): void {
    const config = getPlatformConfig(platform)
    const view = new WebContentsView({ webPreferences: { partition: config.sessionPartition,
      contextIsolation: true, nodeIntegration: false, sandbox: true, webSecurity: true,
      navigateOnDragDrop: false, spellcheck: false } })
    const entry: Entry = { window, view, overlay: null, visible: false,
      snapshot: { session: { platform, state: 'STARTING', loadState: 'idle', lastUpdatedAt: new Date().toISOString() },
        bounds: { x: 0, y: 180, width: 1, height: 1 }, zoomFactor: 1, draft: null } }
    this.entries.set(platform, entry)
    window.contentView.addChildView(view)
    view.setVisible(false)
    const contents = view.webContents
    contents.session.setPermissionRequestHandler((_webContents, _permission, callback) => callback(false))
    contents.session.setPermissionCheckHandler(() => false)
    const preventDownload = (event: Electron.Event): void => event.preventDefault()
    contents.session.on('will-download', preventDownload)
    const loginWindows = new Set<BrowserWindow>()
    let loginError: { errorCode: string; errorMessage: string } | undefined
    const reportLoginError = (errorCode: string, errorMessage: string): void => {
      loginError = { errorCode, errorMessage }
      entry.snapshot.session = { ...entry.snapshot.session, ...loginError,
        state: 'ERROR', lastUpdatedAt: new Date().toISOString() }
    }
    const blocked = (url: string): void => reportLoginError('LOGIN_NAVIGATION_BLOCKED',
      `QuantScreen Trader blocked a login page (${safeOrigin(url) ?? 'unsupported destination'}). This destination is not permitted for ${config.displayName}.`)
    const guardNavigation = (remote: WebContents): void => {
      remote.on('will-navigate', (event, url) => {
        if (!allowedLoginNavigation(config, url)) { event.preventDefault(); blocked(url) }
      })
      remote.on('will-redirect', (event, url, _inPlace, mainFrame) => {
        if (mainFrame && !allowedLoginNavigation(config, url)) { event.preventDefault(); blocked(url) }
      })
    }
    guardNavigation(contents)
    contents.setWindowOpenHandler(({ url }) => {
      if (!allowedLoginNavigation(config, url)) { blocked(url); return { action: 'deny' } }
      return { action: 'allow', createWindow: (options) => {
        // Preserve Chromium's opener/POST behavior, but pin all browser privileges
        // and session ownership to the originating platform before the first load.
        const popup = new BrowserWindow({ ...options, parent: window, show: true,
          width: 600, height: 760, title: `${config.displayName} — Sign in`,
          webPreferences: { session: contents.session, partition: config.sessionPartition,
            contextIsolation: true, nodeIntegration: false, sandbox: true, webSecurity: true,
            navigateOnDragDrop: false, spellcheck: false } })
        guardNavigation(popup.webContents)
        popup.webContents.setWindowOpenHandler(({ url: target }) => { blocked(target); return { action: 'deny' } })
        popup.webContents.on('did-fail-load', (_event, code, _description, _url, mainFrame) => {
          if (mainFrame && code !== -3) reportLoginError(`LOGIN_LOAD_${code}`,
            `The ${config.displayName} sign-in window could not load (code ${code}). Close it and retry from the workspace.`)
        })
        popup.webContents.on('render-process-gone', () => reportLoginError('LOGIN_RENDERER_GONE',
          `The ${config.displayName} sign-in window stopped responding. Close it and retry from the workspace.`))
        loginWindows.add(popup)
        popup.once('closed', () => loginWindows.delete(popup))
        return popup.webContents
      } }
    })
    const state = (status: BrowserSnapshot['session']['state'], loadState: BrowserSnapshot['session']['loadState'], errorCode?: string): void => {
      if (errorCode) loginError = undefined
      const origin = safeOrigin(contents.getURL())
      entry.snapshot.session = { platform, state: status, loadState, lastUpdatedAt: new Date().toISOString(),
        ...(origin ? { currentUrl: origin } : {}),
        ...(errorCode ? { errorCode, errorMessage: 'Platform unavailable. Check your connection or reload; no security bypass is attempted.' } : {}),
        ...(loginError ? { ...loginError, state: 'ERROR' } : {}) }
    }
    contents.on('did-start-navigation', (_event, url, inPlace, mainFrame) => {
      if (mainFrame && !inPlace && allowedLoginNavigation(config, url)) {
        loginError = undefined
        state('LOADING', 'loading')
      }
    })
    contents.on('dom-ready', () => state('LOGIN_REQUIRED', 'loaded'))
    contents.on('did-finish-load', () => state('LOGIN_REQUIRED', 'loaded'))
    contents.on('did-fail-load', (_event, code, _description, _url, mainFrame) => {
      if (mainFrame && code !== -3) state(code === -106 ? 'DISCONNECTED' : 'ERROR', 'failed', String(code))
    })
    contents.on('render-process-gone', () => state('ERROR', 'failed', 'RENDERER_GONE'))
    contents.on('zoom-changed', () => { contents.setZoomFactor(entry.snapshot.zoomFactor) })
    window.once('closed', () => {
      this.endCalibration(entry)
      for (const popup of loginWindows) if (!popup.isDestroyed()) popup.destroy()
      contents.session.removeListener('will-download', preventDownload)
      if (!contents.isDestroyed()) contents.close()
      this.entries.delete(platform)
    })
    void contents.loadURL(config.startUrl).catch(() => { /* did-fail-load reports a sanitized error. */ })
  }
  private endCalibration(entry: Entry): void {
    if (entry.overlay) {
      if (!entry.window.isDestroyed()) entry.window.contentView.removeChildView(entry.overlay)
      if (!entry.overlay.webContents.isDestroyed()) entry.overlay.webContents.close()
      entry.overlay = null
    }
    entry.snapshot.draft = null
  }
  command(request: PlatformCommand): BrowserSnapshot {
    const entry = this.entries.get(request.platform)
    if (!entry) throw new Error('Workspace is not open')
    switch (request.operation) {
      case 'reload': {
        const url = entry.view.webContents.getURL()
        if (allowedNavigation(getPlatformConfig(request.platform), url)) entry.view.webContents.reload()
        else void entry.view.webContents.loadURL(getPlatformConfig(request.platform).startUrl).catch(() => {})
        break
      }
      case 'layout': {
        const [width = 0, height = 0] = entry.window.getContentSize()
        if (request.bounds.x + request.bounds.width > width || request.bounds.y + request.bounds.height > height)
          throw new Error('Browser bounds exceed workspace')
        entry.snapshot.bounds = request.bounds
        entry.visible = request.visible
        entry.view.setBounds(request.bounds)
        entry.view.setVisible(request.visible)
        entry.overlay?.setBounds(request.bounds)
        entry.overlay?.setVisible(request.visible)
        break
      }
      case 'beginCalibration':
        this.endCalibration(entry)
        entry.snapshot.draft = request.draft
        entry.snapshot.zoomFactor = request.draft.zoomFactor
        entry.view.webContents.setZoomFactor(request.draft.zoomFactor)
        entry.overlay = this.createOverlay(request.platform)
        entry.window.contentView.addChildView(entry.overlay)
        entry.overlay.setBounds(entry.snapshot.bounds)
        entry.overlay.setVisible(entry.visible)
        break
      case 'draft':
        if (!entry.overlay) throw new Error('Calibration is not active')
        entry.snapshot.draft = request.draft
        break
      case 'endCalibration': this.endCalibration(entry); break
    }
    return entry.snapshot
  }
}
