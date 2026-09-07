import { existsSync, realpathSync } from 'node:fs'
import { join, resolve, relative, isAbsolute, sep } from 'node:path'
import { app, BrowserWindow, ipcMain, WebContentsView, type IpcMainInvokeEvent } from 'electron'
import {
  IPC_CHANNELS, MarketCommandSchema,
  PlatformSchema,
  PlatformCommandSchema, ConfigurationRequestSchema, PLATFORM_DETAILS,
  type Platform
} from '@quant-screen-trader/shared-types'
import { getEngineConnectionConfig } from './engine-config'
import { EngineProcessManager } from './engine-process'
import { fetchEngineHealth } from './health-client'
import { PlatformWindowRegistry } from './window-registry'
import { PlatformBrowserManager } from './platform-browser'
import { MarketManager } from './market-manager'
import { requestConfiguration } from './configuration-client'
import { requireScope, type RendererScope } from './ipc-scope'

const trustedRenderers = new Map<number, RendererScope>()
function authorize(event: IpcMainInvokeEvent, platform?: Platform): RendererScope {
  return requireScope(trustedRenderers.get(event.sender.id), event.senderFrame === event.sender.mainFrame, platform)
}
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
  ipcMain.handle(IPC_CHANNELS.market, (event, input: unknown) => {
    const command = MarketCommandSchema.parse(input)
    if (authorize(event, command.platform).overlay) throw new Error('Overlay cannot observe')
    return market!.command(command)
  })

  ipcMain.handle(IPC_CHANNELS.getEngineHealth, (event) => { authorize(event); return fetchEngineHealth(connection) })
  ipcMain.handle(IPC_CHANNELS.openWorkspace, (event, input: unknown) => {
    if (authorize(event).overlay) throw new Error('Overlay cannot open windows')
    workspaceWindows.open(PlatformSchema.parse(input))
  })
  ipcMain.handle(IPC_CHANNELS.platformCommand, (event, input: unknown) => {
    const command = PlatformCommandSchema.parse(input)
    const scope = authorize(event, command.platform)
    if (scope.overlay && command.operation !== 'state' && command.operation !== 'draft')
      throw new Error('Overlay operation not authorized')
    return browsers.command(command)
  })
  ipcMain.handle(IPC_CHANNELS.configuration, async (event, input: unknown) => {
    const request = ConfigurationRequestSchema.parse(input)
    if (authorize(event, request.platform).overlay) throw new Error('Overlay cannot persist configuration')
    const result = await requestConfiguration(connection, request)
    market!.configure(result)
    return result
  })

  openDashboard()
  app.on('activate', () => {
    if (!dashboardWindow || dashboardWindow.isDestroyed()) openDashboard()
  })
})

app.on('before-quit', () => { market?.stop(); engineProcess?.stop() })
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
