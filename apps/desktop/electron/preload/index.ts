import { contextBridge, ipcRenderer } from 'electron'
import {
  IPC_CHANNELS, AssetSyncCommandSchema, AssetSyncStateSchema, MarketCommandSchema, MarketSnapshotSchema,
  PlatformSchema,
  PlatformCommandSchema, BrowserSnapshotSchema, ConfigurationRequestSchema, ConfigurationResultSchema,
  FeatureStateSchema, StrategyStateSchema, OpportunityStateSchema, PaperStateSchema,
  ExecutionCommandSchema, ExecutionStateSchema,
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
  strategy: async (platform) => StrategyStateSchema.parse(
    await ipcRenderer.invoke(IPC_CHANNELS.strategy, PlatformSchema.parse(platform))),
  opportunities: async (platform) => OpportunityStateSchema.parse(
    await ipcRenderer.invoke(IPC_CHANNELS.opportunities, PlatformSchema.parse(platform))),
  paper: async (platform) => PaperStateSchema.parse(
    await ipcRenderer.invoke(IPC_CHANNELS.paper, PlatformSchema.parse(platform))),
  execution: async (request) => ExecutionStateSchema.parse(await ipcRenderer.invoke(
    IPC_CHANNELS.execution, ExecutionCommandSchema.parse(request))),
  getEngineHealth: () =>
    ipcRenderer.invoke(IPC_CHANNELS.getEngineHealth) as Promise<EngineHealthSnapshot>,
  openWorkspace: async (platform: Platform) => {
    await ipcRenderer.invoke(IPC_CHANNELS.openWorkspace, PlatformSchema.parse(platform))
  },
  openTrading: async (platform: Platform) => {
    await ipcRenderer.invoke(IPC_CHANNELS.openTrading, PlatformSchema.parse(platform))
  }
}

contextBridge.exposeInMainWorld('quantScreenTrader', bridge)
