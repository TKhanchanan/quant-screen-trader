import { CapitalBearAssetDetector, IQOptionAssetDetector, normalizeAsset } from './asset-detector'
import { chartGridBounds, type AssetDetectionResult, type CalibrationSlot } from '@quant-screen-trader/shared-types'
import { normalizeBitmap, type NormalizedImage, type ObservationContext, type ParsedFields } from './market-providers'
import { normalizedToPixel } from '@quant-screen-trader/shared-types'
import { WebContentsView, BrowserWindow, type WebContents } from 'electron'
import { type BrowserSnapshot, type Platform, type PlatformCommand } from '@quant-screen-trader/shared-types'
import { allowedLoginNavigation, allowedNavigation, getPlatformConfig, safeOrigin } from '../platforms/config'

interface Entry {
  window: BrowserWindow
  view: WebContentsView
  overlay: WebContentsView | null
  snapshot: BrowserSnapshot
  visible: boolean
  revision: number
  preparedRevision: number
}
function imageActivity(image: NormalizedImage, top: number, bottom: number): number {
  const x0 = Math.floor(image.width * .06), x1 = Math.floor(image.width * .98)
  const y0 = Math.floor(image.height * top), y1 = Math.floor(image.height * bottom)
  let total = 0, count = 0
  for (let y = y0 + 1; y < y1; y += 2) for (let x = x0 + 1; x < x1; x += 2) {
    const value = image.grayscale[y * image.width + x]!
    total += Math.abs(value - image.grayscale[y * image.width + x - 1]!) +
      Math.abs(value - image.grayscale[(y - 1) * image.width + x]!)
    count++
  }
  return count ? total / count : 0
}
export function chartSurfaceActivity(image: NormalizedImage): { middle: number; lower: number; portfolioOpen: boolean } {
  const middle = imageActivity(image, .18, .58), lower = imageActivity(image, .68, .88)
  return { middle, lower, portfolioOpen: middle >= 2.5 && lower < 1.5 }
}
export class PlatformBrowserManager {
  private readonly entries = new Map<Platform, Entry>()
  private readonly assetScans = new Set<Platform>()
  private readonly preparations = new Map<Platform, Promise<void>>()
  constructor(private readonly createOverlay: (platform: Platform) => WebContentsView) {}
  attach(platform: Platform, window: BrowserWindow): void {
    const config = getPlatformConfig(platform)
    const view = new WebContentsView({ webPreferences: { partition: config.sessionPartition,
      contextIsolation: true, nodeIntegration: false, sandbox: true, webSecurity: true,
      navigateOnDragDrop: false, spellcheck: false } })
    const entry: Entry = { window, view, overlay: null, visible: false, revision: 0, preparedRevision: -1,
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
      entry.revision++
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
    contents.on('dom-ready', () => state('UNKNOWN', 'loaded'))
    contents.on('did-finish-load', () => state('UNKNOWN', 'loaded'))
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
  private prepareChartGrid(platform: Platform): Promise<void> {
    const active = this.preparations.get(platform)
    if (active) return active
    const preparation = this.ensureChartGrid(platform)
    this.preparations.set(platform, preparation)
    return preparation.finally(() => { if (this.preparations.get(platform) === preparation) this.preparations.delete(platform) })
  }
  private async ensureChartGrid(platform: Platform): Promise<void> {
    const entry = this.entries.get(platform), surface = this.observationSurface(platform)
    if (!entry || !surface.available || surface.paused) throw new Error('Chart grid preparation unavailable')
    if (entry.preparedRevision === entry.revision) return
    const capture = async (): Promise<NormalizedImage> => {
      const native = await entry.view.webContents.capturePage()
      if (native.isEmpty()) throw new Error('Empty chart surface capture')
      const size = native.getSize()
      return normalizeBitmap(native.toBitmap(), size.width, size.height, undefined, false)
    }
    const wait = (milliseconds: number): Promise<void> => new Promise(resolve => setTimeout(resolve, milliseconds))
    let image = await capture(), activity = chartSurfaceActivity(image)
    for (let attempt = 0; activity.middle < 2.5 && attempt < 10; attempt++) {
      await wait(500); image = await capture(); activity = chartSurfaceActivity(image)
    }
    if (activity.middle < 2.5) throw new Error('Chart grid preparation failed: the visible trading grid did not become ready.')
    if (activity.portfolioOpen) {
      entry.window.show(); entry.window.focus(); entry.view.webContents.focus()
      const point = { x: Math.round(surface.bounds.width * .982),
        y: Math.round(surface.bounds.height * (platform === 'capitalbear' ? .625 : .631)) }
      entry.view.webContents.sendInputEvent({ type: 'mouseMove', ...point })
      entry.view.webContents.sendInputEvent({ type: 'mouseDown', button: 'left', clickCount: 1, ...point })
      entry.view.webContents.sendInputEvent({ type: 'mouseUp', button: 'left', clickCount: 1, ...point })
      for (let attempt = 0; attempt < 10; attempt++) {
        await wait(attempt ? 500 : 800); image = await capture(); activity = chartSurfaceActivity(image)
        if (activity.middle >= 2.5 && activity.lower >= 1.5) break
      }
      if (activity.middle < 2.5 || activity.lower < 1.5)
        throw new Error('Chart grid preparation failed: the open portfolio panel could not be collapsed safely.')
    }
    entry.preparedRevision = entry.revision
  }
  observationSurface(platform: Platform): { available: boolean; paused: boolean; revision: number; bounds: BrowserSnapshot['bounds'] } {
    const entry = this.entries.get(platform)
    return { available: !!entry && entry.visible && !entry.view.webContents.isDestroyed() &&
      entry.snapshot.session.loadState === 'loaded' && entry.snapshot.session.state !== 'ERROR' &&
      allowedNavigation(getPlatformConfig(platform), entry.view.webContents.getURL()),
      paused: !!entry?.overlay, revision: entry?.revision ?? 0, bounds: entry?.snapshot.bounds ?? { x: 0, y: 0, width: 1, height: 1 } }
  }
  async detectAssets(platform: Platform, calibration?: CalibrationSlot[]): Promise<AssetDetectionResult> {
    const entry = this.entries.get(platform), surface = this.observationSurface(platform)
    if (!entry || !surface.available || surface.paused) throw new Error('Platform unavailable for asset detection')
    await this.prepareChartGrid(platform)
    const evaluate = (script: string): Promise<unknown> => entry.view.webContents.executeJavaScript(script)
    return (platform === 'capitalbear' ? new CapitalBearAssetDetector(evaluate) : new IQOptionAssetDetector(evaluate)).detectAssets(calibration)
  }
  async captureSlot(context: ObservationContext): Promise<NormalizedImage> {
    await this.prepareChartGrid(context.platform)
    const surface = this.observationSurface(context.platform)
    const entry = this.entries.get(context.platform)
    if (!entry || !surface.available || surface.paused || this.assetScans.has(context.platform)) throw new Error('Capture unavailable')
    const roi = normalizedToPixel(context.priceBounds ?? context.bounds, surface.bounds.width, surface.bounds.height)
    const x = Math.floor(roi.x), y = Math.floor(roi.y)
    const image = await entry.view.webContents.capturePage({ x, y,
      width: Math.min(surface.bounds.width - x, Math.ceil(roi.width)),
      height: Math.min(surface.bounds.height - y, Math.ceil(roi.height)) })
    if (image.isEmpty()) throw new Error('Empty capture')
    // Normalize the native device scale to bounded pixel dimensions, preserve original in memory only.
    const scale = Math.min(2, 1024 / roi.width, 1024 / roi.height)
    const resized = image.resize({ width: Math.max(1, Math.round(roi.width * scale)), height: Math.max(1, Math.round(roi.height * scale)) })
    const size = resized.getSize()
    return normalizeBitmap(resized.toBitmap(), size.width, size.height)
  }
  async captureAssetLabel(platform: Platform, slotId: number, calibration: CalibrationSlot[],
    recognize: (image: NormalizedImage) => Promise<ParsedFields>): Promise<ParsedFields> {
    const surface = this.observationSurface(platform), entry = this.entries.get(platform)
    const slot = calibration.find(candidate => candidate.id === slotId)
    if (!entry || !surface.available || surface.paused || !slot || this.assetScans.has(platform))
      throw new Error('Asset label capture unavailable')
    const grid = normalizedToPixel(chartGridBounds(calibration), surface.bounds.width, surface.bounds.height)
    const target = normalizedToPixel(slot.bounds, surface.bounds.width, surface.bounds.height)
    const firstWidth = Math.min(...calibration.map(candidate => candidate.bounds.width)) * surface.bounds.width
    const firstHeight = Math.min(...calibration.map(candidate => candidate.bounds.height)) * surface.bounds.height
    const click = (x: number, y: number): void => {
      const point = { x: Math.round(x), y: Math.round(y) }
      entry.view.webContents.sendInputEvent({ type: 'mouseMove', ...point })
      entry.view.webContents.sendInputEvent({ type: 'mouseDown', button: 'left', clickCount: 1, ...point })
      entry.view.webContents.sendInputEvent({ type: 'mouseUp', button: 'left', clickCount: 1, ...point })
    }
    const wait = (milliseconds: number): Promise<void> => new Promise(resolve => setTimeout(resolve, milliseconds))
    const captureTitle = async (): Promise<NormalizedImage> => {
      const x = Math.round(grid.x + firstWidth * .2), y = Math.round(grid.y)
      const width = Math.max(1, Math.min(surface.bounds.width - x, Math.round(firstWidth * .78)))
      const height = Math.max(1, Math.min(surface.bounds.height - y, Math.round(firstHeight * .25)))
      const image = await entry.view.webContents.capturePage({ x, y, width, height })
      if (image.isEmpty()) throw new Error('Empty asset title capture')
      const resized = image.resize({ width: Math.min(760, width * 2), height: Math.min(96, height * 2) })
      const size = resized.getSize()
      return { ...normalizeBitmap(resized.toBitmap(), size.width, size.height, undefined, false),
        purpose: 'ASSET', png: resized.toPNG() }
    }
    const difference = (a: NormalizedImage, b: NormalizedImage): number => {
      if (a.width !== b.width || a.height !== b.height) return 1
      let total = 0
      for (let index = 0; index < a.grayscale.length; index++) total += Math.abs(a.grayscale[index]! - b.grayscale[index]!)
      return total / a.grayscale.length / 255
    }
    const visibleAsset = (fields: ParsedFields): string | null => fields.asset ? normalizeAsset(fields.asset) : null
    this.assetScans.add(platform)
    try {
      entry.window.show(); entry.window.focus(); entry.view.webContents.focus()
      const baseline = await captureTitle(), baselineFields = await recognize(baseline)
      let expanded: NormalizedImage | null = null, fields: ParsedFields | null = null
      for (let attempt = 0; attempt < 2; attempt++) {
        click(target.x + target.width * .058, target.y + target.height * .31)
        await wait(420)
        const samples: { image: NormalizedImage; fields: ParsedFields; asset: string | null }[] = []
        for (let read = 0; read < 4; read++) {
          if (read) await wait(160)
          const image = await captureTitle(), parsed = await recognize(image)
          samples.push({ image, fields: parsed, asset: visibleAsset(parsed) })
          const names = samples.map(sample => sample.asset).filter((name): name is string => !!name)
          const stable = names.find(name => names.filter(candidate => candidate === name).length >= 2)
          if (stable && difference(baseline, image) >= .015) {
            const matching = samples.filter(sample => sample.asset === stable)
            expanded = image; fields = { ...matching.at(-1)!.fields,
              confidence: Math.max(...matching.map(sample => sample.fields.confidence), .96) }; break
          }
        }
        if (expanded && fields) break
        const confirmation = samples.at(-1)!
        if (difference(baseline, confirmation.image) >= .015) {
          const names = samples.map(sample => sample.asset ?? 'not found').join(' / ')
          entry.view.webContents.reload()
          throw new Error(`Slot ${slotId} expansion changed the canvas but its title was not stable (${names}); the platform was reloaded and no asset was applied.`)
        }
      }
      if (!expanded || !fields) throw new Error(`Slot ${slotId} expand control was not reached after 2 bounded attempts.`)
      const asset = visibleAsset(fields)!
      // The expanded chart keeps its toggle at the first cell's original control point.
      const baselineAsset = visibleAsset(baselineFields)
      let restoredLayout = false
      for (let attempt = 0; attempt < 3; attempt++) {
        click(grid.x + firstWidth * .058, grid.y + firstHeight * .5)
        await wait(360)
        const restored = await captureTitle(), restoredFields = await recognize(restored)
        const restoredAsset = visibleAsset(restoredFields)
        if (restoredAsset !== asset || (baselineAsset === asset && difference(baseline, restored) < .06 &&
          difference(expanded, restored) >= .015)) { restoredLayout = true; break }
      }
      if (!restoredLayout) {
        entry.view.webContents.reload()
        throw new Error(`Chart grid restoration failed after Slot ${slotId}; the platform was reloaded, scan stopped, and configuration was preserved.`)
      }
      return fields
    } finally { this.assetScans.delete(platform) }
  }
  async readSlotDOM(context: ObservationContext): Promise<ParsedFields> {
    const entry = this.entries.get(context.platform), surface = this.observationSurface(context.platform)
    if (!entry || !surface.available || surface.paused || this.assetScans.has(context.platform)) throw new Error('DOM unavailable')
    // Fixed selectors only; no input fields, page state, attributes, cookies, or network inspection.
    // ROI is normalized in viewport CSS pixels, which accounts for browser zoom.
    const script = `(() => {
      const b = ${JSON.stringify(context.bounds)};
      const read = (selector) => {
        const nodes = [...document.querySelectorAll(selector)].filter(e => {
          if (e.closest('input,textarea,form,[contenteditable], [hidden]')) return false;
          const r = e.getBoundingClientRect(), s = getComputedStyle(e);
          return s.visibility === 'visible' && s.display !== 'none' && Number(s.opacity) > 0 &&
            r.width > 0 && r.height > 0 && r.left >= b.x * innerWidth && r.top >= b.y * innerHeight &&
            r.right <= (b.x + b.width) * innerWidth && r.bottom <= (b.y + b.height) * innerHeight;
        });
        return nodes.length === 1 ? nodes[0].innerText?.trim().slice(0, 120) : undefined;
      };
      const rawAsset = read('[data-testid="asset-name"], .asset-name, .instrument-name');
      const asset = rawAsset?.replace(/\\s+/g, ' ').replace(/\\s*\\/\\s*/g, '/').replace(/\\s*\\(OTC\\)$/i, ' OTC');
      const price = read('[data-testid="current-price"], .current-price');
      const payout = read('[data-testid="payout"], .payout-value');
      const timer = read('[data-testid="expiry-timer"], .expiry-timer');
      return { asset: asset === ${JSON.stringify(context.assetName)} ? asset : undefined,
        price: /^(?:0|[1-9]\\d*)(?:\\.\\d+)?$/.test(price ?? '') ? price : undefined,
        payout: /^\\d{1,3}%$/.test(payout ?? '') ? payout : undefined,
        timer: /^\\d{1,3}:[0-5]\\d$/.test(timer ?? '') ? timer : undefined, confidence: .9 };
    })()`
    const result: unknown = await entry.view.webContents.executeJavaScript(script)
    if (!result || typeof result !== 'object') return { confidence: 0 }
    const value = result as Record<string, unknown>
    return { confidence: .9,
      ...(typeof value.asset === 'string' && normalizeAsset(value.asset) === normalizeAsset(context.assetName) ? { asset: value.asset } : {}),
      ...(typeof value.price === 'string' && /^(?:0|[1-9]\d*)(?:\.\d+)?$/.test(value.price) ? { price: value.price } : {}),
      ...(typeof value.payout === 'string' && /^\d{1,3}%$/.test(value.payout) ? { payout: value.payout } : {}),
      ...(typeof value.timer === 'string' && /^\d{1,3}:[0-5]\d$/.test(value.timer) ? { timer: value.timer } : {}) }
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
        entry.preparedRevision = -1
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
