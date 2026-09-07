export const DEFAULT_ENGINE_HOST = '127.0.0.1'
export const DEFAULT_ENGINE_PORT = 8765
const LOOPBACK_HOSTS = new Set([DEFAULT_ENGINE_HOST, 'localhost', '::1'])

export interface EngineConnectionConfig {
  host: string
  port: number
  healthUrl: string
}

function parsePort(value: string | undefined): number {
  const port = Number(value)
  return Number.isInteger(port) && port > 0 && port <= 65_535 ? port : DEFAULT_ENGINE_PORT
}

export function getEngineConnectionConfig(
  environment: NodeJS.ProcessEnv = process.env
): EngineConnectionConfig {
  const requestedHost = environment.QST_ENGINE_HOST?.trim().toLowerCase() || DEFAULT_ENGINE_HOST
  const host = LOOPBACK_HOSTS.has(requestedHost) ? requestedHost : DEFAULT_ENGINE_HOST
  const port = parsePort(environment.QST_ENGINE_PORT)
  const urlHost = host === '::1' ? `[${host}]` : host

  return {
    host,
    port,
    healthUrl: `http://${urlHost}:${port}/health`
  }
}
