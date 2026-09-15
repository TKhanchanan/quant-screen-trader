import { EventEmitter } from 'node:events'
import { resolve } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { EngineProcessManager, resolveEngineLaunch } from '../electron/main/engine-process'

const mocks = vi.hoisted(() => ({ spawn: vi.fn() }))
vi.mock('node:child_process', () => ({ spawn: mocks.spawn }))
const options = {
  appPath: resolve('apps/desktop'), resourcesPath: resolve('fake-resources'),
  dataDirectory: resolve('fake-data'), isPackaged: true,
  connection: { host: '127.0.0.1', port: 8765, healthUrl: 'http://127.0.0.1:8765/health' }
}
afterEach(() => { vi.clearAllMocks(); vi.useRealTimers(); vi.unstubAllEnvs() })
describe('engine package contract', () => {
  it.each([['darwin', 'quant-engine'], ['win32', 'quant-engine.exe']] as const)('resolves %s without a Python fallback', (platform, name) => {
    expect(resolveEngineLaunch({ ...options, platform, environment: { QST_PYTHON_EXECUTABLE: '/ignored/python' } })).toEqual({
      command: resolve(options.resourcesPath, 'quant-engine', name),
      cwd: resolve(options.resourcesPath, 'quant-engine'), args: ['--host', '127.0.0.1', '--port', '8765']
    })
  })
  it('preserves development CLI and explicit Python override', () => {
    const result = resolveEngineLaunch({ ...options, isPackaged: false, environment: { QST_PYTHON_EXECUTABLE: ' /custom/python ' } })
    expect(result.command).toBe('/custom/python')
    expect(result.cwd).toBe(resolve('services/quant-engine'))
    expect(result.args).toEqual(['-m', 'uvicorn', 'quant_engine.main:app', '--app-dir',
      resolve('services/quant-engine/src'), '--host', '127.0.0.1', '--port', '8765'])
    expect(resolveEngineLaunch({ ...options, isPackaged: false, environment: {} }).command).toMatch(/python/)
  })
  it('keeps the process Python override when startup adds only the application version', () => {
    vi.stubEnv('QST_PYTHON_EXECUTABLE', '/process/python')
    expect(resolveEngineLaunch({ ...options, isPackaged: false,
      environment: { QST_APPLICATION_VERSION: '0.1.0' } }).command).toBe('/process/python')
  })
  it('reports a missing packaged binary visibly and never invokes Python', () => {
    const manager = new EngineProcessManager(options)
    manager.start()
    expect(manager.diagnostic).toContain('bundled binary missing')
    expect(manager.diagnostic).toContain(options.resourcesPath)
    expect(mocks.spawn).not.toHaveBeenCalled()
  })
  it('starts once, waits for shutdown, and kills a non-responsive child after a deadline', async () => {
    vi.useFakeTimers()
    const child = Object.assign(new EventEmitter(), { pid: 123, stdout: new EventEmitter(), stderr: new EventEmitter(),
      exitCode: null, signalCode: null, kill: vi.fn() })
    mocks.spawn.mockReturnValue(child)
    const manager = new EngineProcessManager({ ...options, isPackaged: false })
    manager.start(); manager.start()
    expect(mocks.spawn).toHaveBeenCalledTimes(1)
    const stopped = manager.stop()
    expect(manager.stop()).toBe(stopped)
    expect(child.kill).toHaveBeenCalledOnce()
    await vi.advanceTimersByTimeAsync(5_000)
    expect(child.kill).toHaveBeenLastCalledWith('SIGKILL')
    child.emit('exit', 0, null); child.emit('close', 0, null)
    await stopped
    expect(manager.pid).toBeUndefined()
    expect(manager.diagnostic).toBeNull()
  })
  it('reports unexpected exits without including process environment', () => {
    const child = Object.assign(new EventEmitter(), { stdout: null, stderr: null })
    mocks.spawn.mockReturnValue(child)
    const manager = new EngineProcessManager({ ...options, isPackaged: false, environment: { SECRET: 'must-not-log' } })
    manager.start(); child.emit('exit', 7, null)
    expect(manager.diagnostic).toContain('exit code=7')
    expect(manager.diagnostic).not.toContain('must-not-log')
  })
})
