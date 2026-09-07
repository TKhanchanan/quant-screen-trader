import type { EngineHealthSnapshot } from '@quant-screen-trader/shared-types'
import { create } from 'zustand'

interface AppState {
  engineHealth: EngineHealthSnapshot
  setEngineHealth: (health: EngineHealthSnapshot) => void
}

export const useAppStore = create<AppState>((set) => ({
  engineHealth: {
    state: 'offline',
    checkedAt: new Date(0).toISOString(),
    message: 'Waiting for the quant engine.'
  },
  setEngineHealth: (engineHealth) => set({ engineHealth })
}))
