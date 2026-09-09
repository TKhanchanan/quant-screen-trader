import { contextBridge, ipcRenderer } from 'electron'
import {
  IPC_CHANNELS, AssetSyncCommandSchema, AssetSyncStateSchema, MarketCommandSchema, MarketSnapshotSchema,
  PlatformSchema,
  PlatformCommandSchema, BrowserSnapshotSchema, ConfigurationRequestSchema, ConfigurationResultSchema,
  FeatureStateSchema,
  type DesktopBridge,
  type EngineHealthSnapshot,
  type Platform
} from '@quant-screen-trader/shared-types'

const bridge: DesktopBridge = {
  assetSync: async (request) => AssetSyncStateSchema.parse(await ipcRenderer.invoke(IPC_CHANNELS.assetSync, AssetSyncCommandSchema.parse(request))),
  market: async (request) => MarketSnapshotSchema.parse(await ipcRenderer.invoke(IPC_CHANNELS.market, MarketCommandSchema.parse(request))),
  platformCommand: async (request) => BrowserSnapshotSchema.parse(await ipcRenderer.invoke(
    IPC_CHANNELS.platformCommand, PlatformCommandSchema.parse(request))),
  configuration: async (request) => ConfigurationResultSchema.parse(await ipcRenderer.invoke(
    IPC_CHANNELS.configuration, ConfigurationRequestSchema.parse(request))),
  features: async (platform) => FeatureStateSchema.parse(
    await ipcRenderer.invoke(IPC_CHANNELS.features, PlatformSchema.parse(platform))),
  getEngineHealth: () =>
    ipcRenderer.invoke(IPC_CHANNELS.getEngineHealth) as Promise<EngineHealthSnapshot>,
  openWorkspace: async (platform: Platform) => {
    await ipcRenderer.invoke(IPC_CHANNELS.openWorkspace, PlatformSchema.parse(platform))
  }
}

contextBridge.exposeInMainWorld('quantScreenTrader', bridge)
