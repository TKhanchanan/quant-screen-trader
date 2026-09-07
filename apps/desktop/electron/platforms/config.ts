import { z } from 'zod'
import { PlatformSchema, PLATFORM_DETAILS, type Platform } from '@quant-screen-trader/shared-types'

export const PlatformConfigSchema = z.object({
  id: PlatformSchema, displayName: z.string().min(1), startUrl: z.url(),
  sessionPartition: z.string().startsWith('persist:'), allowedOrigins: z.array(z.url()).min(1)
}).refine((config) => {
  const url = new URL(config.startUrl)
  return config.sessionPartition === `persist:${config.id}-profile` &&
    url.protocol === 'https:' && !url.username && !url.password && !url.search && !url.hash &&
    config.allowedOrigins.includes(url.origin) && config.allowedOrigins.every((origin) => {
      const parsed = new URL(origin)
      return parsed.protocol === 'https:' && parsed.origin === origin
    })
}, 'Only credential-free HTTPS platform URLs are permitted')
export type PlatformConfig = z.infer<typeof PlatformConfigSchema>
const origins: Record<Platform, string[]> = {
  capitalbear: ['https://capitalbear.com', 'https://www.capitalbear.com', 'https://trade.capitalbear.com'],
  iqoption: ['https://iqoption.com', 'https://www.iqoption.com', 'https://login.iqoption.com', 'https://trade.iqoption.com',
    'https://auth.iqoption.com', 'https://api.iqoption.com',
    'https://eu.iqoption.com', 'https://km.iqoption.com', 'https://sc.iqoption.com']
}
export function getPlatformConfig(input: unknown, environment: NodeJS.ProcessEnv = process.env): PlatformConfig {
  const id = PlatformSchema.parse(input)
  const allowedOrigins = origins[id]
  return PlatformConfigSchema.parse({ id, displayName: PLATFORM_DETAILS[id].name,
    startUrl: environment[`QST_${id.toUpperCase()}_START_URL`] ||
      (id === 'capitalbear' ? 'https://trade.capitalbear.com/traderoom' : 'https://iqoption.com/traderoom'),
    sessionPartition: `persist:${id}-profile`, allowedOrigins })
}
export function allowedNavigation(config: PlatformConfig, input: string): boolean {
  try {
    const url = new URL(input)
    return url.protocol === 'https:' && !url.username && !url.password && config.allowedOrigins.includes(url.origin)
  } catch { return false }
}
export function safeOrigin(input: string): string | undefined {
  try { const url = new URL(input); return url.protocol === 'https:' ? url.origin : undefined }
  catch { return undefined }
}
// Google is an authentication destination, never a workspace start URL.
export function allowedLoginNavigation(config: PlatformConfig, input: string): boolean {
  if (allowedNavigation(config, input)) return true
  try {
    const url = new URL(input)
    if (url.origin !== 'https://accounts.google.com' || url.username || url.password) return false
    // Reject an explicit callback to another platform before sending the request.
    return url.searchParams.getAll('redirect_uri').every((uri) => allowedNavigation(config, uri))
  } catch { return false }
}
