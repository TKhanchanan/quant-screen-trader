import type { Platform } from '@quant-screen-trader/shared-types'

export interface RendererScope { platform?: Platform; overlay: boolean }
export function requireScope(scope: RendererScope | undefined, isMainFrame: boolean, platform?: Platform): RendererScope {
  if (!scope || !isMainFrame || (platform && scope.platform !== platform))
    throw new Error('IPC sender not authorized')
  return scope
}
