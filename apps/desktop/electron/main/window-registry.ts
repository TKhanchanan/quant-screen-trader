import type { Platform } from '@quant-screen-trader/shared-types'

export interface ManagedWindow {
  focus: () => void
  isDestroyed: () => boolean
  isMinimized?: () => boolean
  once: (event: 'closed', listener: () => void) => unknown
  restore?: () => void
}

export class PlatformWindowRegistry<TWindow extends ManagedWindow> {
  private readonly windows = new Map<Platform, TWindow>()

  constructor(private readonly createWindow: (platform: Platform) => TWindow) {}

  open(platform: Platform): TWindow {
    const existing = this.windows.get(platform)
    if (existing && !existing.isDestroyed()) {
      if (existing.isMinimized?.()) existing.restore?.()
      existing.focus()
      return existing
    }

    const window = this.createWindow(platform)
    this.windows.set(platform, window)
    window.once('closed', () => {
      if (this.windows.get(platform) === window) this.windows.delete(platform)
    })
    return window
  }

  get(platform: Platform): TWindow | undefined {
    return this.windows.get(platform)
  }

  get size(): number {
    return this.windows.size
  }
}
