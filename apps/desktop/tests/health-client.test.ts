import { describe, expect, it } from 'vitest'
import {
  fetchEngineHealth,
  mapHealthPayload,
  type HealthFetcher
} from '../electron/main/health-client'
import type { EngineConnectionConfig } from '../electron/main/engine-config'

const payload = {
  type: 'health',
  service: 'quant-engine',
  version: '0.1.0',
  status: 'ok',
  database: 'ok',
  timestamp: '2026-09-07T00:00:00Z',
  sequence: 0
} as const

const connection: EngineConnectionConfig = {
  host: '127.0.0.1',
  port: 8765,
  healthUrl: 'http://127.0.0.1:8765/health'
}

describe('engine health mapping', () => {
  it('maps a healthy engine and database to online', () => {
    const health = mapHealthPayload(payload, '2026-09-07T00:00:01Z', 12)

    expect(health.state).toBe('online')
    expect(health.latencyMs).toBe(12)
    expect(health.engine?.service).toBe('quant-engine')
  })

  it('maps a degraded engine to degraded', () => {
    const health = mapHealthPayload(
      { ...payload, status: 'degraded', database: 'error' },
      '2026-09-07T00:00:01Z',
      5
    )

    expect(health.state).toBe('degraded')
    expect(health.message).toMatch(/degraded/i)
  })

  it('rejects malformed health data without throwing', () => {
    const health = mapHealthPayload({ status: 'ok' }, '2026-09-07T00:00:01Z', 1)

    expect(health.state).toBe('offline')
    expect(health.message).toMatch(/invalid/i)
  })

  it('reports connection failures as offline', async () => {
    const fetcher: HealthFetcher = async () => {
      throw new Error('ECONNREFUSED')
    }

    const health = await fetchEngineHealth(connection, { fetcher, timeoutMs: 20 })

    expect(health.state).toBe('offline')
    expect(health.message).toMatch(/unable to reach/i)
  })
})
