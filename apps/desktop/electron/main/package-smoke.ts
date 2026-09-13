import { existsSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { app, type BrowserWindow } from 'electron'
import type { EngineConnectionConfig } from './engine-config'
import { EngineProcessManager, resolveEngineLaunch } from './engine-process'
import { fetchEngineHealth } from './health-client'
import { TesseractOCRProvider } from './market-ocr'

/** Explicit packaging acceptance switch, using a disposable OS-data root; never opens a broker. */
export async function runPackageSmoke(window: BrowserWindow, connection: EngineConnectionConfig, engine: EngineProcessManager): Promise<void> {
  if (!process.env.QST_PACKAGE_SMOKE_ROOT) throw new Error('Smoke requires a temporary data root')
  console.info('[package-smoke] Waiting for engine and renderer')
  const timeout = setTimeout(() => app.quit(), 60_000)
  const deadline = Date.now() + 40_000
  while ((await fetchEngineHealth(connection)).state !== 'online') {
    if (engine.diagnostic || Date.now() > deadline) throw new Error('Engine smoke readiness failed')
    await new Promise(resolve => setTimeout(resolve, 200))
  }
  console.info('[package-smoke] Engine ready')
  if (window.webContents.isLoading()) await new Promise<void>(resolve => window.webContents.once('did-finish-load', () => resolve()))
  const text: string = await window.webContents.executeJavaScript('document.body.innerText')
  if (!text.trim()) throw new Error('Packaged renderer is blank')
  console.info('[package-smoke] Renderer ready; initializing offline OCR')
  const ocr = new TesseractOCRProvider()
  try {
    await ocr.parseText({ width: 120, height: 40, grayscale: new Uint8Array(4800).fill(255) })
  } finally { await ocr.stop() }
  console.info('[package-smoke] OCR ready')
  const enginePath = resolveEngineLaunch({ appPath: app.getAppPath(), resourcesPath: process.resourcesPath,
    connection, dataDirectory: app.getPath('userData'), isPackaged: true }).command
  writeFileSync(join(app.getPath('userData'), 'package-smoke.json'), JSON.stringify({
    appVersion: app.getVersion(), arch: process.arch, userData: app.getPath('userData'),
    enginePath, enginePid: engine.pid, ocr: 'ok', renderer: 'ok'
  }))
  const poll = setInterval(() => {
    if (existsSync(join(app.getPath('userData'), 'package-smoke.quit'))) {
      clearInterval(poll); clearTimeout(timeout); app.quit()
    }
  }, 200)
}
