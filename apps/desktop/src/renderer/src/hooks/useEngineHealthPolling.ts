import { useEffect } from 'react'
import { useAppStore } from '../state/appStore'

const HEALTH_POLL_INTERVAL_MS = 3_000

export function useEngineHealthPolling(): void {
  const setEngineHealth = useAppStore((state) => state.setEngineHealth)

  useEffect(() => {
    let disposed = false

    const poll = async (): Promise<void> => {
      try {
        const health = await window.quantScreenTrader.getEngineHealth()
        if (!disposed) setEngineHealth(health)
      } catch {
        if (!disposed) {
          setEngineHealth({
            state: 'offline',
            checkedAt: new Date().toISOString(),
            message: 'Desktop health bridge is unavailable.'
          })
        }
      }
    }

    void poll()
    const interval = window.setInterval(() => void poll(), HEALTH_POLL_INTERVAL_MS)
    return () => {
      disposed = true
      window.clearInterval(interval)
    }
  }, [setEngineHealth])
}
