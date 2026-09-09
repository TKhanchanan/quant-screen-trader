import { CapitalBearAssetDetector, IQOptionAssetDetector, normalizeAsset } from './asset-detector'
import { normalizedToPixel, type AssetDetectionResult, type CalibrationSlot } from '@quant-screen-trader/shared-types'
import { normalizeBitmap, type NormalizedImage, type ObservationContext, type ParsedFields } from './market-providers'
import { WebContentsView, BrowserWindow, type WebContents } from 'electron'
import { writeFileSync } from 'node:fs'
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

interface PixelBounds { x: number; y: number; width: number; height: number }
const DEFAULT_PLATFORM_ZOOM_FACTOR = .7

export function findAssetTabs(image: NormalizedImage, platform: Platform): PixelBounds[] {
  const top = Math.max(1, Math.floor(image.height * .005))
  const bottom = Math.min(image.height, Math.ceil(image.height * .12))
  const start = Math.floor(image.width * (platform === 'capitalbear' ? .14 : .1))
  const end = Math.ceil(image.width * (platform === 'capitalbear' ? .78 : .75))
  const edges: { x: number; score: number; top: number; bottom: number }[] = []
  for (let x = start; x < end; x++) {
    const rows: number[] = []
    for (let y = top; y < bottom; y++) {
      for (let offset = -5; offset <= 5; offset++) {
        const column = x + offset
        if (column > 0 && column < image.width &&
          Math.abs(image.grayscale[y * image.width + column]! - image.grayscale[y * image.width + column - 1]!) >= 6) {
          rows.push(y)
          break
        }
      }
    }
    if (rows.length >= image.height * .03) edges.push({ x, score: rows.length, top: rows[0]!, bottom: rows.at(-1)! })
  }
  const boundaries: typeof edges = []
  for (let index = 0; index < edges.length;) {
    let last = index
    while (last + 1 < edges.length && edges[last + 1]!.x <= edges[last]!.x + 1) last++
    boundaries.push(edges.slice(index, last + 1).sort((a, b) => b.score - a.score)[0]!)
    index = last + 1
  }
  const minimumWidth = Math.max(image.width * .035, image.height * .1)
  const maximumWidth = image.width * .18
  const candidates = boundaries.flatMap((left, index) => boundaries.slice(index + 1).flatMap(right => {
      const width = candidate.x - left.x
      if (width < minimumWidth || width > maximumWidth ||
        Math.abs(right.top - left.top) > 3 || Math.abs(right.bottom - left.bottom) > 3) return []
    const y = Math.max(left.top, right.top), height = Math.min(left.bottom, right.bottom) - y + 1
    return height >= image.height * .03 ? [{ x: left.x, y, width, height }] : []
  }))
  const chains = candidates.map((tab, index) => {
    const previous = candidates.slice(0, index).map((candidate, previousIndex) => ({ candidate, chain: chains[previousIndex]! }))
      .filter(({ candidate }) => {
        const gap = tab.x - candidate.x - candidate.width
        return gap >= 2 && gap <= Math.max(tab.width, candidate.width) * .35 &&
          Math.abs(tab.width - candidate.width) <= Math.max(tab.width, candidate.width) * .08
      }).sort((a, b) => b.chain.length - a.chain.length)[0]
    return [...(previous?.chain ?? []), tab]
  })
  return (chains.sort((a, b) => b.length - a.length || a[0]!.x - b[0]!.x)[0] ?? []).slice(0, 9)
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
        bounds: { x: 0, y: 180, width: 1, height: 1 }, zoomFactor: DEFAULT_PLATFORM_ZOOM_FACTOR, draft: null } }
    this.entries.set(platform, entry)
    window.contentView.addChildView(view)
    view.setVisible(false)
    const contents = view.webContents
    contents.setZoomFactor(DEFAULT_PLATFORM_ZOOM_FACTOR)
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
    if (activity.portfolioOpen)
      throw new Error('Chart grid preparation failed: collapse the portfolio panel manually, then retry.')
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
    return normalizeBitmap(resized.toBitmap(), size.width, size.height, undefined, false)
  }
  async captureAssetLabel(platform: Platform, slotId: number, calibration: CalibrationSlot[],
    recognize: (image: NormalizedImage) => Promise<ParsedFields>
  ): Promise<ParsedFields & { present: boolean }> {
    const surface = this.observationSurface(platform), entry = this.entries.get(platform)
    const configuredSlot = calibration.find(slot => slot.id === slotId)
    if (!entry || !surface.available || surface.paused || !configuredSlot || this.assetScans.has(platform))
      throw new Error('Asset label capture unavailable')
    this.assetScans.add(platform)
    try {
      const capture = async (): Promise<{ image: Electron.NativeImage; normalized: NormalizedImage; tabs: PixelBounds[] }> => {
        const image = await entry.view.webContents.capturePage()
        if (image.isEmpty()) throw new Error(`Empty asset tab capture for Slot ${slotId}`)
        if (process.env.QST_CAPTURE_DEBUG) writeFileSync(`/private/tmp/qst-${platform}-zoom.png`, image.toPNG())
        const size = image.getSize()
        const normalized = normalizeBitmap(image.toBitmap(), size.width, size.height, undefined, false)
        const tabs = findAssetTabs(normalized, platform)
        if (process.env.QST_CAPTURE_DEBUG) console.info('[asset-tabs]', platform, size, tabs)
        return { image, normalized, tabs }
      }
      const first = await capture(), second = await capture()
      const stable = first.tabs.length === second.tabs.length && first.tabs.every((tab, index) => {
        const next = second.tabs[index]
        return !!next && Math.abs(tab.x - next.x) <= first.image.getSize().width * .01 &&
          Math.abs(tab.width - next.width) <= first.image.getSize().width * .01
      })
      if (!stable || !second.tabs.length) throw new Error('Asset tab bar was not isolated consistently')
      const { image, normalized, tabs } = second
      const tab = tabs[slotId - 1]
      if (!tab) return { confidence: 1, present: false }
      let brightEdge = 0
      for (let row = Math.floor(tab.y + tab.height * .15); row < tab.y + tab.height * .58; row++)
        for (let column = Math.floor(tab.x + tab.width * .96); column < tab.x + tab.width * .995; column++)
          if (normalized.grayscale[row * normalized.width + column]! >= 150) brightEdge++
      if (tab.width < normalized.height * .12) return { confidence: 0, present: true }
      const x = Math.floor(tab.x + tab.width * .28), y = Math.floor(tab.y + tab.height * .12)
      const width = Math.max(1, Math.floor(tab.width * .7)), height = Math.max(1, Math.floor(tab.height * .43))
      const resized = image.crop({ x, y, width, height }).resize({ width: width * 4, height: height * 4 })
      const croppedSize = resized.getSize()
      const bitmap = resized.toBitmap(), variants: ParsedFields[] = []
      for (const threshold of [undefined, 125, 145, 165]) variants.push(await recognize({
        ...normalizeBitmap(bitmap, croppedSize.width, croppedSize.height, threshold, true), purpose: 'ASSET' }))
      const groups = new Map<string, ParsedFields[]>()
      for (const result of variants) {
        const asset = result.asset ? normalizeAsset(result.asset) : null
        if (asset) groups.set(asset, [...(groups.get(asset) ?? []), result])
      }
      const matches = [...groups.values()].sort((a, b) => b.length - a.length ||
        Math.max(...b.map(v => v.confidence)) - Math.max(...a.map(v => v.confidence)))
      const agreed = matches[0]?.length && matches[0].length >= 2 && matches[0].length > (matches[1]?.length ?? 0)
        ? matches[0] : null
      const result = agreed
        ? { ...agreed.sort((a, b) => b.confidence - a.confidence)[0]!, confidence: Math.max(.95, ...agreed.map(v => v.confidence)) }
        : { ...variants.sort((a, b) => b.confidence - a.confidence)[0]!, confidence: Math.min(.94, variants[0]!.confidence) }
      const truncatedOtc = /\(\s*O(?:T(?:C)?)?\s*(?:\.{2,}|…)/i.test(result.asset ?? '')
      return { ...result, confidence: brightEdge <= 2 || truncatedOtc ? result.confidence : Math.min(.94, result.confidence), present: true }
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
