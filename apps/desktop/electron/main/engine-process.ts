import { existsSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { spawn, type ChildProcess } from 'node:child_process'
import type { EngineConnectionConfig } from './engine-config'

interface EngineProcessOptions {
  appPath: string
  connection: EngineConnectionConfig
  dataDirectory: string
  environment?: NodeJS.ProcessEnv
  isPackaged: boolean
  platform?: NodeJS.Platform
  resourcesPath: string
}

interface EngineLaunch {
  command: string
  args: string[]
  cwd: string
}

export function resolveDevelopmentServiceDirectory(appPath: string): string {
  return resolve(appPath, '..', '..', 'services', 'quant-engine')
}

export function resolvePythonExecutable(
  serviceDirectory: string,
  platform: NodeJS.Platform,
  override?: string
): string {
  if (override?.trim()) return override.trim()

  const repositoryDirectory = resolve(serviceDirectory, '..', '..')
  const virtualEnvironmentExecutable =
    platform === 'win32' ? join('.venv', 'Scripts', 'python.exe') : join('.venv', 'bin', 'python')
  const candidates = [
    resolve(serviceDirectory, virtualEnvironmentExecutable),
    resolve(repositoryDirectory, virtualEnvironmentExecutable)
  ]
  const localInterpreter = candidates.find((candidate) => existsSync(candidate))

  return localInterpreter ?? (platform === 'win32' ? 'python' : 'python3')
}

export function resolveEngineLaunch(options: EngineProcessOptions): EngineLaunch {
  const environment = options.environment ?? process.env
  const platform = options.platform ?? process.platform

  if (options.isPackaged) {
    const executable = platform === 'win32' ? 'quant-engine.exe' : 'quant-engine'
    const serviceDirectory = resolve(options.resourcesPath, 'quant-engine')
    return {
      command: resolve(serviceDirectory, executable),
      args: ['--host', options.connection.host, '--port', String(options.connection.port)],
      cwd: serviceDirectory
    }
  }

  const serviceDirectory = resolveDevelopmentServiceDirectory(options.appPath)
  return {
    command: resolvePythonExecutable(
      serviceDirectory,
      platform,
      environment.QST_PYTHON_EXECUTABLE
    ),
    args: [
      '-m',
      'uvicorn',
      'quant_engine.main:app',
      '--app-dir',
      resolve(serviceDirectory, 'src'),
      '--host',
      options.connection.host,
      '--port',
      String(options.connection.port)
    ],
    cwd: serviceDirectory
  }
}

export class EngineProcessManager {
  private child: ChildProcess | null = null

  constructor(private readonly options: EngineProcessOptions) {}

  start(): void {
    if (this.child && !this.child.killed) return

    const launch = resolveEngineLaunch(this.options)
    let child: ChildProcess
    try {
      child = spawn(launch.command, launch.args, {
        cwd: launch.cwd,
        env: {
          ...process.env,
          ...this.options.environment,
          QST_DATA_DIR: this.options.dataDirectory,
          QST_ENGINE_HOST: this.options.connection.host,
          QST_ENGINE_PORT: String(this.options.connection.port)
        },
        stdio: ['ignore', 'pipe', 'pipe'],
        windowsHide: true
      })
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      console.warn(`[quant-engine] Failed to start: ${message}`)
      return
    }
    this.child = child

    child.stdout?.on('data', (chunk: Buffer) => {
      console.info(`[quant-engine] ${chunk.toString().trimEnd()}`)
    })
    child.stderr?.on('data', (chunk: Buffer) => {
      console.warn(`[quant-engine] ${chunk.toString().trimEnd()}`)
    })
    child.once('error', (error) => {
      console.warn(`[quant-engine] Failed to start: ${error.message}`)
      if (this.child === child) this.child = null
    })
    child.once('exit', (code, signal) => {
      if (code && code !== 0) {
        console.warn(`[quant-engine] Exited with code ${code}${signal ? ` (${signal})` : ''}.`)
      }
      if (this.child === child) this.child = null
    })
  }

  stop(): void {
    const child = this.child
    this.child = null
    if (child && !child.killed) child.kill()
  }
}
