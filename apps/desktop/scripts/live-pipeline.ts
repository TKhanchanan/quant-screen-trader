/**
 * Live end-to-end acceptance. Drives exactly what the workspace buttons drive — verify geometry,
 * Sync Assets, Start Observation — against the real authenticated sessions, then reports what the
 * engine built from the resulting samples. Read-only: capture, visible DOM and OCR only.
 * Run through scripts/live-acceptance.mjs.
 */
import { app, BrowserWindow, WebContentsView } from 'electron'
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { PlatformSchema, type Platform } from '@quant-screen-trader/shared-types'
import { getEngineConnectionConfig } from '../electron/main/engine-config'
import { requestConfiguration } from '../electron/main/configuration-client'
import { prepareCalibration } from '../electron/main/calibration'
import { PlatformBrowserManager } from '../electron/main/platform-browser'
import { AssetSyncManager } from '../electron/main/asset-sync'
import { MarketManager } from '../electron/main/market-manager'

const platforms = (process.env.QST_LIVE_PLATFORM ?? 'iqoption').split(',').map(value => PlatformSchema.parse(value.trim()))
const out = process.env.QST_LIVE_OUT!
const userData = process.env.QST_LIVE_USER_DATA!
const settleMs = Number(process.env.QST_LIVE_SETTLE_MS ?? 120000)
const observeMs = Number(process.env.QST_LIVE_OBSERVE_MS ?? 90000)
const intervalMs = Number(process.env.QST_LIVE_INTERVAL_MS ?? 1000)
const width = Number(process.env.QST_LIVE_WIDTH ?? 1440), height = Number(process.env.QST_LIVE_HEIGHT ?? 940)
const resizeWidth = Number(process.env.QST_LIVE_RESIZE_WIDTH ?? 0), resizeHeight = Number(process.env.QST_LIVE_RESIZE_HEIGHT ?? 0)
if (!out || !userData) throw new Error('Run through scripts/live-acceptance.mjs')
app.setPath('userData', userData)
app.setPath('sessionData', userData)
mkdirSync(out, { recursive: true })

const wait = (ms: number): Promise<void> => new Promise(resolve => setTimeout(resolve, ms))
const log = (message: string): void => console.log(`[live] ${message}`)

/** Read a local engine endpoint. Read-only, and a failure is reported rather than invented. */
async function readEngine(base: string, path: string): Promise<Record<string, unknown> | null> {
  try {
    const response = await fetch(new URL(path, base), { signal: AbortSignal.timeout(4000), redirect: 'error' })
    return response.ok ? (await response.json() as Record<string, unknown>) : null
  } catch { return null }
}
const browsers = new PlatformBrowserManager(() => new WebContentsView())
const report: Record<string, unknown> = {}

void app.whenReady().then(async () => {
  const connection = getEngineConnectionConfig()
  const market = new MarketManager(browsers, connection)
  const assetSync = new AssetSyncManager(browsers, async (platform, before, slots) => {
    const profile = before.calibrations.find(p => p.id === before.activeCalibrationId)
    return requestConfiguration(connection, { operation: 'syncAssets', platform, slots,
      expectedSlots: before.configuration.slots, expectedCalibrationVersion: profile ? `${profile.id}:${profile.updatedAt}` : null })
  }, result => market.configure(result))
  const prepare = async (platform: Platform): Promise<void> => {
    const result = await prepareCalibration(platform, browsers, request => requestConfiguration(connection, request))
    market.configure(result); assetSync.configure(result)
  }
  const windows = new Map<Platform, BrowserWindow>()
  const observing: Platform[] = []
  try {
    for (const [index, platform] of platforms.entries()) {
      const window = new BrowserWindow({ width, height, show: true, x: 40 + index * 60, y: 40 + index * 60,
        title: `Live acceptance — ${platform}`, backgroundColor: '#080d18' })
      windows.set(platform, window)
      browsers.attach(platform, window)
      const [contentWidth = width, contentHeight = height] = window.getContentSize()
      browsers.command({ platform, operation: 'layout', bounds: { x: 0, y: 0, width: contentWidth, height: contentHeight }, visible: true })
    }
    for (const platform of platforms) {
      // The traderoom paints its canvas well after the document loads. Retry exactly what the
      // workspace retries — verify the geometry — until it succeeds or the deadline passes.
      log(`${platform}: waiting up to ${settleMs} ms for the live canvas grid`)
      const platformReport: Record<string, unknown> = {}
      report[platform] = platformReport
      const deadline = Date.now() + settleMs
      let ready = false
      while (!ready) {
        await wait(5000)
        platformReport.zoomFactor = browsers.observationSurface(platform).zoomFactor
        try { await prepare(platform); ready = true }
        catch (error) {
          platformReport.gridError = error instanceof Error ? error.message : 'geometry failed'
          if (Date.now() >= deadline) break
        }
      }
      if (!ready) { log(`${platform}: GEOMETRY FAILED ${String(platformReport.gridError)}`); continue }
      delete platformReport.gridError
      const grid = browsers.command({ platform, operation: 'state' }).grid
      platformReport.grid = grid ? { bounds: grid.bounds, source: grid.source, confidence: grid.confidence } : null
      log(`${platform}: grid ${JSON.stringify(platformReport.grid)}`)
      const state = await assetSync.command({ platform, operation: 'sync' })
      platformReport.syncError = state.error
      platformReport.tabs = state.detection?.slots.map(slot => ({ tab: slot.tabIndex ?? slot.slotId, state: slot.state,
        asset: slot.assetName, confidence: Number(slot.confidence.toFixed(3)), pixelBounds: slot.pixelBounds }))
      for (const slot of state.detection?.slots ?? [])
        log(`${platform}: tab ${slot.tabIndex ?? slot.slotId} -> ${slot.assetName ?? slot.state} (${slot.confidence.toFixed(2)})`)
      market.command({ platform, operation: 'start', intervalMs })
      observing.push(platform)
    }
    if (!observing.length) throw new Error('No platform reached verified geometry')
    const finish = Date.now() + observeMs
    while (Date.now() < finish) {
      await wait(10000)
      for (const platform of observing) {
        const snapshot = market.command({ platform, operation: 'state' })
        log(`${platform}: ${Math.round((finish - Date.now()) / 1000)}s left · ${snapshot.captureRate.toFixed(1)} obs/s · engine ${snapshot.engineAvailable ? 'receiving' : 'waiting'} · ` +
          snapshot.slots.map(slot => `${slot.slotId}:${slot.state[0]}${slot.secondSamples}`).join(' '))
      }
    }
    // PART J: resize the workspace without touching zoom, then confirm the geometry and the
    // tab -> cell -> price mapping are recomputed rather than carried over from the old size.
    if (resizeWidth && resizeHeight) {
      for (const platform of observing) {
        const before = market.command({ platform, operation: 'state' })
        const platformReport = report[platform] as Record<string, unknown>
        platformReport.beforeResize = { bounds: browsers.observationSurface(platform).bounds,
          slots: before.slots.map(slot => ({ slotId: slot.slotId, asset: slot.observation?.assetName ?? null,
            canvasSlotId: slot.diagnostics?.canvasSlotId ?? null, chartPixelBounds: slot.pixelBounds })) }
        windows.get(platform)!.setContentSize(resizeWidth, resizeHeight)
        const [w = resizeWidth, h = resizeHeight] = windows.get(platform)!.getContentSize()
        browsers.command({ platform, operation: 'layout', bounds: { x: 0, y: 0, width: w, height: h }, visible: true })
        log(`${platform}: resized to ${w}x${h}; zoom now ${browsers.observationSurface(platform).zoomFactor}`)
      }
      await wait(20000)
      for (const platform of observing) {
        const platformReport = report[platform] as Record<string, unknown>
        try {
          await prepare(platform)
          const resync = await assetSync.command({ platform, operation: 'sync' })
          market.command({ platform, operation: 'start', intervalMs })
          const grid = browsers.command({ platform, operation: 'state' }).grid
          platformReport.resizedGrid = grid ? { bounds: grid.bounds, source: grid.source, confidence: grid.confidence } : null
          platformReport.resizedSyncError = resync.error
          platformReport.resizedTabs = resync.detection?.slots.map(slot => ({ tab: slot.tabIndex ?? slot.slotId,
            state: slot.state, asset: slot.assetName, confidence: Number(slot.confidence.toFixed(3)) })) ?? null
          log(`${platform}: resized grid ${JSON.stringify(platformReport.resizedGrid)}`)
          log(`${platform}: resized sync error=${resync.error ?? 'none'} tabs=${JSON.stringify(platformReport.resizedTabs)}`)
        } catch (error) {
          platformReport.resizeError = error instanceof Error ? error.message : 'resize recovery failed'
          log(`${platform}: RESIZE FAILED ${String(platformReport.resizeError)}`)
        }
      }
      await wait(Math.max(30000, observeMs / 3))
    }
    // Phase 6 and Phase 7 acceptance. Every closed PRIMARY feature snapshot — S5 on
    // CapitalBear, M1 on IQ Option — must have produced exactly one regime, one evaluation
    // per strategy and one ensemble. WARMING and SKIP are acceptable outcomes; a missing
    // ensemble is not, and nothing here fabricates a direction to make the run look better.
    const features = await readEngine(connection.healthUrl, '/api/features/state')
    const strategy = await readEngine(connection.healthUrl, '/api/strategy/state')
    report.featureState = features
    report.strategyState = strategy
    log(`engine: features ${features ? 'reachable' : 'UNREACHABLE'} · strategy ${strategy ? 'reachable' : 'UNREACHABLE'}`)
    if (strategy) log(`engine: ${String(strategy.featureVersion)} / ${String(strategy.regimeVersion)} / ${String(strategy.strategyVersion)} · evaluated ${String(strategy.evaluated)} · duplicates ${String(strategy.duplicates)}`)
    for (const row of (strategy?.slots as Record<string, unknown>[] | undefined) ?? []) {
      const votes = (row.votes as Record<string, unknown>[]).map(v => `${String(v.strategyId).replace('_v1', '')}:${String(v.direction)}`).join(' ')
      log(`${String(row.platform)}: slot ${String(row.slotId)} ${String(row.assetName)} · regime ${String(row.primaryRegime)} ` +
        `${Math.round(Number(row.regimeConfidence) * 100)}% · ensemble ${String(row.direction)} ${Math.round(Number(row.confidence) * 100)}% ` +
        `· eligible ${String(row.eligibleStrategies)}/${String(row.evaluations)} · votes ${votes} · vetoes ${(row.vetoes as string[]).join(',') || 'none'}`)
    }

    // Phase 8 acceptance. Each platform ranks its own same-time cohort on its own primary
    // horizon — S5 on CapitalBear, M1 on IQ Option — and the two boards stay separate. A
    // COLLECTING, PARTIAL or NO_OPPORTUNITY board is a valid outcome; nothing here invents a
    // top candidate, and no broker control is touched to produce one.
    const opportunities = await readEngine(connection.healthUrl, '/api/opportunities/state')
    report.opportunityState = opportunities
    log(`engine: opportunities ${opportunities ? 'reachable' : 'UNREACHABLE'}`)
    if (opportunities) log(`engine: ranking ${String(opportunities.rankingVersion)} · ingested ${String(opportunities.ingested)} ` +
      `· duplicates ${String(opportunities.duplicates)} · outOfOrder ${String(opportunities.outOfOrder)} ` +
      `· stale ${String(opportunities.staleForEpoch)} · boards ${String(opportunities.finalized)}`)
    for (const platform of observing) {
      const payload = await readEngine(connection.healthUrl, `/api/opportunities/${platform}`)
      const platformReport = report[platform] as Record<string, unknown> | undefined
      if (platformReport) platformReport.opportunityBoard = payload
      const board = payload?.board as Record<string, unknown> | undefined
      if (!board) { log(`${platform}: no opportunity board yet`); continue }
      log(`${platform}: board asOf ${String(board.asOf)} (${String(board.primaryTimeframe)}) · ${String(board.status)} ` +
        `· expected ${String(board.expectedSlots)} received ${String(board.receivedSlots)} ranked ${String(board.rankedSlots)} ` +
        `excluded ${String(board.excludedSlots)} · missing ${(board.missingSlots as number[]).join(',') || 'none'} ` +
        `· reasons ${(board.reasons as string[]).join(',') || 'none'}`)
      log(`${platform}: selected ${board.selectedSlotId === null ? 'NONE' : `slot ${String(board.selectedSlotId)} ${String(board.selectedAssetName)} ${String(board.selectedDirection)} score ${String(board.selectedScore)}`} ` +
        `· lead ${board.leadMargin === null ? '—' : String(board.leadMargin)}`)
      for (const entry of (board.watchlist as Record<string, unknown>[]))
        log(`${platform}: #${String(entry.rank)} slot ${String(entry.slotId)} ${String(entry.assetName)} ${String(entry.direction)} ` +
          `· score ${Number(entry.rankScore).toFixed(3)} · ensemble ${Number(entry.ensembleConfidence).toFixed(3)} · ${String(entry.regime)} · ${String(entry.candidateStatus)}`)
      for (const candidate of (board.candidates as Record<string, unknown>[]))
        log(`${platform}: candidate slot ${String(candidate.slotId)} ${String(candidate.assetName)} ${String(candidate.direction)} ` +
          `rank=${String(candidate.rank ?? '—')} score=${Number(candidate.rankScore).toFixed(3)} status=${String(candidate.candidateStatus)} ` +
          `p3=${Number(candidate.directionPersistence3).toFixed(2)} med3=${Number(candidate.confidenceMedian3).toFixed(2)} ` +
          `quality=${String(candidate.analysisStatus)} excl=${(candidate.exclusionReasons as string[]).join(',') || 'none'}`)
    }

    for (const platform of observing) {
      const snapshot = market.command({ platform, operation: 'state' })
      const platformReport = report[platform] as Record<string, unknown> | undefined
      if (platformReport) platformReport.market = {
        captureRate: snapshot.captureRate, engineAvailable: snapshot.engineAvailable, dropped: snapshot.dropped,
        slots: snapshot.slots.map(slot => ({ slotId: slot.slotId, state: slot.state,
          asset: slot.observation?.assetName ?? null, price: slot.observation?.price ?? null,
          quality: slot.observation?.dataQuality.state ?? null, source: slot.observation?.sourceType ?? null,
          secondSamples: slot.secondSamples, s5Samples: slot.s5Samples ?? 0, s5State: slot.s5State ?? null,
          m1Samples: slot.m1Samples, m1State: slot.m1State, contextId: slot.observation?.contextId ?? null,
          chartPixelBounds: slot.pixelBounds, diagnostics: slot.diagnostics })) }
      for (const slot of snapshot.slots)
        log(`${platform}: slot ${slot.slotId} ${slot.state} ${slot.observation?.assetName ?? '—'} ` +
          `price=${String(slot.observation?.price ?? '—')} 1s=${slot.secondSamples} S5=${slot.s5Samples ?? 0}/${slot.s5State ?? '—'} ` +
          `M1=${slot.m1Samples}/${slot.m1State ?? '—'} ${slot.diagnostics?.stage ?? ''} ${slot.diagnostics?.message ?? ''}`)
      market.command({ platform, operation: 'stop' })
    }
  } catch (error) {
    report.fatal = error instanceof Error ? error.message : String(error)
    log(`FATAL: ${report.fatal as string}`)
  } finally {
    writeFileSync(join(out, 'pipeline.json'), JSON.stringify(report, null, 2))
    assetSync.stop(); market.stop()
    for (const window of windows.values()) if (!window.isDestroyed()) window.destroy()
    app.quit()
  }
})
