import { CapitalBearAssetDetector, IQOptionAssetDetector, emptyAsset, normalizeAsset } from './asset-detector'
import { createHash } from 'node:crypto'
import { calibrationToChartGrid, calibrationZoomMatches, isAutoCalibration, normalizedToPixel, type AssetDetectionResult, type CalibrationProfile, type CalibrationSlot } from '@quant-screen-trader/shared-types'
import { chartGridResolver } from './chart-grid'
import { normalizeBitmap, type NormalizedImage, type ObservationContext, type ParsedFields } from './market-providers'
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
// Readiness only. The chart band is measured to tell a painted trading grid from a splash or a
// blank surface; how far the grid extends down the surface is settled by the verified geometry,
// not guessed from texture, so an expanded portfolio panel below the charts is not a blocker.
export function chartSurfaceActivity(image: NormalizedImage): { middle: number; ready: boolean } {
  const middle = imageActivity(image, .18, .58)
  return { middle, ready: middle >= 2.5 }
}

export interface PixelBounds { x: number; y: number; width: number; height: number }
const DEFAULT_PLATFORM_ZOOM_FACTOR = .7
interface CapturedTab extends ParsedFields {
  present: boolean; tabIndex?: number; pixelBounds?: PixelBounds; rawOCR?: string[]; tabs?: PixelBounds[]; nameHash?: string
}
/**
 * Same tab bar: same count, same left-to-right order, each tab in the same place. Compared with a
 * tolerance rather than exactly — a broker reflows its tab bar by a pixel or two after a resize,
 * and demanding byte-identical geometry across a whole nine-tab scan rejected every real sync.
 */
function sameTabs(a: PixelBounds[] | undefined, b: PixelBounds[] | undefined, width: number): boolean {
  if (!a || !b || a.length !== b.length || !a.length) return false
  const tolerance = Math.max(2, width * .01)
  return a.every((tab, index) => Math.abs(tab.x - b[index]!.x) <= tolerance &&
    Math.abs(tab.width - b[index]!.width) <= tolerance)
}
function tabNameBounds(tab: PixelBounds): PixelBounds {
  return { x: Math.floor(tab.x + tab.width * .28), y: Math.floor(tab.y + tab.height * .12),
    width: Math.max(1, Math.floor(tab.width * .7)), height: Math.max(1, Math.floor(tab.height * .43)) }
}
// The title line a chart cell prints for itself, clear of its close button, instrument icon and
// the instrument-type subtitle below it. Read only to finish a tab name the tab bar had to clip.
function chartNameBounds(cell: PixelBounds): PixelBounds {
  return { x: Math.floor(cell.x + cell.width * .12), y: Math.floor(cell.y + cell.height * .025),
    width: Math.max(1, Math.floor(cell.width * .5)), height: Math.max(1, Math.floor(cell.height * .085)) }
}
/**
 * The legible part of a clipped tab label, e.g. "Australian D…" -> "Australian D". Read from the
 * raw OCR text: a clipped label never normalizes to an asset, so it carries no parsed name at all.
 * A prefix is only returned when the reads agree on it, and it is evidence, never an identity.
 */
/**
 * A chart title carries its own dropdown chevron, which OCR renders as one or two stray glyphs
 * after the name. The instrument suffix closes with a bracket, so anything past it is noise.
 */
export function chartTitleText(line: string): string {
  return line.replace(/\)\s.*$/, ')').replace(/\s+[+vVwW~^|]+$/, '').trim()
}
/**
 * The legible head of a single clipped label. Two brokers clip differently: one appends an
 * ellipsis, the other simply lets the tab overflow, which leaves a bracket it never closed.
 * Both cases keep the leading text, which is evidence — never an identity on its own.
 */
function clippedLabelPrefix(line: string): string | null {
  const text = line.trim().replace(/^[^\p{L}]+/u, '').replace(/\s+/g, ' ')
  const head = /(?:\.{2,}|…)/.test(text)
    ? text.split(/\.{2,}|…/)[0]
    : text.includes('(') && !text.includes(')')
      ? text.slice(0, text.indexOf('('))
      : undefined
  const prefix = head?.trim()
  return prefix && prefix.length >= 3 ? prefix : null
}
export function clippedPrefix(rawOCR: string[] | undefined): string | null {
  const votes = new Map<string, number>()
  for (const raw of rawOCR ?? []) for (const line of raw.split(/\r?\n/)) {
    const prefix = clippedLabelPrefix(line)
    if (prefix) votes.set(prefix, (votes.get(prefix) ?? 0) + 1)
  }
  const ranked = [...votes.entries()].sort((a, b) => b[1] - a[1] || a[0].length - b[0].length)
  return ranked[0] && ranked[0][1] >= 2 ? ranked[0][0] : null
}
// Verified against both live 3×3 broker layouts: the opened tabs run left to right in the same
// order as the row-major canvas cells, so Tab N always addresses Chart Slot N. The indirection
// stays so a platform that ever orders its grid differently gets its own mapping here.
export function canvasSlotForTab(platform: Platform, tabIndex: number, count: number): number {
  if (!['capitalbear', 'iqoption'].includes(platform) || tabIndex < 1 || tabIndex > count || count > 9) throw new Error('MAPPING: invalid tab index')
  return tabIndex
}

export function findAssetTabs(image: NormalizedImage, platform: Platform): PixelBounds[] {
  // The opened tabs have long, separated underlines. Locate their actual band first
  // so chart titles below it cannot extend an apparent vertical tab boundary.
  const underlineRows: { y: number; runs: { x: number; width: number }[] }[] = []
  for (let y = 2; y < Math.min(image.height - 3, image.height * .12); y++) {
    const runs: { x: number; width: number }[] = []
    let start = -1
    for (let x = Math.floor(image.width * .08); x < image.width * .985; x++) {
      const value = image.grayscale[y * image.width + x]!
      if (value >= 90 && value - image.grayscale[(y + 3) * image.width + x]! > 25) {
        if (start < 0) start = x
      } else if (start >= 0) {
        if (x - start >= image.width * .025 && x - start <= image.width * .18) runs.push({ x: start, width: x - start })
        start = -1
      }
    }
    if (runs.length) underlineRows.push({ y, runs })
  }
  const underline = underlineRows.sort((a, b) => b.runs.length - a.runs.length ||
    b.runs.reduce((sum, run) => sum + run.width, 0) - a.runs.reduce((sum, run) => sum + run.width, 0))[0]
  if (underline && underline.runs.length <= 9) {
    const band = underlineRows.filter(row => Math.abs(row.y - underline.y) <= 2 && row.runs.length === underline.runs.length)
    underline.runs = underline.runs.map((run, index) => {
      const pieces = band.map(row => row.runs[index]!).filter(piece => Math.abs(piece.x - run.x) < image.width * .025)
      const x = Math.min(...pieces.map(piece => piece.x))
      return { x, width: Math.max(...pieces.map(piece => piece.x + piece.width)) - x }
    })
    const tabs = underline.runs.map(run => {
      let top = underline.y
      for (let y = underline.y - 1; y > Math.max(1, underline.y - image.height * .1); y--) {
        const inside = image.grayscale[y * image.width + run.x + Math.floor(run.width / 2)]!
        const outside = image.grayscale[y * image.width + Math.max(0, run.x - 3)]!
        if (inside - outside >= 5) top = y
      }
      return { ...run, y: top, height: underline.y - top + 1 }
    })
    if (tabs.some((tab, i) => i > 0 && tab.x - tabs[i - 1]!.x - tabs[i - 1]!.width > Math.max(tab.width, tabs[i - 1]!.width) * .35)) return []
    if (tabs.every(tab => tab.height >= image.height * .025 && tab.width / tab.height >= 1.5) &&
      tabs.every(tab => Math.abs(tab.y - tabs[0]!.y) <= 3)) return tabs
  }
  const top = Math.max(1, Math.floor(image.height * .005))
  const bottom = Math.min(image.height, Math.ceil(image.height * .12))
  const start = Math.floor(image.width * (platform === 'capitalbear' ? .14 : .1))
  const end = Math.ceil(image.width * .985)
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
      const width = right.x - left.x
      if (width < minimumWidth || width > maximumWidth ||
        Math.abs(right.top - left.top) > 3 || Math.abs(right.bottom - left.bottom) > 3) return []
    const y = Math.max(left.top, right.top), height = Math.min(left.bottom, right.bottom) - y + 1
    return height >= image.height * .03 ? [{ x: left.x, y, width, height }] : []
  }))
  const chains: PixelBounds[][] = []
  for (const [index, tab] of candidates.entries()) {
    const previous = candidates.slice(0, index).map((candidate, previousIndex) => ({ candidate, chain: chains[previousIndex]! }))
      .filter(({ candidate }) => {
        const gap = tab.x - candidate.x - candidate.width
        return gap >= 2 && gap <= Math.max(tab.width, candidate.width) * .35 && Math.abs(tab.y - candidate.y) <= 3 &&
          Math.abs(tab.height - candidate.height) <= 3 &&
          Math.abs(tab.width - candidate.width) <= Math.max(tab.width, candidate.width) * .08
      }).sort((a, b) => b.chain.length - a.chain.length)[0]
    chains[index] = [...(previous?.chain ?? []), tab]
  }
  const best = chains.sort((a, b) => b.length - a.length || a[0]!.x - b[0]!.x)[0] ?? []
  if (best.length > 9 || candidates.some(tab => best.length && Math.abs(tab.y - best[0]!.y) <= 3 &&
    Math.abs(tab.width - best[0]!.width) < best[0]!.width * .1 &&
    (tab.x + tab.width < best[0]!.x || tab.x > best.at(-1)!.x + best.at(-1)!.width))) return []
  return best
}

export class PlatformBrowserManager {
  private readonly entries = new Map<Platform, Entry>()
  private readonly identifiedTabs = new Map<Platform, CapturedTab[]>()
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
      entry.snapshot.grid = null
      this.identifiedTabs.delete(platform)
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
    contents.on('dom-ready', () => { contents.setZoomFactor(entry.snapshot.zoomFactor); state('UNKNOWN', 'loaded') })
    contents.on('did-finish-load', () => { contents.setZoomFactor(entry.snapshot.zoomFactor); state('UNKNOWN', 'loaded') })
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
    let activity = chartSurfaceActivity(await capture())
    for (let attempt = 0; !activity.ready && attempt < 10; attempt++) {
      await wait(500); activity = chartSurfaceActivity(await capture())
    }
    if (!activity.ready) throw new Error('Chart grid preparation failed: the visible trading grid did not become ready.')
    entry.preparedRevision = entry.revision
  }
  observationSurface(platform: Platform): { available: boolean; paused: boolean; revision: number; bounds: BrowserSnapshot['bounds']; zoomFactor: number; gridReady: boolean } {
    const entry = this.entries.get(platform)
    if (entry && !entry.view.webContents.isDestroyed()) {
      // Chromium keeps zoom per origin and resets it across navigations. The snapshot owns the
      // operating zoom, so put the browser back on it rather than adopting whatever it reports —
      // adopting it silently returned the broker to 100% and hid most of the opened tabs.
      const actual = entry.view.webContents.getZoomFactor()
      if (Math.abs(actual - entry.snapshot.zoomFactor) > .001) {
        entry.view.webContents.setZoomFactor(entry.snapshot.zoomFactor)
        entry.snapshot.grid = null; entry.revision++
        this.identifiedTabs.delete(platform)
      }
    }
    return { available: !!entry && entry.visible && !entry.view.webContents.isDestroyed() &&
      entry.snapshot.session.loadState === 'loaded' && entry.snapshot.session.state !== 'ERROR' &&
      allowedNavigation(getPlatformConfig(platform), entry.view.webContents.getURL()),
      paused: !!entry?.overlay, revision: entry?.revision ?? 0, bounds: entry?.snapshot.bounds ?? { x: 0, y: 0, width: 1, height: 1 },
      zoomFactor: entry?.snapshot.zoomFactor ?? DEFAULT_PLATFORM_ZOOM_FACTOR, gridReady: !!entry?.snapshot.grid }
  }
  async resolveGrid(platform: Platform): Promise<BrowserSnapshot> {
    const entry = this.entries.get(platform), surface = this.observationSurface(platform)
    if (!entry || !surface.available || surface.paused) throw new Error('CANVAS_GEOMETRY_UNCERTAIN: show the broker grid first.')
    entry.snapshot.grid = null
    const signature = JSON.stringify(this.observationSurface(platform))
    const capture = async () => {
      const image = await entry.view.webContents.capturePage(), size = image.getSize()
      if (image.isEmpty()) throw new Error('CANVAS_GEOMETRY_UNCERTAIN: empty capture.')
      return chartGridResolver(platform).resolve(normalizeBitmap(image.toBitmap(), size.width, size.height, undefined, false))
    }
    const first = await capture(), second = await capture()
    if (JSON.stringify(this.observationSurface(platform)) !== signature ||
      Object.keys(first.bounds).some(key => Math.abs(first.bounds[key as keyof typeof first.bounds] - second.bounds[key as keyof typeof first.bounds]) > .002))
      throw new Error('CANVAS_GEOMETRY_UNCERTAIN: grid changed during capture. Retry Calibrate Chart Area.')
    entry.snapshot.grid = second
    return entry.snapshot
  }
  useCalibration(platform: Platform, profile: CalibrationProfile): void {
    const entry = this.entries.get(platform)
    if (!entry) throw new Error('Workspace is not open')
    if (!calibrationZoomMatches(profile, entry.snapshot)) {
      entry.snapshot.grid = null
      throw new Error('CALIBRATION_ZOOM_MISMATCH: use Calibrate Chart Area at the current browser zoom.')
    }
    if (!isAutoCalibration(profile)) entry.snapshot.grid = calibrationToChartGrid(platform, profile.slots, 'MANUAL')
  }
  async detectAssets(platform: Platform, calibration?: CalibrationSlot[]): Promise<AssetDetectionResult> {
    const entry = this.entries.get(platform), surface = this.observationSurface(platform)
    if (!entry || !surface.available || surface.paused) throw new Error('Platform unavailable for asset detection')
    const evaluate = (script: string): Promise<unknown> => entry.view.webContents.executeJavaScript(script)
    return (platform === 'capitalbear' ? new CapitalBearAssetDetector(evaluate) : new IQOptionAssetDetector(evaluate)).detectAssets(calibration)
  }
  chartSlot(platform: Platform, tabIndex: number, assetName: string): number {
    const tabs = this.identifiedTabs.get(platform), tab = tabs?.[tabIndex - 1]
    if (!tab || tab.confidence < .95 || normalizeAsset(tab.asset ?? '') !== normalizeAsset(assetName))
      throw new Error('TAB: identity is uncertain. Sync Assets before observing this slot.')
    return canvasSlotForTab(platform, tabIndex, tabs!.filter(t => t.present).length)
  }
  async captureAssetTabs(platform: Platform, recognize: (image: NormalizedImage) => Promise<ParsedFields>): Promise<AssetDetectionResult> {
    this.identifiedTabs.delete(platform)
    const start = Date.now(), tabs: CapturedTab[] = []
    const width = this.observationSurface(platform).bounds.width
    // A whole scan is applied atomically. Two captures and OCR agreements are required per tab.
    const slots = Array.from({ length: 9 }, (_, i) => ({ id: i + 1, bounds: { x: 0, y: 0, width: 1, height: 1 } }))
    for (const slot of slots) {
      const first = await this.captureAssetLabel(platform, slot.id, slots, recognize)
      const second = await this.captureAssetLabel(platform, slot.id, slots, recognize)
      if (!sameTabs(first.tabs, second.tabs, width) || (tabs.length && !sameTabs(second.tabs, tabs[0]!.tabs, width)))
        throw new Error('TAB_GEOMETRY_UNCERTAIN: tab count or physical order changed during sync.')
      tabs.push({ ...second, confidence: first.present === second.present && first.nameHash === second.nameHash &&
        normalizeAsset(first.asset ?? '') === normalizeAsset(second.asset ?? '') ? Math.min(first.confidence, second.confidence) : 0,
        rawOCR: [...(first.rawOCR ?? []), ...(second.rawOCR ?? [])] })
    }
    const present = tabs.filter(tab => tab.present).length
    for (const [index, tab] of tabs.entries()) {
      if (!tab.present || normalizeAsset(tab.asset ?? '')) continue
      const prefix = clippedPrefix(tab.rawOCR)
      const completed = prefix && await this.completeClippedTab(platform, index + 1, present, prefix, recognize)
      if (completed) { tab.asset = completed.asset; tab.confidence = .95; tab.rawOCR = [...(tab.rawOCR ?? []), ...completed.rawOCR] }
    }
    this.identifiedTabs.set(platform, tabs)
    const detected = tabs.map((tab, index) => {
      const name = tab.asset ? normalizeAsset(tab.asset) : null
      const valid = name && tab.confidence >= .95
      return { ...emptyAsset(platform, index + 1), source: 'OCR' as const,
        state: !tab.present ? 'NOT_FOUND' as const : valid ? 'DETECTED' as const : 'UNCERTAIN' as const,
        confidence: tab.confidence, evidenceType: 'CALIBRATED_OCR' as const,
        assetName: valid ? name : null, displayName: valid ? name : null, canonicalAssetId: valid ? `${platform}:${name}` : null,
        tabIndex: index + 1, pixelBounds: tab.pixelBounds, rawOCR: tab.rawOCR }
    })
    return { platform, slots: detected, durationMs: Date.now() - start,
      overallConfidence: detected.reduce((sum, slot) => sum + slot.confidence, 0) / 9 }
  }
  /**
   * A broker tab bar clips long instrument names to a fixed width, which leaves the tab OCR with
   * a prefix that must never be guessed at. The chart cell the tab addresses prints the same name
   * in full, so read that and accept it only when it continues exactly what the tab still shows.
   */
  private async completeClippedTab(platform: Platform, tabIndex: number, count: number, prefix: string,
    recognize: (image: NormalizedImage) => Promise<ParsedFields>
  ): Promise<{ asset: string; rawOCR: string[] } | null> {
    const entry = this.entries.get(platform), surface = this.observationSurface(platform)
    const grid = entry?.snapshot.grid
    if (!entry || !grid || !surface.available || surface.paused) return null
    const cell = grid.slots.find(slot => slot.slotId === canvasSlotForTab(platform, tabIndex, count))
    if (!cell) return null
    const image = await entry.view.webContents.capturePage()
    if (image.isEmpty()) return null
    const size = image.getSize()
    const { x, y, width, height } = chartNameBounds(normalizedToPixel(cell.chartBounds, size.width, size.height))
    if (width < 8 || height < 4 || x + width > size.width || y + height > size.height) return null
    const resized = image.crop({ x, y, width, height }).resize({ width: width * 3, height: height * 3 })
    const resizedSize = resized.getSize(), bitmap = resized.toBitmap(), variants: ParsedFields[] = []
    for (const threshold of [undefined, 125, 145, 165]) variants.push(await recognize({
      ...normalizeBitmap(bitmap, resizedSize.width, resizedSize.height, threshold, true), purpose: 'ASSET' }))
    const votes = new Map<string, number>()
    for (const variant of variants) {
      for (const line of (variant.rawText ?? variant.asset ?? '').split(/\r?\n/)) {
        const asset = normalizeAsset(chartTitleText(line))
        if (asset && asset.length > prefix.length && asset.toLowerCase().startsWith(prefix.toLowerCase()))
          votes.set(asset, (votes.get(asset) ?? 0) + 1)
      }
    }
    const ranked = [...votes.entries()].sort((a, b) => b[1] - a[1])
    const rawOCR = variants.map(variant => variant.rawText ?? variant.asset ?? '')
    return ranked[0] && ranked[0][1] >= 2 && ranked[0][1] > (ranked[1]?.[1] ?? 0) ? { asset: ranked[0][0], rawOCR } : null
  }
  async captureSlot(context: ObservationContext): Promise<NormalizedImage> {
    await this.prepareChartGrid(context.platform)
    const surface = this.observationSurface(context.platform)
    const entry = this.entries.get(context.platform)
    if (!entry || !surface.available || surface.paused || !surface.gridReady || this.assetScans.has(context.platform)) throw new Error('Capture unavailable: verified chart geometry required')
    this.chartSlot(context.platform, context.slotId, context.assetName)
    const full = await entry.view.webContents.capturePage(), size = full.getSize()
    if (full.isEmpty()) throw new Error('Empty capture')
    const currentTabs = findAssetTabs(normalizeBitmap(full.toBitmap(), size.width, size.height, undefined, false), context.platform)
    const identified = this.identifiedTabs.get(context.platform)![context.slotId - 1]!
    const tab = currentTabs[context.slotId - 1]
    if (!tab || !sameTabs(currentTabs, identified.tabs, size.width) ||
      createHash('sha256').update(full.crop(tabNameBounds(tab)).toBitmap()).digest('hex') !== identified.nameHash)
      throw new Error('TAB: visible tab identity changed. Sync Assets before observing.')
    const roi = normalizedToPixel(context.priceBounds ?? context.bounds, surface.bounds.width, surface.bounds.height)
    const x = Math.floor(roi.x), y = Math.floor(roi.y)
    const scaleX = size.width / surface.bounds.width, scaleY = size.height / surface.bounds.height
    const image = full.crop({ x: Math.floor(x * scaleX), y: Math.floor(y * scaleY),
      width: Math.ceil(roi.width * scaleX), height: Math.ceil(roi.height * scaleY) })
    // Normalize the native device scale to bounded pixel dimensions, preserve original in memory only.
    const scale = Math.min(2, 1024 / roi.width, 1024 / roi.height)
    const resized = image.resize({ width: Math.max(1, Math.round(roi.width * scale)), height: Math.max(1, Math.round(roi.height * scale)) })
    const resizedSize = resized.getSize()
    return { ...normalizeBitmap(resized.toBitmap(), resizedSize.width, resizedSize.height, undefined, false), pixelBounds: roi }
  }
  async captureAssetLabel(platform: Platform, slotId: number, calibration: CalibrationSlot[],
    recognize: (image: NormalizedImage) => Promise<ParsedFields>
  ): Promise<CapturedTab> {
    const surface = this.observationSurface(platform), entry = this.entries.get(platform)
    const configuredSlot = calibration.find(slot => slot.id === slotId)
    if (!entry || !surface.available || surface.paused || !configuredSlot || this.assetScans.has(platform))
      throw new Error('Asset label capture unavailable')
    this.assetScans.add(platform)
    try {
      const capture = async (): Promise<{ image: Electron.NativeImage; normalized: NormalizedImage; tabs: PixelBounds[] }> => {
        const image = await entry.view.webContents.capturePage()
        if (image.isEmpty()) throw new Error(`Empty asset tab capture for Slot ${slotId}`)
        const size = image.getSize()
        const normalized = normalizeBitmap(image.toBitmap(), size.width, size.height, undefined, false)
        const tabs = findAssetTabs(normalized, platform)
        return { image, normalized, tabs }
      }
      const first = await capture(), second = await capture()
      if (!sameTabs(first.tabs, second.tabs, first.image.getSize().width))
        throw new Error('TAB_GEOMETRY_UNCERTAIN: tab bar was not isolated consistently')
      const { image, normalized, tabs } = second
      const tab = tabs[slotId - 1]
      if (!tab) return { confidence: 1, present: false, tabs }
      let brightEdge = 0
      for (let row = Math.floor(tab.y + tab.height * .15); row < tab.y + tab.height * .58; row++)
        for (let column = Math.floor(tab.x + tab.width * .96); column < tab.x + tab.width * .995; column++)
          if (normalized.grayscale[row * normalized.width + column]! >= 150) brightEdge++
      // A tab clipped down to its instrument icon cannot carry a legible name. Judge that by the
      // tab's own shape: comparing its width against the whole surface height rejected every real
      // tab as soon as the capture was taller than a tab is wide.
      if (tab.width < tab.height * 2) return { confidence: 0, present: true, tabs }
      const { x, y, width, height } = tabNameBounds(tab)
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
      return { ...result, tabIndex: slotId, pixelBounds: tab, tabs, rawOCR: variants.map(v => v.rawText ?? v.asset ?? ''),
        nameHash: createHash('sha256').update(image.crop({ x, y, width, height }).toBitmap()).digest('hex'), confidence: brightEdge <= 2 || truncatedOtc ? result.confidence : Math.min(.94, result.confidence), present: true }
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
    this.observationSurface(request.platform)
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
        if (entry.snapshot.bounds.width !== request.bounds.width || entry.snapshot.bounds.height !== request.bounds.height) {
          if (entry.snapshot.grid?.source === 'AUTO') entry.snapshot.grid = null
          entry.revision++
        }
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
        entry.snapshot.draft = { ...request.draft, zoomFactor: entry.snapshot.zoomFactor }
        entry.overlay = this.createOverlay(request.platform)
        entry.window.contentView.addChildView(entry.overlay)
        entry.overlay.setBounds(entry.snapshot.bounds)
        entry.overlay.setVisible(entry.visible)
        break
      case 'draft':
        if (!entry.overlay) throw new Error('Calibration is not active')
        entry.snapshot.draft = { ...request.draft, zoomFactor: entry.snapshot.zoomFactor }
        break
      case 'endCalibration': this.endCalibration(entry); break
    }
    return entry.snapshot
  }
}
