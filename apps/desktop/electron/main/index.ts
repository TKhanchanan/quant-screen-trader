import { existsSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { app, BrowserWindow, ipcMain } from 'electron'
import {
  IPC_CHANNELS,
  PlatformSchema,
  type Platform
} from '@quant-screen-trader/shared-types'
import { getEngineConnectionConfig } from './engine-config'
import { EngineProcessManager } from './engine-process'
import { fetchEngineHealth } from './health-client'
import { PlatformWindowRegistry } from './window-registry'

let dashboardWindow: BrowserWindow | null = null
let engineProcess: EngineProcessManager | null = null

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
  const displayName = platform === 'capitalbear' ? 'CapitalBear' : 'IQ Option'
  return createWindow(
    `${displayName} Workspace — QuantScreen Trader`,
    { view: 'workspace', platform },
    { width: 1320, height: 900 },
    `persist:${platform}-profile`
  )
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

void app.whenReady().then(() => {
  try {
    if (!app.isPackaged && typeof process.loadEnvFile === 'function') {
      const envFile = resolve(app.getAppPath(), '..', '..', '.env')
      if (existsSync(envFile)) process.loadEnvFile(envFile)
    }
  } catch {
    console.warn('Local environment overrides could not be loaded; using safe defaults.')
  }

  const connection = getEngineConnectionConfig()
  engineProcess = new EngineProcessManager({
    appPath: app.getAppPath(),
    connection,
    dataDirectory: app.getPath('userData'),
    isPackaged: app.isPackaged,
    resourcesPath: process.resourcesPath
  })
  engineProcess.start()

  ipcMain.handle(IPC_CHANNELS.getEngineHealth, () => fetchEngineHealth(connection))
  ipcMain.handle(IPC_CHANNELS.openWorkspace, (_event, input: unknown) => {
    workspaceWindows.open(PlatformSchema.parse(input))
  })

  openDashboard()
  app.on('activate', () => {
    if (!dashboardWindow || dashboardWindow.isDestroyed()) openDashboard()
  })
})

app.on('before-quit', () => engineProcess?.stop())
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
