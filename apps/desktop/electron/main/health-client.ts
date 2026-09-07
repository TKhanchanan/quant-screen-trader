import {
  EngineHealthSnapshotSchema,
  QuantEngineHealthPayloadSchema,
  type EngineHealthSnapshot
} from '@quant-screen-trader/shared-types'
import type { EngineConnectionConfig } from './engine-config'

interface HealthResponse {
  ok: boolean
  status: number
  json: () => Promise<unknown>
}

export type HealthFetcher = (
  input: string,
  init: {
    method: 'GET'
    headers: { accept: 'application/json' }
    signal: AbortSignal
  }
) => Promise<HealthResponse>

interface HealthClientOptions {
  fetcher?: HealthFetcher
  timeoutMs?: number
  now?: () => number
}

function offline(checkedAt: string, message: string): EngineHealthSnapshot {
  return EngineHealthSnapshotSchema.parse({
    state: 'offline',
    checkedAt,
    message
  })
}

export function mapHealthPayload(
  input: unknown,
  checkedAt: string,
  latencyMs: number
): EngineHealthSnapshot {
  const result = QuantEngineHealthPayloadSchema.safeParse(input)
  if (!result.success) {
    return offline(checkedAt, 'Quant engine returned an invalid health response.')
  }

  const engine = result.data
  const state = engine.status === 'ok' && engine.database === 'ok' ? 'online' : 'degraded'

  return EngineHealthSnapshotSchema.parse({
    state,
    checkedAt,
    latencyMs,
    engine,
    ...(state === 'degraded'
      ? { message: 'Quant engine reported degraded health.' }
      : {})
  })
}

export async function fetchEngineHealth(
  config: EngineConnectionConfig,
  options: HealthClientOptions = {}
): Promise<EngineHealthSnapshot> {
  const fetcher = options.fetcher ?? (globalThis.fetch as HealthFetcher)
  const timeoutMs = options.timeoutMs ?? 1_500
  const now = options.now ?? Date.now
  const startedAt = now()
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), timeoutMs)

  try {
    const response = await fetcher(config.healthUrl, {
      method: 'GET',
      headers: { accept: 'application/json' },
      signal: controller.signal
    })
    const checkedAt = new Date(now()).toISOString()

    if (!response.ok) {
      return offline(
        checkedAt,
        `Quant engine health check returned HTTP ${response.status}.`
      )
    }

    return mapHealthPayload(await response.json(), checkedAt, Math.max(0, now() - startedAt))
  } catch (error) {
    const checkedAt = new Date(now()).toISOString()
    const reason =
      error instanceof Error && error.name === 'AbortError'
        ? 'Health check timed out.'
        : 'Unable to reach the quant engine.'
    return offline(checkedAt, reason)
  } finally {
    clearTimeout(timeout)
  }
}
