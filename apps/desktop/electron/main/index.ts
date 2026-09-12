import { existsSync, realpathSync } from 'node:fs'
import { join, resolve, relative, isAbsolute, sep } from 'node:path'
import { app, BrowserWindow, ipcMain, WebContentsView, type IpcMainInvokeEvent } from 'electron'
import {
  IPC_CHANNELS, MarketCommandSchema, AssetSyncCommandSchema,
  PlatformSchema,
  PlatformCommandSchema, ConfigurationRequestSchema, PLATFORM_DETAILS, FeatureEngineStateSchema,
  StrategyEngineStateSchema, OpportunityResponseSchema, ExecutionCommandSchema,
  PaperTradeSchema, PaperStatsSchema, PaperEngineStateSchema,
  SessionGuardCommandSchema, SessionGuardSettingsSchema, DailySessionSchema,
  DailySessionSummarySchema, SessionNotificationSchema, SessionBlockReasonSchema,
  defaultSessionGuardSettings, emptyAnalyticsState,
  AnalyticsQualitySchema, OutcomeMetricsSchema, MoneyMetricsSchema, CalibrationSchema,
  SegmentMetricsSchema, MatrixSchema, ThresholdCandidateSchema, TemporalSplitSchema,
  SampleLabelSchema,
  ReplayCommandSchema, ReplayStatusSchema, ReplaySummarySchema, emptyReplayState,
  type Platform, type PaperState, type SessionGuardState, type AnalyticsState,
  type SegmentMetrics, type ReplayState
} from '@quant-screen-trader/shared-types'
import { getEngineConnectionConfig } from './engine-config'
import { EngineProcessManager } from './engine-process'
import { fetchEngineHealth } from './health-client'
import { PlatformWindowRegistry } from './window-registry'
import { PlatformBrowserManager } from './platform-browser'
import { AssetSyncManager } from './asset-sync'
import { MarketManager } from './market-manager'
import { OrderExecutor } from './order-executor'
import { ExecutionManager } from './execution-manager'
import { requestConfiguration } from './configuration-client'
import { SessionWatcher } from './session-watcher'
import { requireScope, type RendererScope } from './ipc-scope'
import { prepareCalibration } from './calibration'

const trustedRenderers = new Map<number, RendererScope>()
function authorize(event: IpcMainInvokeEvent, platform?: Platform): RendererScope {
  return requireScope(trustedRenderers.get(event.sender.id), event.senderFrame === event.sender.mainFrame, platform)
}
/**
 * The replay run this window is following. A replay is offline compute over a recorded file, so
 * this is a view handle and nothing else: it can be lost, reacquired from the engine's own
 * listing, and it authorises nothing.
 */
let activeReplayJob: string | null = null
const browsers = new PlatformBrowserManager((platform) => {
  const view = new WebContentsView({ webPreferences: { preload: join(__dirname, '../preload/index.cjs'),
    sandbox: true, contextIsolation: true, nodeIntegration: false, webSecurity: true } })
  view.setBackgroundColor('#00000000')
  trustedRenderers.set(view.webContents.id, { platform, overlay: true })
  const id = view.webContents.id
  view.webContents.once('destroyed', () => trustedRenderers.delete(id))
  view.webContents.on('will-navigate', (event) => event.preventDefault())
  view.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  const location = rendererLocation()
  const query = { view: 'calibration-overlay', platform }
  const load = location.devServerUrl
    ? view.webContents.loadURL(`${location.devServerUrl}?${new URLSearchParams(query)}`)
    : view.webContents.loadFile(location.file, { query })
  void load.catch(() => console.warn('Calibration overlay failed to load.'))
  return view
})

let dashboardWindow: BrowserWindow | null = null
let engineProcess: EngineProcessManager | null = null
let market: MarketManager | null = null
let assetSync: AssetSyncManager | null = null
let execution: ExecutionManager | null = null
let sessionWatcher: SessionWatcher | null = null

function rendererLocation(): { devServerUrl?: string; file: string } {
  const devServerUrl = process.env.ELECTRON_RENDERER_URL
  return {
    ...(devServerUrl ? { devServerUrl } : {}),
    file: join(__dirname, '../renderer/index.html')
  }
}

function createWindow(
  title: string,
  query: Record<string, string>,
  dimensions: { width: number; height: number },
  partition?: string
): BrowserWindow {
  const window = new BrowserWindow({
    ...dimensions,
    minWidth: 760,
    minHeight: 560,
    show: false,
    title,
    backgroundColor: '#080d18',
    webPreferences: {
      preload: join(__dirname, '../preload/index.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      ...(partition ? { partition } : {})
    }
  })

  window.once('ready-to-show', () => window.show())
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  window.webContents.on('will-navigate', (event) => event.preventDefault())
  const id = window.webContents.id
  const platform = PlatformSchema.safeParse(query.platform)
  trustedRenderers.set(id, { overlay: false, ...(platform.success ? { platform: platform.data } : {}) })
  window.webContents.once('destroyed', () => trustedRenderers.delete(id))

  const location = rendererLocation()
  const devServerUrl = location.devServerUrl
  const load = devServerUrl
    ? (() => {
        const url = new URL(devServerUrl)
        for (const [key, value] of Object.entries(query)) url.searchParams.set(key, value)
        return window.loadURL(url.toString())
      })()
    : window.loadFile(location.file, { query })

  void load.catch((error: unknown) => {
    console.error(`Failed to load ${title}:`, error)
    if (!window.isDestroyed()) window.close()
  })

  return window
}

function createWorkspaceWindow(platform: Platform): BrowserWindow {
  const displayName = PLATFORM_DETAILS[platform].name
  const window = createWindow(
    `${displayName} Workspace — QuantScreen Trader`,
    { view: 'workspace', platform },
    { width: 1320, height: 900 }
  )
  browsers.attach(platform, window)
  return window
}

const workspaceWindows = new PlatformWindowRegistry<BrowserWindow>(createWorkspaceWindow)

/**
 * The board and execution controls live in their own window. Kept out of the workspace because
 * every pixel they take there is a pixel the broker's nine charts do not get, and the grid
 * detector needs those charts at a readable size.
 */
function createTradingWindow(platform: Platform): BrowserWindow {
  return createWindow(`${PLATFORM_DETAILS[platform].name} Trading — QuantScreen Trader`,
    { view: 'trading', platform }, { width: 720, height: 840 })
}
const tradingWindows = new PlatformWindowRegistry<BrowserWindow>(createTradingWindow)

function openDashboard(): BrowserWindow {
  if (dashboardWindow && !dashboardWindow.isDestroyed()) {
    dashboardWindow.focus()
    return dashboardWindow
  }

  const window = createWindow(
    'QuantScreen Trader',
    { view: 'dashboard' },
    { width: 1120, height: 760 }
  )
  dashboardWindow = window
  window.once('closed', () => {
    dashboardWindow = null
  })
  return window
}

function configureEnvironment(): void {
  try {
    if (!app.isPackaged && typeof process.loadEnvFile === 'function') {
      const envFile = resolve(app.getAppPath(), '..', '..', '.env')
      if (existsSync(envFile)) process.loadEnvFile(envFile)
    }
  } catch {
    console.warn('Local environment overrides could not be loaded; using safe defaults.')
  }
  if (!app.isPackaged && process.env.QST_DATA_DIR?.trim()) {
    // Explicit development data roots must exist and remain outside the checkout,
    // including when symlinks are used. Set before Chromium creates any sessions.
    const directory = realpathSync(process.env.QST_DATA_DIR.trim())
    const repository = realpathSync(resolve(app.getAppPath(), '..', '..'))
    const pathFromRepository = relative(repository, directory)
    if (!pathFromRepository || (!(pathFromRepository === '..' || pathFromRepository.startsWith(`..${sep}`)) && !isAbsolute(pathFromRepository)))
      throw new Error('QST_DATA_DIR must be outside the repository')
    app.setPath('userData', directory)
    app.setPath('sessionData', directory)
  }
}
configureEnvironment()

void app.whenReady().then(() => {

  const connection = getEngineConnectionConfig()
  engineProcess = new EngineProcessManager({
    appPath: app.getAppPath(),
    connection,
    dataDirectory: app.getPath('userData'),
    isPackaged: app.isPackaged,
    resourcesPath: process.resourcesPath
  })
  engineProcess.start()
  market = new MarketManager(browsers, connection)
  execution = new ExecutionManager(browsers, new OrderExecutor(browsers), connection)
  const prepare = async (platform: Platform): Promise<void> => {
    const result = await prepareCalibration(platform, browsers, request => requestConfiguration(connection, request))
    market!.configure(result)
    assetSync!.configure(result)
    execution!.configure(result)
  }
  assetSync = new AssetSyncManager(browsers, async (platform, before, slots) => {
    const profile = before.calibrations.find(p => p.id === before.activeCalibrationId)
    return requestConfiguration(connection, { operation: 'syncAssets', platform, slots,
      expectedSlots: before.configuration.slots, expectedCalibrationVersion: profile ? `${profile.id}:${profile.updatedAt}` : null })
  }, result => { market!.configure(result); execution!.configure(result) })
  ipcMain.handle(IPC_CHANNELS.assetSync, async (event, input: unknown) => {
    const command = AssetSyncCommandSchema.parse(input)
    if (authorize(event, command.platform).overlay) throw new Error('Overlay cannot sync assets')
    // Geometry first: a tab whose name the broker had to clip is completed from the chart cell it
    // addresses, which only exists once the canvas grid for this browser state has been verified.
    let geometry: string | null = null
    if (command.operation === 'sync') {
      try { await prepare(command.platform) }
      catch (error) { geometry = error instanceof Error ? error.message : 'Use Calibrate Chart Area.' }
    }
    const state = await assetSync!.command(command)
    if (geometry) state.error = geometry
    return state
  })
  ipcMain.handle(IPC_CHANNELS.market, async (event, input: unknown) => {
    const command = MarketCommandSchema.parse(input)
    if (authorize(event, command.platform).overlay) throw new Error('Overlay cannot observe')
    if (command.operation === 'start') await prepare(command.platform)
    return market!.command(command)
  })

  ipcMain.handle(IPC_CHANNELS.features, async (event, input: unknown) => {
    // Read-only Phase 6 diagnostics. Unavailable features are reported, never invented.
    const platform = PlatformSchema.parse(input)
    authorize(event, platform)
    try {
      const response = await fetch(new URL('/api/features/state', connection.healthUrl),
        { signal: AbortSignal.timeout(2000), redirect: 'error' })
      if (!response.ok) throw new Error('Feature state unavailable')
      const value = FeatureEngineStateSchema.parse(await response.json())
      return { featureVersion: value.featureVersion, available: true,
        slots: value.slots.filter(s => s.platform === platform) }
    } catch {
      return { featureVersion: 'unknown', available: false, slots: [] }
    }
  })
  ipcMain.handle(IPC_CHANNELS.strategy, async (event, input: unknown) => {
    // Read-only Phase 7 diagnostics. The desktop renders opinions; it never places anything.
    const platform = PlatformSchema.parse(input)
    authorize(event, platform)
    try {
      const response = await fetch(new URL('/api/strategy/state', connection.healthUrl),
        { signal: AbortSignal.timeout(2000), redirect: 'error' })
      if (!response.ok) throw new Error('Strategy state unavailable')
      const value = StrategyEngineStateSchema.parse(await response.json())
      return { featureVersion: value.featureVersion, regimeVersion: value.regimeVersion,
        strategyVersion: value.strategyVersion, available: true,
        slots: value.slots.filter(s => s.platform === platform) }
    } catch {
      return { featureVersion: 'unknown', regimeVersion: 'unknown', strategyVersion: 'unknown',
        available: false, slots: [] }
    }
  })
  ipcMain.handle(IPC_CHANNELS.opportunities, async (event, input: unknown) => {
    // Read-only Phase 8 diagnostics. The desktop renders a ranking the engine produced; it
    // never scores, orders or selects anything, and a top candidate is analysis, not a trade.
    const platform = PlatformSchema.parse(input)
    authorize(event, platform)
    try {
      const response = await fetch(new URL(`/api/opportunities/${platform}`, connection.healthUrl),
        { signal: AbortSignal.timeout(2000), redirect: 'error' })
      // 404 is the honest answer while a platform has produced no cohort yet, and 429 means
      // the engine is mid-ingest. Neither is an invented board.
      if (!response.ok) throw new Error('Opportunity board unavailable')
      const value = OpportunityResponseSchema.parse(await response.json())
      return { rankingVersion: value.rankingVersion, available: true, board: value.board }
    } catch {
      return { rankingVersion: 'unknown', available: false, board: null }
    }
  })
  ipcMain.handle(IPC_CHANNELS.paper, async (event, input: unknown) => {
    // Read-only Phase 9 diagnostics. The desktop renders outcomes the engine resolved from
    // canonical prices; it never enters, prices or settles anything, and a paper WIN is a
    // measurement of the market, never a broker order.
    const platform = PlatformSchema.parse(input)
    authorize(event, platform)
    const offline: PaperState = { paperVersion: 'unknown', available: false, enabled: false,
      accountingConfigured: false, open: [], recent: [], stats: null }
    const read = async (path: string): Promise<unknown> => {
      const response = await fetch(new URL(path, connection.healthUrl),
        { signal: AbortSignal.timeout(2000), redirect: 'error' })
      // 429 means the engine is mid-ingest and 503 that storage is unavailable. Neither is an
      // invented outcome, so the panel reports the gap rather than showing stale certainty.
      if (!response.ok) throw new Error('Paper state unavailable')
      return response.json()
    }
    try {
      const [state, open, history, stats] = await Promise.all([
        read('/api/paper/state'),
        read('/api/paper/open'),
        read(`/api/paper/history?platform=${platform}&limit=8`),
        read(`/api/paper/stats?platform=${platform}`)
      ])
      const engine = PaperEngineStateSchema.parse(state)
      const trades = (value: unknown): PaperState['open'] =>
        PaperTradeSchema.array().parse((value as { trades?: unknown }).trades ?? [])
          .filter(trade => trade.platform === platform)
      return {
        paperVersion: engine.paperVersion, available: true, enabled: engine.enabled,
        accountingConfigured: engine.accountingConfigured,
        open: trades(open).slice(0, 6), recent: trades(history).slice(0, 20),
        stats: PaperStatsSchema.parse((stats as { stats: unknown }).stats)
      } satisfies PaperState
    } catch {
      return offline
    }
  })
  ipcMain.handle(IPC_CHANNELS.sessionGuard, async (event, input: unknown) => {
    // Phase 9.5 daily accounting. Reading is a read; the two writes are the operator's own
    // limits and ending the trading day. Nothing here starts anything, and the engine's own
    // local-only boundary is what actually enforces that.
    const command = SessionGuardCommandSchema.parse(input)
    authorize(event)
    const offline: SessionGuardState = { sessionGuardVersion: 'unknown', available: false,
      enabled: false, canOpenNewEntry: true, blockReason: null, shutdownRequested: false,
      paperAccountingConfigured: false, settingsError: null, session: null,
      settings: defaultSessionGuardSettings(), targetProgress: null, lossProgress: null,
      remainingToTarget: null, nextResetAt: null, openTrades: 0, notifications: [], history: [] }
    const read = async (path: string): Promise<Record<string, unknown>> => {
      const response = await fetch(new URL(path, connection.healthUrl),
        { signal: AbortSignal.timeout(2000), redirect: 'error' })
      if (!response.ok) throw new Error('Session guard unavailable')
      return await response.json() as Record<string, unknown>
    }
    try {
      if (command.operation !== 'state') {
        const body = command.operation === 'settings'
          ? { operation: 'settings', ...command.settings } : { operation: 'stop' }
        const response = await fetch(new URL('/api/session-guard/command', connection.healthUrl),
          { method: 'POST', headers: { 'content-type': 'application/json' },
            body: JSON.stringify(body), signal: AbortSignal.timeout(4000), redirect: 'error' })
        if (!response.ok) throw new Error(response.status === 422
          ? 'ค่าที่กรอกไม่ถูกต้อง' : 'ตั้งค่ารอบวันไม่สำเร็จ')
      }
      const [state, settings, history] = await Promise.all([
        read('/api/session-guard/state'),
        read('/api/session-guard/settings'),
        read('/api/session-guard/history?limit=30')
      ])
      const session = state.session === null ? null : DailySessionSchema.parse(state.session)
      return {
        sessionGuardVersion: String(state.sessionGuardVersion), available: true,
        enabled: Boolean(state.enabled), canOpenNewEntry: Boolean(state.canOpenNewEntry),
        blockReason: state.blockReason === null ? null
          : SessionBlockReasonSchema.parse(state.blockReason),
        shutdownRequested: Boolean(state.shutdownRequested),
        paperAccountingConfigured: Boolean(state.paperAccountingConfigured),
        settingsError: (settings.settingsError as string | null) ?? null,
        session, settings: SessionGuardSettingsSchema.parse(settings.settings),
        targetProgress: (state.targetProgress as number | null) ?? null,
        lossProgress: (state.lossProgress as number | null) ?? null,
        remainingToTarget: (state.remainingToTarget as number | null) ?? null,
        nextResetAt: (state.nextResetAt as number | null) ?? null,
        openTrades: Number(state.openTrades ?? 0),
        notifications: SessionNotificationSchema.array().parse(state.notifications ?? []).slice(0, 16),
        history: DailySessionSummarySchema.array().parse(history.sessions ?? []).slice(0, 30)
      } satisfies SessionGuardState
    } catch (error) {
      // A stop the operator asked for must not fail silently; a poll that cannot reach the
      // engine simply reports that, without inventing a permission either way.
      if (command.operation !== 'state') throw error
      return offline
    }
  })
  ipcMain.handle(IPC_CHANNELS.analytics, async (event, input: unknown) => {
    // Read-only Phase 10 research. The desktop renders an analysis the engine computed from its
    // own durable record; it fits nothing, and there is deliberately no command channel here —
    // a threshold this returns describes outcomes that already happened and cannot be applied.
    const platform = PlatformSchema.parse(input)
    authorize(event, platform)
    const offline = emptyAnalyticsState(platform)
    try {
      // One read of the whole snapshot rather than nine of its sections: the sections would be
      // assembled from nine separate analyses, and a panel whose regime table came from a
      // different dataset than its win rate would be quietly inconsistent.
      const response = await fetch(
        new URL(`/api/analytics/snapshot?platform=${platform}`, connection.healthUrl),
        // Longer than the other readers on purpose. The first read after a restart rebuilds the
        // analysis from the whole Parquet history; a 429 on the next poll is the retry path.
        { signal: AbortSignal.timeout(20_000), redirect: 'error' })
      if (response.status === 429) return { ...offline, busy: true } satisfies AnalyticsState
      if (!response.ok) throw new Error('Analytics unavailable')
      const body = await response.json() as Record<string, unknown>
      const snapshot = body.snapshot as Record<string, unknown>
      const segments = (value: unknown, limit: number): SegmentMetrics[] =>
        SegmentMetricsSchema.array().parse(value ?? []).slice(0, limit)
      return {
        analyticsVersion: String(snapshot.analyticsVersion), available: true, busy: false,
        platform, sampleCount: Number(snapshot.totalResolved ?? 0),
        sampleLabel: SampleLabelSchema.parse(body.sampleLabel ?? 'INSUFFICIENT_SAMPLE'),
        timezone: String(snapshot.timezone ?? 'Asia/Bangkok'),
        stale: Boolean(body.stale), pendingOutcomes: Number(body.pendingOutcomes ?? 0),
        quality: AnalyticsQualitySchema.parse(snapshot.quality),
        overall: OutcomeMetricsSchema.parse(snapshot.overallMetrics),
        money: MoneyMetricsSchema.parse(snapshot.overallMoney),
        rank: CalibrationSchema.parse(snapshot.rankCalibration),
        confidence: CalibrationSchema.parse(snapshot.confidenceCalibration),
        regimes: segments(snapshot.regimeMetrics, 16),
        assets: [...segments(snapshot.assetMetrics, 64)]
          .sort((a, b) => b.outcomes.resolved - a.outcomes.resolved).slice(0, 24),
        hours: segments(snapshot.hourMetrics, 24),
        strategyRegime: MatrixSchema.parse(snapshot.strategyRegimeMatrix),
        thresholds: ThresholdCandidateSchema.array().parse(snapshot.thresholdCandidates ?? [])
          .slice(0, 8),
        split: TemporalSplitSchema.parse(snapshot.temporalSplit),
        warnings: (Array.isArray(snapshot.warnings) ? snapshot.warnings : [])
          .filter((code): code is string => typeof code === 'string').slice(0, 24)
      } satisfies AnalyticsState
    } catch {
      return offline
    }
  })
  ipcMain.handle(IPC_CHANNELS.getEngineHealth, (event) => { authorize(event); return fetchEngineHealth(connection) })
  ipcMain.handle(IPC_CHANNELS.replay, async (event, input: unknown) => {
    // Read-only Phase 11 research. The two commands here start and cancel *offline compute*
    // over the engine's own recorded file; there is no path from this handler to an order, a
    // broker control, an arm state or the trading day, and no command that applies a finding.
    const command = ReplayCommandSchema.parse(input)
    authorize(event)
    const base = emptyReplayState('ต่อเอ็นจิ้นไม่ได้')
    try {
      if (command.operation === 'start') {
        const body = {
          includeCapitalBear: command.platform === null || command.platform === 'capitalbear',
          includeIqOption: command.platform === null || command.platform === 'iqoption',
          warmupDurationMs: command.warmupMs,
          latencyScenarios: command.latencyScenarios,
          ...(command.windowMs === null ? {} : { fromTime: Date.now() - command.windowMs })
        }
        const started = await fetch(new URL('/api/replay/runs', connection.healthUrl), {
          method: 'POST', headers: { 'content-type': 'application/json' },
          body: JSON.stringify(body), signal: AbortSignal.timeout(20_000), redirect: 'error' })
        if (started.status === 409) return { ...base, available: true, busy: true,
          message: 'มีการจำลองย้อนหลังทำงานอยู่แล้ว — รอให้จบก่อน' } satisfies ReplayState
        if (!started.ok) throw new Error('Replay could not be started')
        activeReplayJob = String(((await started.json()) as Record<string, unknown>).jobId)
      }
      if (command.operation === 'cancel' && activeReplayJob) {
        await fetch(new URL(`/api/replay/runs/${activeReplayJob}/cancel`, connection.healthUrl),
          { method: 'POST', signal: AbortSignal.timeout(10_000), redirect: 'error' })
      }
      if (!activeReplayJob) {
        const listing = await fetch(new URL('/api/replay/runs', connection.healthUrl),
          { signal: AbortSignal.timeout(10_000), redirect: 'error' })
        if (!listing.ok) throw new Error('Replay unavailable')
        const body = await listing.json() as Record<string, unknown>
        const runs = Array.isArray(body.runs) ? body.runs as Record<string, unknown>[] : []
        const latest = runs[0]
        if (!latest) return { ...base, available: true, message: 'ยังไม่เคยรันย้อนหลัง' }
        activeReplayJob = String(latest.replayRunId)
      }
      const status = await fetch(
        new URL(`/api/replay/runs/${activeReplayJob}`, connection.healthUrl),
        { signal: AbortSignal.timeout(10_000), redirect: 'error' })
      if (!status.ok) throw new Error('Replay status unavailable')
      const body = await status.json() as Record<string, unknown>
      const run = (body.run ?? {}) as Record<string, unknown>
      const state: ReplayState = {
        replayVersion: String(body.replayVersion ?? 'unknown'), available: true, busy: false,
        jobId: body.jobId === undefined ? null : String(body.jobId),
        replayRunId: body.replayRunId === null || body.replayRunId === undefined
          ? null : String(body.replayRunId),
        status: ReplayStatusSchema.parse(body.status ?? run.status ?? 'PENDING'),
        phase: String(body.phase ?? 'DONE'),
        totalEvents: Number(body.totalEvents ?? run.totalEvents ?? 0),
        processedEvents: Number(body.processedEvents ?? run.processedEvents ?? 0),
        percent: typeof body.percent === 'number' ? body.percent : null,
        currentMarketTime: typeof body.currentMarketTime === 'number' ? body.currentMarketTime : null,
        error: body.error === null || body.error === undefined ? null : String(body.error),
        summary: null, message: ''
      }
      if (state.status !== 'COMPLETED' || state.replayRunId === null) return state
      const summary = await fetch(
        new URL(`/api/replay/runs/${state.replayRunId}/summary`, connection.healthUrl),
        { signal: AbortSignal.timeout(20_000), redirect: 'error' })
      if (!summary.ok) return state
      const payload = await summary.json() as Record<string, unknown>
      return { ...state, summary: ReplaySummarySchema.parse(payload.summary) } satisfies ReplayState
    } catch {
      return base
    }
  })
  ipcMain.handle(IPC_CHANNELS.openWorkspace, (event, input: unknown) => {
    if (authorize(event).overlay) throw new Error('Overlay cannot open windows')
    workspaceWindows.open(PlatformSchema.parse(input))
  })
  ipcMain.handle(IPC_CHANNELS.openTrading, (event, input: unknown) => {
    if (authorize(event).overlay) throw new Error('Overlay cannot open windows')
    tradingWindows.open(PlatformSchema.parse(input))
  })
  ipcMain.handle(IPC_CHANNELS.platformCommand, async (event, input: unknown) => {
    const command = PlatformCommandSchema.parse(input)
    const scope = authorize(event, command.platform)
    if (scope.overlay && command.operation !== 'state' && command.operation !== 'draft')
      throw new Error('Overlay operation not authorized')
    if (command.operation === 'resolveGrid') return browsers.resolveGrid(command.platform)
    const previous = browsers.observationSurface(command.platform)
    const result = browsers.command(command)
    if (command.operation === 'layout' && previous.gridReady && !browsers.observationSurface(command.platform).gridReady) {
      try { await prepare(command.platform) }
      catch { /* Geometry remains blocked; Sync/Start reports the calibration fallback. */ }
    }
    return result
  })
  ipcMain.handle(IPC_CHANNELS.configuration, async (event, input: unknown) => {
    const request = ConfigurationRequestSchema.parse(input)
    if (authorize(event, request.platform).overlay) throw new Error('Overlay cannot persist configuration')
    const result = await requestConfiguration(connection, request)
    market!.configure(result)
    assetSync!.configure(result)
    execution!.configure(result)
    return result
  })
  ipcMain.handle(IPC_CHANNELS.execution, async (event, input: unknown) => {
    // The only channel that can lead to a press. A calibration overlay sits above the broker
    // page and must never reach it, so it is refused here as well as by its own scope.
    const command = ExecutionCommandSchema.parse(input)
    if (authorize(event, command.platform).overlay) throw new Error('Overlay cannot control execution')
    // Measuring controls needs verified geometry, the same precondition Start observation has, so
    // it resolves the grid itself rather than demanding the operator go and find another button.
    if (command.operation === 'calibrateControls') await prepare(command.platform)
    return execution!.command(command)
  })

  // Phase 9.5 reports; Electron decides. The watcher shows the operator what happened and asks
  // the application to close itself the same way a person closing the window would.
  sessionWatcher = new SessionWatcher(connection, () => app.quit())

  openDashboard()
  app.on('activate', () => {
    if (!dashboardWindow || dashboardWindow.isDestroyed()) openDashboard()
  })
})

app.on('before-quit', () => {
  sessionWatcher?.stop(); execution?.stop(); assetSync?.stop(); market?.stop(); engineProcess?.stop()
})
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
