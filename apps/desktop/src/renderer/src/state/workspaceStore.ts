import { create } from 'zustand'
import type { ConfigurationRequest, ConfigurationResult, PlatformSessionState } from '@quant-screen-trader/shared-types'

interface WorkspaceState {
  data: ConfigurationResult | null
  session: PlatformSessionState | null
  busy: boolean
  error: string | null
  setSession: (session: PlatformSessionState) => void
  execute: (request: ConfigurationRequest) => Promise<ConfigurationResult | null>
}
// Each workspace has its own renderer/process and store; SQLite is the durable source.
export const useWorkspaceStore = create<WorkspaceState>((set, get) => ({
  data: null, session: null, busy: false, error: null,
  setSession: (session) => set({ session }),
  execute: async (request) => {
    if (get().busy) return null
    set({ busy: true, error: null })
    try {
      const data = await window.quantScreenTrader.configuration(request)
      set({ data }); return data
    } catch (error) {
      set({ error: error instanceof Error ? error.message : 'Configuration unavailable' }); return null
    } finally { set({ busy: false }) }
  }
}))
