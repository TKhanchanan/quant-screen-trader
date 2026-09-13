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
  log?: (message: string) => void
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
  private stopping = false
  private stopPromise: Promise<void> | null = null
  diagnostic: string | null = null

  get pid(): number | undefined { return this.child?.pid }

  private log(message: string): void {
    console.info(`[quant-engine] ${message}`)
    this.options.log?.(message)
  }

  private unavailable(reason: string): void {
    this.diagnostic = `Quant engine unavailable: ${resolveEngineLaunch(this.options).command} (${reason})`
    this.log(this.diagnostic)
  }

  constructor(private readonly options: EngineProcessOptions) {}

  start(): void {
    if (this.child || this.stopping) return
    this.diagnostic = null

    const launch = resolveEngineLaunch(this.options)
    this.log(`Starting ${launch.command}`)
    if (this.options.isPackaged && !existsSync(launch.command)) {
      this.unavailable('bundled binary missing')
      return
    }
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
      this.unavailable(`spawn failed: ${(error as NodeJS.ErrnoException).code ?? 'unknown'}`)
      return
    }
    this.child = child

    child.stdout?.on('data', (chunk: Buffer) => {
      if (!this.options.isPackaged) console.info(`[quant-engine] ${chunk.toString().trimEnd()}`)
    })
    child.stderr?.on('data', (chunk: Buffer) => {
      if (!this.options.isPackaged) console.warn(`[quant-engine] ${chunk.toString().trimEnd()}`)
    })
    child.once('spawn', () => this.log(`Started pid=${child.pid}`))
    child.once('error', (error: NodeJS.ErrnoException) => {
      this.unavailable(`spawn failed: ${error.code ?? 'unknown'}`)
      if (this.child === child) this.child = null
    })
    child.once('exit', (code, signal) => {
      this.log(`Exited code=${code} signal=${signal}`)
      if (!this.stopping) this.unavailable(`exit code=${code} signal=${signal}`)
      if (this.child === child) this.child = null
    })
  }

  stop(): Promise<void> {
    if (this.stopPromise) return this.stopPromise
    this.stopping = true
    const child = this.child
    if (!child) return Promise.resolve()
    this.stopPromise = new Promise<void>((done) => {
      // onedir engine runs one process, without a reload supervisor or onefile bootloader.
      const killTimer = setTimeout(() => child.kill('SIGKILL'), 5_000)
      const finish = (): void => {
        clearTimeout(killTimer)
        if (this.child === child) this.child = null
        done()
      }
      child.once('close', finish)
      if (child.exitCode !== null || child.signalCode !== null) finish()
      else child.kill()
    })
    return this.stopPromise
  }
}
