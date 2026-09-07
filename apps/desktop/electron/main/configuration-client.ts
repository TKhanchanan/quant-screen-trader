import { ConfigurationRequestSchema, ConfigurationResultSchema,
  type ConfigurationRequest, type ConfigurationResult } from '@quant-screen-trader/shared-types'
import type { EngineConnectionConfig } from './engine-config'

export async function requestConfiguration(connection: EngineConnectionConfig, input: ConfigurationRequest): Promise<ConfigurationResult> {
  const request = ConfigurationRequestSchema.parse(input)
  try {
    const response = await fetch(new URL(`/api/workspaces/${request.platform}/configuration`, connection.healthUrl), {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(request),
      signal: AbortSignal.timeout(5000), redirect: 'error'
    })
    if (!response.ok) throw new Error(`Configuration request failed (${response.status})`)
    const result = ConfigurationResultSchema.parse(await response.json())
    if (result.configuration.platform !== request.platform ||
      [...result.presets, ...result.calibrations].some((r) => r.platform !== request.platform))
      throw new Error('Platform mismatch')
    return result
  } catch { throw new Error('Configuration could not be loaded or saved. Check engine health and retry; your draft is unchanged.') }
}
