import { CapitalBearAssetDetector, IQOptionAssetDetector, emptyAsset, normalizeAsset } from './asset-detector'
import { calibrationToChartGrid, calibrationZoomMatches, isAutoCalibration, normalizedToPixel, type AssetDetectionResult, type CalibrationProfile, type CalibrationSlot, type DetectedAsset } from '@quant-screen-trader/shared-types'
import { canvasPriceGeometry, chartGridResolver } from './chart-grid'
import { normalizeBitmap, type NormalizedImage, type ObservationContext, type ParsedFields } from './market-providers'
import { WebContentsView, BrowserWindow, type WebContents } from 'electron'
import { type BrowserSnapshot, type Platform, type PlatformCommand, PLATFORM_DETAILS } from '@quant-screen-trader/shared-types'
import { allowedLoginNavigation, allowedNavigation, getPlatformConfig, safeOrigin } from '../platforms/config'

interface Entry {
  window: BrowserWindow
  view: WebContentsView
  overlay: WebContentsView | null
  snapshot: BrowserSnapshot
  visible: boolean
  revision: number
  preparedRevision: number
  portfolioCleanupDocumentId: number
  portfolioCleanupDone: boolean
  portfolioCleanupPromise: Promise<'CLOSED' | 'ALREADY_CLOSED' | 'NOT_FOUND' | 'FAILED'> | null
  portfolioCleanupStatus?: 'CLOSED' | 'ALREADY_CLOSED' | 'NOT_FOUND' | 'FAILED' | undefined
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
  rawOcrConfidence?: number; identityEvidenceConfidence?: number; geometryConsensus?: boolean; fingerprintStable?: boolean; ocrVotes?: number;
  present: boolean; tabIndex?: number; pixelBounds?: PixelBounds; rawOCR?: string[]; tabs?: PixelBounds[]; nameFingerprint?: Uint8Array
}
/**
 * Same tab bar: same count, same left-to-right order, each tab in the same place. Compared with a
 * tolerance rather than exactly — a broker reflows its tab bar by a pixel or two after a resize,
 * and demanding byte-identical geometry across a whole nine-tab scan rejected every real sync.
 */
export function sameTabs(a: PixelBounds[] | undefined, b: PixelBounds[] | undefined, width: number): { match: boolean; reason?: string; firstCount: number; secondCount: number; xShift?: number; widthShift?: number; tolerance: number } {
  const tolerance = Math.max(2, width * .01)
  if (!a || !b || a.length !== b.length || !a.length) return { match: false, reason: 'COUNT', firstCount: a?.length ?? 0, secondCount: b?.length ?? 0, tolerance }
  let xShift = 0, widthShift = 0
  for (let i = 0; i < a.length; i++) {
    const dx = Math.abs(a[i]!.x - b[i]!.x)
    const dw = Math.abs(a[i]!.width - b[i]!.width)
    xShift = Math.max(xShift, dx)
    widthShift = Math.max(widthShift, dw)
  }
  if (xShift > tolerance) return { match: false, reason: 'X_SHIFT', firstCount: a.length, secondCount: b.length, xShift, widthShift, tolerance }
  if (widthShift > tolerance) return { match: false, reason: 'WIDTH_SHIFT', firstCount: a.length, secondCount: b.length, xShift, widthShift, tolerance }
  return { match: true, firstCount: a.length, secondCount: b.length, xShift, widthShift, tolerance }
}
function tabNameBounds(tab: PixelBounds): PixelBounds {
  return { x: Math.floor(tab.x + tab.width * .28), y: Math.floor(tab.y + tab.height * .12),
    width: Math.max(1, Math.floor(tab.width * .7)), height: Math.max(1, Math.floor(tab.height * .43)) }
}
function tabNameFingerprint(image: NormalizedImage, tab: PixelBounds): Uint8Array {
  const bounds = tabNameBounds(tab), columns = 24, rows = 8, result = new Uint8Array(columns * rows)
  for (let row = 0; row < rows; row++) for (let column = 0; column < columns; column++) {
    const left = Math.floor(bounds.x + column * bounds.width / columns)
    const right = Math.max(left + 1, Math.floor(bounds.x + (column + 1) * bounds.width / columns))
    const top = Math.floor(bounds.y + row * bounds.height / rows)
    const bottom = Math.max(top + 1, Math.floor(bounds.y + (row + 1) * bounds.height / rows))
    let total = 0, count = 0
    for (let y = top; y < Math.min(bottom, image.height); y++) for (let x = left; x < Math.min(right, image.width); x++) {
      total += image.grayscale[y * image.width + x]!; count++
    }
    result[row * columns + column] = count ? Math.round(total / count) : 0
  }
  return result
}
export function sameTabFingerprint(a: Uint8Array | undefined, b: Uint8Array | undefined): boolean {
  if (!a || !b || a.length !== b.length || !a.length) return false
  let difference = 0, changed = 0
  for (let index = 0; index < a.length; index++) {
    const delta = Math.abs(a[index]! - b[index]!)
    difference += delta
    if (delta > 32) changed++
  }
  return difference / a.length <= 12 && changed / a.length <= .1
}
export function chartNameBounds(cell: PixelBounds): PixelBounds {
  return { x: Math.floor(cell.x + cell.width * .12), y: Math.floor(cell.y + cell.height * .025),
    width: Math.max(1, Math.floor(cell.width * .5)), height: Math.max(1, Math.floor(cell.height * .085)) }
}

export function chartTitleFingerprint(image: NormalizedImage, titleBounds: PixelBounds): Uint8Array {
  const columns = 24, rows = 8, result = new Uint8Array(columns * rows)
  for (let row = 0; row < rows; row++) for (let column = 0; column < columns; column++) {
    const left = Math.floor(titleBounds.x + column * titleBounds.width / columns)
    const right = Math.max(left + 1, Math.floor(titleBounds.x + (column + 1) * titleBounds.width / columns))
    const top = Math.floor(titleBounds.y + row * titleBounds.height / rows)
    const bottom = Math.max(top + 1, Math.floor(titleBounds.y + (row + 1) * titleBounds.height / rows))
    let total = 0, count = 0
    for (let y = Math.max(0, top); y < Math.min(bottom, image.height); y++)
      for (let x = Math.max(0, left); x < Math.min(right, image.width); x++) {
        total += image.grayscale[y * image.width + x]!
        count++
      }
    result[row * columns + column] = count ? Math.round(total / count) : 0
  }
  return result
}

export function sameChartTitleFingerprint(a: Uint8Array | undefined, b: Uint8Array | undefined): boolean {
  if (!a || !b || a.length !== b.length || !a.length) return false
  let difference = 0, changed = 0
  for (let index = 0; index < a.length; index++) {
    const delta = Math.abs(a[index]! - b[index]!)
    difference += delta
    if (delta > 32) changed++
  }
  return difference / a.length <= 12 && changed / a.length <= .1
}

export interface IdentifiedChartSlot {
  slotId: number
  assetName: string
  source: 'DOM' | 'OCR'
  confidence: number
  titleFingerprint: Uint8Array
  gridRevision: number
  pixelBounds?: PixelBounds
  state?: DetectedAsset['state']
  evidenceType?: DetectedAsset['evidenceType']
  rawOCR?: string[]
  rawOcrConfidence?: number
  identityEvidenceConfidence?: number
  ocrVotes?: number
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
  const prefix = head?.split('(')[0]?.trim()
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

export function findAssetTabs(image: NormalizedImage, platform: Platform): PixelBounds[] & { branch?: 'UNDERLINE' | 'FALLBACK' } {
  console.log(`[findAssetTabs] ${platform} capture size: ${image.width}x${image.height}`)
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
  let underlineRejectReason: string | null
  if (!underline) {
    underlineRejectReason = 'no underline candidate rows found'
  } else if (underline.runs.length > 9) {
    underlineRejectReason = `run count exceeded 9 (${underline.runs.length})`
  } else {
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
    const hasGap = tabs.some((tab, i) => i > 0 && tab.x - tabs[i - 1]!.x - tabs[i - 1]!.width > Math.max(tab.width, tabs[i - 1]!.width) * .35)
    if (hasGap && (platform !== 'iqoption' || tabs.length >= 4)) {
      const reason = 'rejected due to gap between tabs'
      console.log(`[findAssetTabs] ${platform} UNDERLINE: candidate row count=${underlineRows.length}, best row y=${underline.y}, run count=${underline.runs.length}, reason rejected: ${reason}`)
      console.log(`[findAssetTabs] ${platform} branch: UNDERLINE count: 0 (${reason})`)
      return Object.assign([], { branch: 'UNDERLINE' as const })
    } else if (platform === 'iqoption' && tabs.length < 9) {
      underlineRejectReason = `IQ Option inactive tabs lack underline (found ${tabs.length}); falling back to edge boundaries`
    } else if (!tabs.every(tab => tab.height >= image.height * .025 && tab.width / tab.height >= 1.5)) {
      underlineRejectReason = `tab height or aspect ratio rejected (heights: ${tabs.map(t => t.height).join(',')}, minHeight: ${image.height * .025})`
    } else if (!tabs.every(tab => Math.abs(tab.y - tabs[0]!.y) <= 3)) {
      underlineRejectReason = `tab y variance exceeded 3 (${tabs.map(t => t.y).join(',')})`
    } else {
      console.log(`[findAssetTabs] ${platform} UNDERLINE: candidate row count=${underlineRows.length}, best row y=${underline.y}, run count=${underline.runs.length}, accepted=${tabs.length}`)
      console.log(`[findAssetTabs] ${platform} branch: UNDERLINE count: ${tabs.length}\n` + tabs.map((t, i) => `  tab ${i}: x=${t.x} y=${t.y} w=${t.width} h=${t.height}`).join('\n'))
      return Object.assign(tabs, { branch: 'UNDERLINE' as const })
    }
  }
  console.log(`[findAssetTabs] ${platform} UNDERLINE: candidate row count=${underlineRows.length}, best row y=${underline?.y ?? 'none'}, run count=${underline?.runs.length ?? 0}, reason rejected: ${underlineRejectReason}`)

  const top = Math.max(1, Math.floor(image.height * .005))
  const bottom = Math.min(image.height, Math.ceil(image.height * .12))
  const start = Math.floor(image.width * (platform === 'capitalbear' ? .14 : .08))
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
    if (rows.length >= image.height * .025) edges.push({ x, score: rows.length, top: rows[0]!, bottom: rows.at(-1)! })
  }
  const boundaries: typeof edges = []
  for (let index = 0; index < edges.length;) {
    let last = index
    while (last + 1 < edges.length && edges[last + 1]!.x <= edges[last]!.x + 2) last++
    boundaries.push(edges.slice(index, last + 1).sort((a, b) => b.score - a.score)[0]!)
    index = last + 1
  }
  const minimumWidth = image.width * .035
  const maximumWidth = image.width * .18
  const yTolerance = Math.max(3, Math.round(image.height * 0.008))
  const heightTolerance = Math.max(8, Math.round(image.height * 0.015))
  const candidates = boundaries.flatMap((left, index) => boundaries.slice(index + 1).flatMap(right => {
    const width = right.x - left.x
    if (width < minimumWidth || width > maximumWidth ||
      Math.abs(right.top - left.top) > yTolerance || Math.abs(right.bottom - left.bottom) > heightTolerance) return []
    const y = Math.max(left.top, right.top), height = Math.min(left.bottom, right.bottom) - y + 1
    return height >= image.height * .025 ? [{ x: left.x, y, width, height }] : []
  }))
  const chains: PixelBounds[][] = []
  for (const [index, tab] of candidates.entries()) {
    const previous = candidates.slice(0, index).map((candidate, previousIndex) => ({ candidate, chain: chains[previousIndex]! }))
      .filter(({ candidate }) => {
        const gap = tab.x - candidate.x - candidate.width
        const widthTolerance = platform === 'capitalbear' ? .20 : .08
        return gap >= -4 && gap <= Math.max(tab.width, candidate.width) * .35 &&
          Math.abs(tab.y - candidate.y) <= yTolerance &&
          Math.abs(tab.height - candidate.height) <= heightTolerance &&
          Math.abs(tab.width - candidate.width) <= Math.max(tab.width, candidate.width) * widthTolerance
      }).sort((a, b) => b.chain.length - a.chain.length)[0]
    chains[index] = [...(previous?.chain ?? []), tab]
  }
  const best = chains.sort((a, b) => b.length - a.length || a[0]!.x - b[0]!.x)[0] ?? []
  let fallbackRejectReason: string | null = null
  if (best.length === 0) {
    fallbackRejectReason = 'no candidate chains formed'
  } else if (best.length > 9) {
    fallbackRejectReason = `chain length exceeded 9 (${best.length})`
  } else if (best.length < 9 && candidates.some(tab => best.length && Math.abs(tab.y - best[0]!.y) <= yTolerance &&
    Math.abs(tab.width - best[0]!.width) < best[0]!.width * .1 &&
    (tab.x + tab.width < best[0]!.x || tab.x > best.at(-1)!.x + best.at(-1)!.width))) {
    fallbackRejectReason = `rejected due to bounds/count (candidate outside best chain of length ${best.length})`
  }
  if (fallbackRejectReason) {
    console.log(`[findAssetTabs] ${platform} FALLBACK: edge count=${edges.length}, boundary count=${boundaries.length}, candidate count=${candidates.length}, best chain count=${best.length}, reason rejected: ${fallbackRejectReason}`)
    console.log(`[findAssetTabs] ${platform} branch: FALLBACK count: 0 (${fallbackRejectReason})`)
    return Object.assign([], { branch: 'FALLBACK' as const })
  }
  console.log(`[findAssetTabs] ${platform} FALLBACK: edge count=${edges.length}, boundary count=${boundaries.length}, candidate count=${candidates.length}, best chain count=${best.length}, accepted=${best.length}`)
  console.log(`[findAssetTabs] ${platform} branch: FALLBACK count: ${best.length}\n` + best.map((t, i) => `  tab ${i}: x=${t.x} y=${t.y} w=${t.width} h=${t.height}`).join('\n'))
  return Object.assign(best, { branch: 'FALLBACK' as const })
}

export class PlatformBrowserManager {
  private readonly entries = new Map<Platform, Entry>()
  private readonly identifiedSlots = new Map<Platform, IdentifiedChartSlot[]>()
  private readonly assetScans = new Set<Platform>()
  private readonly preparations = new Map<Platform, Promise<void>>()
  constructor(private readonly createOverlay: (platform: Platform) => WebContentsView) {}
  attach(platform: Platform, window: BrowserWindow): void {
    const config = getPlatformConfig(platform)
    const view = new WebContentsView({ webPreferences: { partition: config.sessionPartition,
      contextIsolation: true, nodeIntegration: false, sandbox: true, webSecurity: true,
      navigateOnDragDrop: false, spellcheck: false } })
    const entry: Entry = { window, view, overlay: null, visible: false, revision: 0, preparedRevision: -1,
      portfolioCleanupDocumentId: 0, portfolioCleanupDone: false, portfolioCleanupPromise: null,
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
      this.identifiedSlots.delete(platform)
      const origin = safeOrigin(contents.getURL())
      entry.snapshot.session = { platform, state: status, loadState, lastUpdatedAt: new Date().toISOString(),
        ...(origin ? { currentUrl: origin } : {}),
        ...(errorCode ? { errorCode, errorMessage: 'Platform unavailable. Check your connection or reload; no security bypass is attempted.' } : {}),
        ...(loginError ? { ...loginError, state: 'ERROR' } : {}) }
    }
    contents.on('did-start-navigation', (_event, url, inPlace, mainFrame) => {
      if (mainFrame && !inPlace) {
        entry.portfolioCleanupDocumentId++
        entry.portfolioCleanupDone = false
        entry.portfolioCleanupPromise = null
        entry.portfolioCleanupStatus = undefined
        if (allowedLoginNavigation(config, url)) {
          loginError = undefined
          state('LOADING', 'loading')
        }
      }
    })
    contents.on('dom-ready', () => {
      contents.setZoomFactor(entry.snapshot.zoomFactor)
      state('UNKNOWN', 'loaded')
      if (platform === 'capitalbear') void this.closePortfolioPanel(platform).catch(() => {})
    })
    contents.on('did-finish-load', () => {
      contents.setZoomFactor(entry.snapshot.zoomFactor)
      state('UNKNOWN', 'loaded')
      if (platform === 'capitalbear') void this.closePortfolioPanel(platform).catch(() => {})
    })
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
  async closePortfolioPanel(platform: Platform): Promise<'CLOSED' | 'ALREADY_CLOSED' | 'NOT_FOUND' | 'FAILED'> {
    // Shadow-live acceptance requires the operator to close broker panels manually.
    if (process.env.QST_SHADOW_LIVE === '1') return 'FAILED'
    const entry = this.entries.get(platform)
    if (!entry || entry.view.webContents.isDestroyed()) return 'FAILED'
    if (entry.portfolioCleanupPromise) return entry.portfolioCleanupPromise
    if (entry.portfolioCleanupDone) return entry.portfolioCleanupStatus ?? 'ALREADY_CLOSED'

    const docId = entry.portfolioCleanupDocumentId
    const cleanup = (async (): Promise<'CLOSED' | 'ALREADY_CLOSED' | 'NOT_FOUND' | 'FAILED'> => {
      const contents = entry.view.webContents
      const wait = (ms: number) => new Promise(r => setTimeout(r, ms))

      for (let attempt = 0; attempt < 15; attempt++) {
        try {
          if (contents.isDestroyed() || entry.portfolioCleanupDocumentId !== docId) return 'FAILED'

          // 1. Try DOM elements first (supports standard DOM buttons/tabs and unit test mocks)
          const domResult = await contents.executeJavaScript(`(() => {
            const isVisible = (e) => {
              if (!e) return false;
              const b = e.getBoundingClientRect(), s = getComputedStyle(e);
              return b.width > 0 && b.height > 0 && s.display !== 'none' && s.visibility === 'visible' &&
                Number(s.opacity) > 0 && !e.closest('[hidden]');
            };
            const candidates = [...document.querySelectorAll('button, [role="button"], [role="tab"], div, span, a')]
              .filter(e => {
                if (e.closest('form,input,textarea,[contenteditable]')) return false;
                const t = (e.innerText || '').trim();
                const aria = (e.getAttribute('aria-label') || '').trim();
                const title = (e.getAttribute('title') || '').trim();
                const matches = (s) => s === 'พอร์ตทั้งหมด' || s.toLowerCase() === 'total portfolio' ||
                  s.startsWith('พอร์ตทั้งหมด') || s.toLowerCase().startsWith('total portfolio');
                return (matches(t) || matches(aria) || matches(title)) && isVisible(e);
              });
            if (!candidates.length) return { status: 'NOT_FOUND' };
            const toggle = candidates.find(e => e.matches('button, [role="button"], [role="tab"]') || e.getAttribute('aria-expanded') !== null) ||
              candidates.sort((a, b) => (a.innerText?.length || 0) - (b.innerText?.length || 0))[0];
            if (!toggle) return { status: 'NOT_FOUND' };

            const ariaExpanded = toggle.getAttribute('aria-expanded') ?? toggle.closest('[aria-expanded]')?.getAttribute('aria-expanded');
            const activeClass = (toggle.className + ' ' + (toggle.parentElement?.className || '')).toLowerCase();
            const hasActiveIndicator = activeClass.includes('active') || activeClass.includes('opened') || activeClass.includes('expanded') || activeClass.includes('selected');
            const portfolioPanel = document.querySelector('[class*="portfolio"], [data-testid*="portfolio"]');
            const panelVisible = portfolioPanel && isVisible(portfolioPanel) && portfolioPanel.getBoundingClientRect().height > 80;

            const isOpen = ariaExpanded === 'true' || (ariaExpanded !== 'false' && (hasActiveIndicator || panelVisible));
            if (!isOpen) return { status: 'ALREADY_CLOSED' };

            toggle.click();
            return { status: 'CLOSED' };
          })()`) as { status?: 'CLOSED' | 'ALREADY_CLOSED' | 'NOT_FOUND' } | null

          if (domResult?.status === 'CLOSED') {
            await wait(350)
            if (entry.snapshot.grid?.source === 'AUTO') entry.snapshot.grid = null
            entry.preparedRevision = -1
            entry.revision++
            entry.portfolioCleanupDone = true
            entry.portfolioCleanupStatus = 'CLOSED'
            console.log(`[${platform}] PORTFOLIO_CLEANUP: CLOSED`)
            return 'CLOSED'
          }
          if (domResult?.status === 'ALREADY_CLOSED') {
            entry.portfolioCleanupDone = true
            entry.portfolioCleanupStatus = 'ALREADY_CLOSED'
            console.log(`[${platform}] PORTFOLIO_CLEANUP: ALREADY_CLOSED`)
            return 'ALREADY_CLOSED'
          }

          // 2. Canvas inspection & dispatch fallback (WebGL traderoom applications)
          const isOpen = await this.isPortfolioPanelOpen(platform)
          if (!isOpen) {
            entry.portfolioCleanupDone = true
            entry.portfolioCleanupStatus = 'ALREADY_CLOSED'
            console.log(`[${platform}] PORTFOLIO_CLEANUP: ALREADY_CLOSED (canvas)`)
            return 'ALREADY_CLOSED'
          }

          console.log(`[${platform}] Portfolio panel detected OPEN, dispatching close click to canvas...`)
          // Click drawer tab header line (at x: 60, y: height * 0.755)
          const clicked = await contents.executeJavaScript(`(() => {
            const canvas = document.getElementById('glcanvas');
            if (!canvas) return false;
            const rect = canvas.getBoundingClientRect();
            function clickCanvas(clientX, clientY) {
              const opts = { clientX, clientY, screenX: clientX, screenY: clientY, bubbles: true, cancelable: true, view: window, button: 0, buttons: 1 };
              canvas.dispatchEvent(new MouseEvent('mousemove', { ...opts, buttons: 0 }));
              canvas.dispatchEvent(new MouseEvent('mousedown', opts));
              canvas.dispatchEvent(new MouseEvent('mouseup', { ...opts, buttons: 0 }));
              canvas.dispatchEvent(new MouseEvent('click', { ...opts, buttons: 0 }));
            }
            clickCanvas(60, Math.round(rect.height * 0.755));
            return true;
          })()`) as boolean

          if (clicked) {
            await wait(400)
            const stillOpen = await this.isPortfolioPanelOpen(platform)
            if (!stillOpen) {
              if (entry.snapshot.grid?.source === 'AUTO') entry.snapshot.grid = null
              entry.preparedRevision = -1
              entry.revision++
              entry.portfolioCleanupDone = true
              entry.portfolioCleanupStatus = 'CLOSED'
              console.log(`[${platform}] PORTFOLIO_CLEANUP: CLOSED (via drawer tab)`)
              return 'CLOSED'
            }

            // If still open, try clicking left sidebar icon at (22, 75)
            await contents.executeJavaScript(`(() => {
              const canvas = document.getElementById('glcanvas');
              if (!canvas) return;
              function clickCanvas(clientX, clientY) {
                const opts = { clientX, clientY, screenX: clientX, screenY: clientY, bubbles: true, cancelable: true, view: window, button: 0, buttons: 1 };
                canvas.dispatchEvent(new MouseEvent('mousemove', { ...opts, buttons: 0 }));
                canvas.dispatchEvent(new MouseEvent('mousedown', opts));
                canvas.dispatchEvent(new MouseEvent('mouseup', { ...opts, buttons: 0 }));
                canvas.dispatchEvent(new MouseEvent('click', { ...opts, buttons: 0 }));
              }
              clickCanvas(22, 75);
            })()`)
            await wait(400)
            const openAfterSidebar = await this.isPortfolioPanelOpen(platform)
            if (!openAfterSidebar) {
              if (entry.snapshot.grid?.source === 'AUTO') entry.snapshot.grid = null
              entry.preparedRevision = -1
              entry.revision++
              entry.portfolioCleanupDone = true
              entry.portfolioCleanupStatus = 'CLOSED'
              console.log(`[${platform}] PORTFOLIO_CLEANUP: CLOSED (via sidebar icon)`)
              return 'CLOSED'
            }
          }
        } catch {
          // Page may be navigating or busy rendering
        }
        await wait(280)
      }
      entry.portfolioCleanupDone = true
      entry.portfolioCleanupStatus = 'NOT_FOUND'
      console.log(`[${platform}] PORTFOLIO_CLEANUP: NOT_FOUND`)
      return 'NOT_FOUND'
    })()

    entry.portfolioCleanupPromise = cleanup
    return cleanup.finally(() => {
      if (entry.portfolioCleanupPromise === cleanup) entry.portfolioCleanupPromise = null
    })
  }
  async isPortfolioPanelOpen(platform: Platform): Promise<boolean> {
    const entry = this.entries.get(platform)
    if (!entry || entry.view.webContents.isDestroyed()) return false
    try {
      // Check DOM first
      const domOpen = await entry.view.webContents.executeJavaScript(`(() => {
        const isVisible = (e) => {
          if (!e) return false;
          const b = e.getBoundingClientRect(), s = getComputedStyle(e);
          return b.width > 0 && b.height > 0 && s.display !== 'none' && s.visibility === 'visible' &&
            Number(s.opacity) > 0 && !e.closest('[hidden]');
        };
        const portfolioPanel = document.querySelector('[class*="portfolio"], [data-testid*="portfolio"]');
        return Boolean(portfolioPanel && isVisible(portfolioPanel) && portfolioPanel.getBoundingClientRect().height > 80);
      })()`) as boolean
      if (domOpen) return true

      // Check canvas surface pixels (for WebGL traderoom)
      const native = await entry.view.webContents.capturePage()
      if (native.isEmpty()) return false
      const size = native.getSize()
      const bmp = native.toBitmap()

      // Sample lower middle band: y: 0.80..0.88, x: 0.30..0.70
      const startY = Math.round(size.height * 0.80), endY = Math.round(size.height * 0.88)
      const startX = Math.round(size.width * 0.30), endX = Math.round(size.width * 0.70)
      let count = 0, highBrightnessCount = 0, coloredPixels = 0

      for (let y = startY; y < endY; y += 2) {
        for (let x = startX; x < endX; x += 2) {
          const idx = (y * size.width + x) * 4
          const b = bmp[idx]!, g = bmp[idx + 1]!, r = bmp[idx + 2]!
          count++
          if ((r + g + b) / 3 > 70) highBrightnessCount++
          if (Math.abs(r - g) > 25) coloredPixels++
        }
      }
      if (count === 0) return false
      const brightRatio = highBrightnessCount / count
      const colorRatio = coloredPixels / count
      return colorRatio < 0.05 && brightRatio < 0.04
    } catch {
      return false
    }
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
    const status = await this.closePortfolioPanel(platform)
    if (status === 'NOT_FOUND' || status === 'FAILED') {
      const isOpen = await this.isPortfolioPanelOpen(platform)
      if (isOpen) throw new Error(`${PLATFORM_DETAILS[platform].name} portfolio panel is still open. Close it or retry Reload Platform.`)
    }
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
        this.identifiedSlots.delete(platform)
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
    await this.closePortfolioPanel(platform)
    const isOpen = await this.isPortfolioPanelOpen(platform)
    if (isOpen) throw new Error(`${PLATFORM_DETAILS[platform].name} portfolio panel is still open. Close it or retry Reload Platform.`)
    entry.snapshot.grid = null
    const signature = JSON.stringify(this.observationSurface(platform))
    const capture = async () => {
      const image = await entry.view.webContents.capturePage(), size = image.getSize()
      if (image.isEmpty()) throw new Error('CANVAS_GEOMETRY_UNCERTAIN: empty capture.')
      return chartGridResolver(platform).resolve(normalizeBitmap(image.toBitmap(), size.width, size.height, undefined, false))
    }
    const first = await capture(), second = await capture()
    if (JSON.stringify(this.observationSurface(platform)) !== signature ||
      Object.keys(first.bounds).some(key => Math.abs(first.bounds[key as keyof typeof first.bounds] - second.bounds[key as keyof typeof second.bounds]) > .002))
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
  chartSlot(platform: Platform, slotId: number, assetName: string): number {
    const slots = this.identifiedSlots.get(platform)
    const slot = slots?.find(s => s.slotId === slotId)
    if (!slot || slot.confidence < .95 || normalizeAsset(slot.assetName) !== normalizeAsset(assetName))
      throw new Error('CHART_SLOT: identity is uncertain. Sync Assets before observing this slot.')
    return slotId
  }
  async captureAssetTabs(platform: Platform, recognize: (image: NormalizedImage) => Promise<ParsedFields>): Promise<AssetDetectionResult> {
    this.identifiedSlots.delete(platform)
    await this.prepareChartGrid(platform)
    const surface = this.observationSurface(platform)
    const entry = this.entries.get(platform)
    if (!entry || !surface.available || surface.paused || !surface.gridReady)
      throw new Error('Chart grid preparation failed: verified chart geometry required before Sync Assets.')
    const start = Date.now()
    this.assetScans.add(platform)

    const identified: IdentifiedChartSlot[] = []
    const detected: DetectedAsset[] = []
    try {
      // 1. Single captured frame for all 9 chart-cell identities
      const finalImage = await entry.view.webContents.capturePage()
      if (finalImage.isEmpty()) throw new Error('Empty asset capture')
      const size = finalImage.getSize()
      const normalized = normalizeBitmap(finalImage.toBitmap(), size.width, size.height, undefined, false)

      const grid = entry.snapshot.grid?.source === 'AUTO'
        ? chartGridResolver(platform).resolve(normalized)
        : entry.snapshot.grid!
      entry.snapshot.grid = grid

      // 2. Optional top-tab detection for diagnostic cross-check only
      try {
        const tabs = findAssetTabs(normalized, platform)
        console.log(`[SyncAssets] ${platform} optional tab scan: found ${tabs.length} tabs`)
      } catch (err) {
        console.log(`[SyncAssets] ${platform} optional tab scan:`, err instanceof Error ? err.message : String(err))
      }

      // 3. Priority 1: Visible DOM detector
      let domResult: AssetDetectionResult | null = null
      try {
        const calibration: CalibrationSlot[] = grid.slots.map(s => ({
          id: s.slotId,
          bounds: s.chartBounds
        }))
        domResult = await this.detectAssets(platform, calibration)
      } catch (err) {
        console.log(`[SyncAssets] ${platform} DOM detection:`, err instanceof Error ? err.message : String(err))
      }

      // 4. Evaluate each chart cell
      for (let slotId = 1; slotId <= 9; slotId++) {
        const cell = grid.slots.find(s => s.slotId === slotId)
        if (!cell) {
          detected.push(emptyAsset(platform, slotId, 'NOT_FOUND'))
          continue
        }

        const cellPixel = normalizedToPixel(cell.chartBounds, size.width, size.height)
        const titleBounds = chartNameBounds(cellPixel)

        const domSlot = domResult?.slots.find(s => s.slotId === slotId)
        const domName = domSlot?.state === 'DETECTED' && domSlot.assetName ? normalizeAsset(domSlot.assetName) : null

        if (domName && (domSlot?.confidence ?? 0) >= .95) {
          const fingerprint = chartTitleFingerprint(normalized, titleBounds)
          identified.push({
            slotId,
            assetName: domName,
            source: 'DOM',
            confidence: domSlot!.confidence,
            titleFingerprint: fingerprint,
            gridRevision: entry.revision,
            pixelBounds: titleBounds,
            state: 'DETECTED',
            evidenceType: domSlot!.evidenceType ?? 'CHART_LABEL'
          })
          detected.push({
            ...emptyAsset(platform, slotId),
            state: 'DETECTED',
            assetName: domName,
            displayName: domSlot!.displayName ?? domName,
            canonicalAssetId: `${platform}:${domName}`,
            source: 'DOM',
            confidence: domSlot!.confidence,
            evidenceType: domSlot!.evidenceType ?? 'CHART_LABEL',
            pixelBounds: titleBounds,
            identityEvidenceConfidence: domSlot!.confidence,
            fingerprintStable: true,
            geometryConsensus: true,
            tabIndex: slotId
          })
          continue
        }

        // Priority 2: Visual Chart-Title Fallback inside chart cell
        if (titleBounds.width < 8 || titleBounds.height < 4 ||
          titleBounds.x + titleBounds.width > size.width || titleBounds.y + titleBounds.height > size.height) {
          detected.push(emptyAsset(platform, slotId, 'UNCERTAIN'))
          continue
        }

        const cropped = finalImage.crop(titleBounds).resize({ width: titleBounds.width * 3, height: titleBounds.height * 3 })
        const croppedSize = cropped.getSize()
        const bitmap = cropped.toBitmap()
        const variants: ParsedFields[] = []
        for (const threshold of [undefined, 125, 145, 165]) {
          variants.push(await recognize({
            ...normalizeBitmap(bitmap, croppedSize.width, croppedSize.height, threshold, true),
            purpose: 'ASSET'
          }))
        }

        const votes = new Map<string, number>()
        for (const variant of variants) {
          const text = variant.rawText ?? variant.asset ?? ''
          const candidateSet = new Set<string>()
          for (const line of text.split(/\r?\n/)) {
            const cleaned = chartTitleText(line)
            const asset = normalizeAsset(cleaned)
            if (asset) candidateSet.add(asset)
          }
          if (candidateSet.size === 1) {
            for (const asset of candidateSet) {
              votes.set(asset, (votes.get(asset) ?? 0) + 1)
            }
          }
        }

        const ranked = [...votes.entries()].sort((a, b) => b[1] - a[1])
        const winner = ranked[0]
        const agreed = winner && winner[1] >= 2 && winner[1] > (ranked[1]?.[1] ?? 0)

        if (agreed && winner) {
          const assetName = winner[0]
          const fingerprint = chartTitleFingerprint(normalized, titleBounds)
          const rawOCR = variants.map(v => v.rawText ?? v.asset ?? '')
          const rawOcrConfidence = variants.reduce((n, v) => n + v.confidence, 0) / variants.length
          identified.push({
            slotId,
            assetName,
            source: 'OCR',
            confidence: .96,
            titleFingerprint: fingerprint,
            gridRevision: entry.revision,
            pixelBounds: titleBounds,
            state: 'DETECTED',
            evidenceType: 'CALIBRATED_OCR',
            rawOCR,
            rawOcrConfidence,
            ocrVotes: winner[1]
          })
          detected.push({
            ...emptyAsset(platform, slotId),
            state: 'DETECTED',
            assetName,
            displayName: assetName,
            canonicalAssetId: `${platform}:${assetName}`,
            source: 'OCR',
            confidence: .96,
            evidenceType: 'CALIBRATED_OCR',
            pixelBounds: titleBounds,
            rawOCR,
            rawOcrConfidence,
            identityEvidenceConfidence: .96,
            fingerprintStable: true,
            geometryConsensus: true,
            ocrVotes: winner[1],
            tabIndex: slotId
          })
        } else {
          // Priority 3: UNCERTAIN
          const rawOCR = variants.map(v => v.rawText ?? v.asset ?? '')
          const rawOcrConfidence = variants.reduce((n, v) => n + v.confidence, 0) / variants.length
          detected.push({
            ...emptyAsset(platform, slotId, 'UNCERTAIN'),
            source: 'OCR',
            confidence: 0,
            evidenceType: 'CALIBRATED_OCR',
            pixelBounds: titleBounds,
            rawOCR,
            rawOcrConfidence,
            ocrVotes: winner?.[1] ?? 0,
            tabIndex: slotId
          })
        }
      }

      this.identifiedSlots.set(platform, identified)
    } finally {
      this.assetScans.delete(platform)
    }

    return {
      platform,
      slots: detected,
      durationMs: Date.now() - start,
      overallConfidence: detected.reduce((sum, slot) => sum + slot.confidence, 0) / 9
    }
  }
  async captureSlot(context: ObservationContext): Promise<NormalizedImage> {
    const batch = await this.captureSlots([context]), result = batch.images.get(context.slotId)!
    if (result instanceof Error) throw result
    return result
  }
  async captureSlots(contexts: ObservationContext[]): Promise<{ observedAt: number; images: Map<number, NormalizedImage | Error> }> {
    const platform = contexts[0]?.platform
    if (!platform || contexts.some(c => c.platform !== platform) || contexts.length > 9) throw new Error('Invalid capture batch')
    const surface = this.observationSurface(platform), entry = this.entries.get(platform)
    if (!entry || !surface.available || surface.paused || !surface.gridReady || this.assetScans.has(platform))
      throw new Error('Capture unavailable: verified chart geometry required')
    const signature = JSON.stringify(surface), observedAt = Date.now()
    const full = await entry.view.webContents.capturePage(), size = full.getSize()
    if (full.isEmpty()) throw new Error('Empty capture')
    if (signature !== JSON.stringify(this.observationSurface(platform))) throw new Error('Capture surface changed')
    const normalizedFull = normalizeBitmap(full.toBitmap(), size.width, size.height, undefined, false)
    const grid = entry.snapshot.grid?.source === 'AUTO' ? chartGridResolver(platform).resolve(normalizedFull) : entry.snapshot.grid!
    entry.snapshot.grid = grid
    const images = new Map<number, NormalizedImage | Error>()
    const synchronizedSlots = this.identifiedSlots.get(platform)

    for (const context of contexts) {
      try {
        const canvasSlotId = this.chartSlot(platform, context.slotId, context.assetName)
        const cell = grid.slots.find(s => s.slotId === canvasSlotId)
        if (!cell) throw new Error('CHART_SLOT: chart cell missing in grid.')
        if (grid.source === 'AUTO') {
          const geometry = canvasPriceGeometry(platform, cell.chartBounds, surface.bounds.width, surface.zoomFactor)
          context.bounds = geometry.chartBounds; context.priceBounds = geometry.priceBounds
          if (context.diagnostics) Object.assign(context.diagnostics, { gridConfidence: grid.confidence,
            pricePixelBounds: normalizedToPixel(geometry.priceBounds, surface.bounds.width, surface.bounds.height) })
        }
        const crop = (): NormalizedImage => {
          const identified = synchronizedSlots?.find(s => s.slotId === context.slotId)
          if (!identified || identified.confidence < .95 || normalizeAsset(identified.assetName) !== normalizeAsset(context.assetName)) {
            throw new Error('CHART_SLOT: identity is uncertain. Sync Assets before observing.')
          }
          const cellPixel = normalizedToPixel(cell.chartBounds, size.width, size.height)
          const titleBounds = chartNameBounds(cellPixel)
          const currentFingerprint = chartTitleFingerprint(normalizedFull, titleBounds)
          if (!sameChartTitleFingerprint(currentFingerprint, identified.titleFingerprint)) {
            console.log('[CaptureSlot]', context.platform, JSON.stringify({
              slotId: context.slotId,
              rejection: 'ASSET_IDENTITY_CHANGED',
              reason: 'chart title fingerprint differs'
            }))
            throw new Error('ASSET_IDENTITY_CHANGED — Sync Assets required')
          }
          const bounds = context.priceBounds ?? context.bounds, cellBounds = context.bounds
          if (bounds.x < cellBounds.x || bounds.y < cellBounds.y || bounds.x + bounds.width > cellBounds.x + cellBounds.width + 1e-6 ||
            bounds.y + bounds.height > cellBounds.y + cellBounds.height + 1e-6) throw new Error('PRICE ROI: outside expected chart cell')
          const roi = normalizedToPixel(bounds, surface.bounds.width, surface.bounds.height)
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
        images.set(context.slotId, crop())
      } catch (error) { images.set(context.slotId, error instanceof Error ? error : new Error('Capture failed')) }
    }
    return { observedAt, images }
  }
  async captureAssetLabel(slotId: number, tabs: PixelBounds[], image: Electron.NativeImage,
    recognize: (image: NormalizedImage) => Promise<ParsedFields>
  ): Promise<CapturedTab> {
    const tab = tabs[slotId - 1]
    if (!tab) return { confidence: 1, present: false, tabs }
    const normalized = normalizeBitmap(image.toBitmap(), image.getSize().width, image.getSize().height, undefined, false)
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
    const agreed =
      matches[0]?.length &&
      matches[0].length >= 2 &&
      matches[0].length > (matches[1]?.length ?? 0)
        ? matches[0]
        : null
    const result = agreed
      ? { ...agreed.sort((a, b) => b.confidence - a.confidence)[0]!, confidence: .96 }
      : { ...variants.sort((a, b) => b.confidence - a.confidence)[0]!, confidence: 0 }
    const clipped = !result.asset && variants.some(v => clippedLabelPrefix(v.rawText ?? v.asset ?? '') !== null)
    const confidence = !clipped
      ? result.confidence
      : Math.min(.94, result.confidence)
    const finalResult: CapturedTab = { ...result, tabIndex: slotId, pixelBounds: tab, tabs, rawOCR: variants.map(v => v.rawText ?? v.asset ?? ''),
      nameFingerprint: tabNameFingerprint(normalized, tab), rawOcrConfidence: variants.reduce((n, v) => n + v.confidence, 0) / variants.length,
      ocrVotes: agreed?.length ?? 0, confidence, present: true }
    if (!finalResult.confidence) delete finalResult.asset
    return finalResult
  }
  /**
   * The visible surface, for callers that measure geometry the capture pipeline does not own.
   * Returns the native image alongside the surface it belongs to so a caller can convert between
   * capture pixels and the browser's own device-independent pixels without guessing a scale.
   */
  async captureSurface(platform: Platform): Promise<{ image: Electron.NativeImage
    size: { width: number; height: number }; surface: ReturnType<PlatformBrowserManager['observationSurface']> }> {
    const entry = this.entries.get(platform), surface = this.observationSurface(platform)
    if (!entry || !surface.available || surface.paused) throw new Error('Surface capture unavailable')
    const image = await entry.view.webContents.capturePage()
    if (image.isEmpty()) throw new Error('Empty surface capture')
    return { image, size: image.getSize(), surface }
  }
  /**
   * Send one click into the platform page at a normalized point on its own surface.
   *
   * This is the only method in the application that produces input for a broker. It refuses
   * unless the surface it is about to press is the same surface the caller measured: same
   * revision, same zoom, still visible, still showing a verified grid. A stale coordinate is a
   * click somewhere the caller never looked.
   */
  async pressPoint(platform: Platform, point: { x: number; y: number },
    guard: { revision: number; zoomFactor: number }): Promise<{ pressedAt: number; devicePoint: { x: number; y: number } }> {
    const entry = this.entries.get(platform), surface = this.observationSurface(platform)
    if (!entry || entry.view.webContents.isDestroyed()) throw new Error('PRESS_UNAVAILABLE: workspace is not open')
    if (!surface.available || surface.paused) throw new Error('PRESS_UNAVAILABLE: surface is hidden, unloaded or calibrating')
    if (!surface.gridReady) throw new Error('PRESS_UNAVAILABLE: verified chart geometry required')
    if (surface.revision !== guard.revision) throw new Error('PRESS_STALE: the surface changed after the controls were measured')
    if (Math.abs(surface.zoomFactor - guard.zoomFactor) > .001) throw new Error('PRESS_STALE: browser zoom changed after the controls were measured')
    if (!(point.x > 0 && point.x < 1 && point.y > 0 && point.y < 1)) throw new Error('PRESS_STALE: point is outside the surface')
    // View-relative device-independent pixels: the same space setBounds uses, so page zoom is
    // applied by Chromium rather than by this arithmetic.
    const x = Math.round(point.x * surface.bounds.width), y = Math.round(point.y * surface.bounds.height)
    const contents = entry.view.webContents
    const base = { x, y, button: 'left', clickCount: 1 } as const
    contents.sendInputEvent({ ...base, type: 'mouseMove', clickCount: 0 })
    const pressedAt = Date.now()
    contents.sendInputEvent({ ...base, type: 'mouseDown' })
    await new Promise(resolve => setTimeout(resolve, 40))
    if (this.observationSurface(platform).revision !== guard.revision) throw new Error('PRESS_STALE: the surface changed mid-press')
    contents.sendInputEvent({ ...base, type: 'mouseUp' })
    return { pressedAt, devicePoint: { x, y } }
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
      case 'closePortfolio':
        void this.closePortfolioPanel(request.platform).catch(() => {})
        break
    }
    return entry.snapshot
  }
  snapshot(platform: Platform): BrowserSnapshot {
    const entry = this.entries.get(platform)
    if (!entry) throw new Error('Workspace is not open')
    return entry.snapshot
  }
}
