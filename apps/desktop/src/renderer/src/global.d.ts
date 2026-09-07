/// <reference types="vite/client" />

import type { DesktopBridge } from '@quant-screen-trader/shared-types'

declare global {
  interface Window {
    quantScreenTrader: DesktopBridge
  }
}

export {}
