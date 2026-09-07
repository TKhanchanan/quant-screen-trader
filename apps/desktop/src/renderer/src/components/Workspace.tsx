import { useEffect, useRef, useState, type JSX } from 'react'
import { defaultCalibration, type Platform } from '@quant-screen-trader/shared-types'
import { PLATFORM_DETAILS } from '../platforms'
import { useAppStore } from '../state/appStore'
import { EngineStatus } from './EngineStatus'
import { useWorkspaceStore } from '../state/workspaceStore'
import { AssetSetup } from './AssetSetup'
import { CalibrationControls } from './CalibrationControls'

interface WorkspaceProps {
  platform: Platform
}

export function Workspace({ platform }: WorkspaceProps): JSX.Element {
  const details = PLATFORM_DETAILS[platform]
  const engineHealth = useAppStore((state) => state.engineHealth)
  const { data, session, busy, error, execute, setSession } = useWorkspaceStore()
  const [mode, setMode] = useState<'browser' | 'assets' | 'calibration'>('browser')
  const [actionError, setActionError] = useState('')
  const region = useRef<HTMLDivElement>(null)
  useEffect(() => {
    void execute({ operation: 'get', platform })
    let disposed = false
    const poll = (): void => { void window.quantScreenTrader.platformCommand({ operation: 'state', platform })
      .then((s) => { if (!disposed) setSession(s.session) }).catch(() => { if (!disposed) setActionError('Platform browser unavailable') }) }
    poll()
    const timer = window.setInterval(poll, 1000)
    return () => { disposed = true; window.clearInterval(timer) }
  }, [platform, execute, setSession])
  useEffect(() => {
    const element = region.current
    if (!element) return
    const layout = (): void => {
      const rect = element.getBoundingClientRect()
      const x = Math.ceil(rect.x), y = Math.ceil(rect.y)
      const width = Math.floor(rect.right) - x, height = Math.floor(rect.bottom) - y
      if (width < 1 || height < 1) return
      void window.quantScreenTrader.platformCommand({ operation: 'layout', platform,
        bounds: { x, y, width, height }, visible: mode !== 'assets' }).catch(() => setActionError('Browser layout unavailable; resize the window to retry.'))
    }
    const observer = new ResizeObserver(layout)
    observer.observe(element); layout()
    window.addEventListener('resize', layout)
    return () => { observer.disconnect(); window.removeEventListener('resize', layout) }
  }, [platform, mode])
  const close = (): void => {
    void window.quantScreenTrader.platformCommand({ operation: 'endCalibration', platform })
      .then(() => setMode('browser')).catch(() => setActionError('Could not close calibration'))
  }
  const calibrate = (): void => {
    if (!data) return
    const profile = data.calibrations.find((p) => p.id === data.activeCalibrationId)
    void window.quantScreenTrader.platformCommand({ operation: 'beginCalibration', platform,
      draft: { assets: data.configuration, slots: profile?.slots ?? defaultCalibration(), zoomFactor: profile?.zoomFactor ?? 1 } })
      .then(() => setMode('calibration')).catch(() => setActionError('Could not open calibration'))
  }
  return <main className="workspace-shell">
    <header className="workspace-toolbar">
      <div className="toolbar"><h1>{details.name}</h1><EngineStatus health={engineHealth} />
        <span>Session: {session?.state ?? 'STARTING'} · Load: {session?.loadState ?? 'idle'}</span>
        <button onClick={() => void window.quantScreenTrader.platformCommand({ operation: 'reload', platform }).catch(() => setActionError('Reload failed'))}>Reload Platform</button>
        <button disabled={!data || busy || mode !== 'browser'} onClick={calibrate}>Calibrate Slots</button>
        <button disabled={!data || busy || mode !== 'browser'} onClick={() => setMode('assets')}>Asset Setup</button>
        <button disabled={busy || mode !== 'browser'} onClick={() => void execute({ operation: 'get', platform })}>Refresh configuration</button>
      </div>
      <p>Login manually in the platform. Login status is unverified; READY is never inferred from page load.</p>
      {session?.errorMessage && <p role="alert" className="error-banner">{session.errorMessage}</p>}
      {(error || actionError) && <p role="alert" className="error-banner">{error || actionError}</p>}
      <div className="slot-summary" aria-label="Nine configured slots">{Array.from({ length: 9 }, (_, i) => {
        const slot = data?.configuration.slots.find((s) => s.id === i + 1)
        return <span key={i} data-slot-id={i + 1}>{i + 1} · {slot?.displayName || slot?.assetName || 'Unassigned'}{slot?.enabled ? '' : ' (off)'}</span>
      })}</div>
      {mode === 'calibration' && <CalibrationControls platform={platform} onClose={close} onError={setActionError} />}
    </header>
    <div ref={region} className="browser-region" aria-label={`${details.name} platform browser`}>
      {mode === 'assets' && data && <AssetSetup platform={platform} initialSlots={data.configuration.slots} onClose={() => setMode('browser')} />}
    </div>
  </main>
}
