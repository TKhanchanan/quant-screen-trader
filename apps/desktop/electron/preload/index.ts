import { contextBridge, ipcRenderer } from 'electron'
import {
  IPC_CHANNELS,
  PlatformSchema,
  type DesktopBridge,
  type EngineHealthSnapshot,
  type Platform
} from '@quant-screen-trader/shared-types'

const bridge: DesktopBridge = {
  getEngineHealth: () =>
    ipcRenderer.invoke(IPC_CHANNELS.getEngineHealth) as Promise<EngineHealthSnapshot>,
  openWorkspace: async (platform: Platform) => {
    await ipcRenderer.invoke(IPC_CHANNELS.openWorkspace, PlatformSchema.parse(platform))
  }
}

contextBridge.exposeInMainWorld('quantScreenTrader', bridge)
