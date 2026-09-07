import { describe, expect, it } from 'vitest'
import {
  DEFAULT_ENGINE_HOST,
  DEFAULT_ENGINE_PORT,
  getEngineConnectionConfig
} from '../electron/main/engine-config'

describe('engine connection configuration', () => {
  it('uses safe loopback defaults', () => {
    expect(getEngineConnectionConfig({})).toEqual({
      host: DEFAULT_ENGINE_HOST,
      port: DEFAULT_ENGINE_PORT,
      healthUrl: 'http://127.0.0.1:8765/health'
    })
  })

  it('supports IPv6 loopback URLs', () => {
    expect(
      getEngineConnectionConfig({ QST_ENGINE_HOST: '::1', QST_ENGINE_PORT: '9000' })
    ).toEqual({
      host: '::1',
      port: 9000,
      healthUrl: 'http://[::1]:9000/health'
    })
  })

  it('rejects network-visible hosts and invalid ports', () => {
    expect(
      getEngineConnectionConfig({ QST_ENGINE_HOST: '0.0.0.0', QST_ENGINE_PORT: '70000' })
    ).toEqual({
      host: DEFAULT_ENGINE_HOST,
      port: DEFAULT_ENGINE_PORT,
      healthUrl: 'http://127.0.0.1:8765/health'
    })
  })
})
